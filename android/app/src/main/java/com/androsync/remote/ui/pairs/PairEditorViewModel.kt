package com.androsync.remote.ui.pairs

import androidx.lifecycle.ViewModel
import com.androsync.remote.Distribution
import androidx.lifecycle.viewModelScope
import com.androsync.remote.data.PhonePath
import com.androsync.remote.data.SafPath
import com.androsync.remote.data.api.ApiException
import com.androsync.remote.data.api.BrowseDto
import com.androsync.remote.data.api.PairRequest
import com.androsync.remote.ui.ConnectionViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** `direction` values as sent to the API, matching `laptop/androsync/config.py`'s DIRECTIONS. */
object Direction {
    const val LAPTOP_TO_PHONE = "laptop-to-phone"
    const val PHONE_TO_LAPTOP = "phone-to-laptop"
    const val TWO_WAY = "two-way"
    val ALL = listOf(LAPTOP_TO_PHONE, TWO_WAY, PHONE_TO_LAPTOP)
}

/** `conflict` values as sent to the API, matching `laptop/androsync/config.py`'s CONFLICT_POLICIES. */
object ConflictPolicy {
    const val NEWEST_WINS = "newest-wins"
    const val LOCAL_WINS = "local-wins"
    const val REMOTE_WINS = "remote-wins"
    const val ASK = "ask"
    val ALL = listOf(NEWEST_WINS, LOCAL_WINS, REMOTE_WINS, ASK)
}

/** The sensible default for a new pair's `propagate_deletes`: off for a phone-to-laptop backup, on otherwise. */
fun defaultPropagateDeletes(direction: String): Boolean = direction != Direction.PHONE_TO_LAPTOP

/** Pure field validation shared by the "Save" button and [PairEditorViewModelTest]. Null means valid. */
fun validatePairForm(name: String, local: String, remote: String): String? = when {
    name.isBlank() -> "Name is required"
    local.isBlank() -> "Laptop folder is required"
    remote.isBlank() -> "Phone folder is required"
    else -> PhonePath.validate(PhonePath.normalize(remote))
}

data class PairEditorUiState(
    val isNew: Boolean = true,
    val loading: Boolean = false,
    val name: String = "",
    val local: String = "",
    val remote: String = "",
    val direction: String = Direction.TWO_WAY,
    /** Null means "use the daemon's default" (newest-wins / on). */
    val conflict: String? = null,
    val propagateDeletes: Boolean? = null,
    val ignore: String = "",
    /**
     * Carried through unchanged from the loaded pair (task 30b `--pause`) - this form has no
     * control for it, but `save()` must still send it back explicitly: `PUT /v1/pairs/<name>`
     * replaces the whole record, so silently omitting it would un-pause a paused pair on every
     * unrelated edit.
     */
    val enabled: Boolean = true,
    /**
     * "adb" or "agent" (task 37d). Carried through even in the `play` build, which has no control for it: the PUT
     * replaces the whole record, so dropping it would silently turn an agent pair back into an adb pair.
     */
    val transport: String = "adb",
    /** "any" or the id of the phone this pair belongs to (task 33a). Carried through like [transport]. */
    val device: String = "any",
    /** Sync by itself when files change (Phase T2). */
    val auto: Boolean = false,
    val saving: Boolean = false,
    val formError: String? = null,
    val browse: BrowseUiState? = null,
    /** Folders as loaded, to tell when an edit moves the pair to other folders (task 36d, issue #9). */
    val originalLocal: String? = null,
    val originalRemote: String? = null,
) {
    /**
     * True when editing an existing pair and its laptop or phone folder differs from what was loaded: the laptop
     * then forgets the pair's history, so the next sync only adds files and deletes nothing.
     */
    val foldersChanged: Boolean
        get() = !isNew && originalLocal != null && originalRemote != null &&
            (local.trimEnd('/') != originalLocal.trimEnd('/') ||
                PhonePath.normalize(remote).trimEnd('/') != PhonePath.normalize(originalRemote).trimEnd('/'))
}

/** State of the folder-picker bottom sheet (null in [PairEditorUiState.browse] means it is closed). */
data class BrowseUiState(
    val side: String,
    val loading: Boolean = true,
    val result: BrowseDto? = null,
    val error: String? = null,
)

/** One-shot effects [PairEditorScreen] reacts to. */
sealed class PairEditorEvent {
    data object Saved : PairEditorEvent()
    data object Deleted : PairEditorEvent()
    data class Error(val message: String) : PairEditorEvent()
}

/**
 * Backs [PairEditorScreen] (PLAN.md task 23c): add/edit a sync pair, with a
 * folder-picker bottom sheet backed by `GET /v1/browse` for both sides.
 *
 * [pairName] null means "new pair"; otherwise the screen loads the existing
 * pair from `GET /v1/pairs` (same re-fetch-by-id pattern as [com.androsync.remote.ui.job.JobViewModel],
 * rather than trusting a value passed through navigation) and edits it.
 */
class PairEditorViewModel(
    private val pairName: String?,
    private val connectionViewModel: ConnectionViewModel,
) : ViewModel() {

    private val _uiState = MutableStateFlow(PairEditorUiState(isNew = pairName == null))
    val uiState: StateFlow<PairEditorUiState> = _uiState.asStateFlow()

    private val _events = Channel<PairEditorEvent>(Channel.BUFFERED)
    val events: Flow<PairEditorEvent> = _events.receiveAsFlow()

    init {
        if (pairName != null) {
            loadExisting(pairName)
        } else if (Distribution.HAS_AGENT) {
            _uiState.update { it.copy(transport = suggestTransport(connectionViewModel.connectionState.value)) }
        }
    }

    private fun loadExisting(name: String) {
        viewModelScope.launch {
            _uiState.update { it.copy(loading = true) }
            val client = connectionViewModel.activeApiClient.value
            if (client == null) {
                _events.trySend(PairEditorEvent.Error("Laptop not reachable"))
                _uiState.update { it.copy(loading = false) }
                return@launch
            }
            try {
                val pair = client.pairs().firstOrNull { it.name == name }
                if (pair == null) {
                    _events.trySend(PairEditorEvent.Error("Pair not found"))
                } else {
                    _uiState.update {
                        it.copy(
                            loading = false,
                            name = pair.name,
                            local = pair.local,
                            remote = pair.remote,
                            direction = pair.direction,
                            conflict = pair.conflict.takeIf { c -> c != ConflictPolicy.NEWEST_WINS },
                            enabled = pair.enabled,
                            transport = pair.transport,
                            device = pair.device,
                            auto = pair.auto,
                            originalLocal = pair.local,
                            originalRemote = pair.remote,
                        )
                    }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _events.trySend(PairEditorEvent.Error(e.message ?: "Failed to load pair"))
            } finally {
                _uiState.update { it.copy(loading = false) }
            }
        }
    }

    fun onNameChange(value: String) = _uiState.update { it.copy(name = value, formError = null) }
    fun onLocalChange(value: String) = _uiState.update { it.copy(local = value, formError = null) }
    fun onRemoteChange(value: String) = _uiState.update { it.copy(remote = value, formError = null) }
    fun onIgnoreChange(value: String) = _uiState.update { it.copy(ignore = value) }

    /**
     * Handles the result of the native "Pick on phone" folder picker
     * (`ActivityResultContracts.OpenDocumentTree`, PLAN.md task 23c). [treeUri]
     * is the tree URI's string form; null means the user cancelled the
     * picker, in which case nothing changes.
     */
    fun onRemoteFolderPicked(treeUri: String?) {
        if (treeUri == null) return
        val path = SafPath.treeUriToPath(treeUri)
        if (path == null) {
            _uiState.update { it.copy(formError = "Pick a folder on internal storage or SD card") }
        } else {
            _uiState.update { it.copy(remote = path, formError = null) }
        }
    }
    fun onConflictChange(value: String?) = _uiState.update { it.copy(conflict = value) }
    fun onPropagateDeletesChange(value: Boolean?) = _uiState.update { it.copy(propagateDeletes = value) }

    fun onDirectionChange(value: String) = _uiState.update { it.copy(direction = value) }

    fun onTransportChange(value: String) = _uiState.update { it.copy(transport = value) }

    fun onDeviceChange(value: String) = _uiState.update { it.copy(device = value) }

    fun onAutoChange(value: Boolean) = _uiState.update { it.copy(auto = value) }

    fun save() {
        val state = _uiState.value
        val error = validatePairForm(state.name, state.local, state.remote)
        if (error != null) {
            _uiState.update { it.copy(formError = error) }
            return
        }
        viewModelScope.launch {
            val client = connectionViewModel.activeApiClient.value
            if (client == null) {
                _events.trySend(PairEditorEvent.Error("Laptop not reachable"))
                return@launch
            }
            _uiState.update { it.copy(saving = true, formError = null) }
            val ignoreList = state.ignore.split(",").map { it.trim() }.filter { it.isNotEmpty() }
            // Resolve "untouched" (null) to the smart default for this direction before
            // sending: the daemon's own schema default is always true regardless of
            // direction, so leaving this null would silently drop the off-by-default
            // for phone-to-laptop backups the UI is showing the user right now.
            val effectivePropagateDeletes = state.propagateDeletes ?: defaultPropagateDeletes(state.direction)
            val request = PairRequest(
                name = state.name,
                local = state.local,
                remote = PhonePath.normalize(state.remote),
                direction = state.direction,
                conflict = state.conflict,
                propagateDeletes = effectivePropagateDeletes.takeIf { it != true },
                ignore = ignoreList.ifEmpty { null },
                enabled = state.enabled,
                transport = state.transport.takeIf { it != "adb" },
                device = state.device.takeIf { it != ANY_PHONE },
                auto = state.auto.takeIf { it },
            )
            try {
                if (state.isNew) {
                    client.createPair(request)
                } else {
                    client.updatePair(pairName!!, request)
                }
                _events.trySend(PairEditorEvent.Saved)
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiException) {
                if (e.code == 400) {
                    _uiState.update { it.copy(formError = e.message) }
                } else {
                    _events.trySend(PairEditorEvent.Error(e.message))
                }
            } catch (e: Exception) {
                _events.trySend(PairEditorEvent.Error(e.message ?: "Save failed"))
            } finally {
                _uiState.update { it.copy(saving = false) }
            }
        }
    }

    fun delete(purgeState: Boolean) {
        val name = pairName ?: return
        viewModelScope.launch {
            val client = connectionViewModel.activeApiClient.value
            if (client == null) {
                _events.trySend(PairEditorEvent.Error("Laptop not reachable"))
                return@launch
            }
            try {
                client.deletePair(name, purgeState)
                _events.trySend(PairEditorEvent.Deleted)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _events.trySend(PairEditorEvent.Error(e.message ?: "Delete failed"))
            }
        }
    }

    // -- folder browser bottom sheet -----------------------------------------

    fun openBrowser(side: String) {
        val current = _uiState.value
        val startPath = when (side) {
            "local" -> current.local
            // An invalid typed folder would only make the phone browser answer "forbidden"; start at /sdcard instead.
            else -> PhonePath.normalize(current.remote).takeIf { PhonePath.validate(it) == null }.orEmpty()
        }
        _uiState.update { it.copy(browse = BrowseUiState(side = side, loading = true)) }
        browseTo(side, if (side == "remote" && startPath.isBlank()) "" else startPath)
    }

    fun closeBrowser() = _uiState.update { it.copy(browse = null) }

    fun browseUp() {
        val parent = _uiState.value.browse?.result?.parent ?: return
        val side = _uiState.value.browse?.side ?: return
        browseTo(side, parent)
    }

    fun browseInto(path: String) {
        val side = _uiState.value.browse?.side ?: return
        browseTo(side, path)
    }

    fun browseChooseCurrent() {
        val browse = _uiState.value.browse ?: return
        val path = browse.result?.path ?: return
        _uiState.update {
            when (browse.side) {
                "local" -> it.copy(local = path, browse = null, formError = null)
                else -> it.copy(remote = path, browse = null, formError = null)
            }
        }
    }

    fun browseMkdir(folderName: String) {
        val browse = _uiState.value.browse ?: return
        val base = browse.result?.path ?: return
        val newPath = if (browse.side == "remote") "${base.trimEnd('/')}/$folderName" else "$base/$folderName"
        viewModelScope.launch {
            val client = connectionViewModel.activeApiClient.value ?: return@launch
            try {
                client.mkdir(browse.side, newPath)
                browseTo(browse.side, newPath)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _uiState.update { it.copy(browse = it.browse?.copy(error = browseErrorMessage(e))) }
            }
        }
    }

    private fun browseTo(side: String, path: String) {
        viewModelScope.launch {
            _uiState.update { it.copy(browse = it.browse?.copy(side = side, loading = true, error = null)) }
            val client = connectionViewModel.activeApiClient.value
            if (client == null) {
                _uiState.update { it.copy(browse = it.browse?.copy(loading = false, error = "Laptop not reachable")) }
                return@launch
            }
            try {
                val result = client.browse(side, path)
                _uiState.update { it.copy(browse = it.browse?.copy(loading = false, result = result, error = null)) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _uiState.update { it.copy(browse = it.browse?.copy(loading = false, error = browseErrorMessage(e))) }
            }
        }
    }

    private fun browseErrorMessage(e: Exception): String = when {
        e is ApiException && e.code == 503 -> "Connect the phone by USB first"
        e is ApiException && e.code == 403 -> "That folder is outside the allowed locations"
        e is ApiException -> e.message
        else -> e.message ?: "Could not list that folder"
    }
}
