package com.androsync.remote.ui.pairs

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.androsync.remote.data.SyncLauncher
import com.androsync.remote.data.api.ConflictVersionDto
import com.androsync.remote.data.api.ConflictsCleanResponse
import com.androsync.remote.data.api.PairDto
import com.androsync.remote.data.api.PairRequest
import com.androsync.remote.data.api.RunDto
import com.androsync.remote.data.api.StatusDto
import com.androsync.remote.ui.ConnectionViewModel
import com.androsync.remote.ui.common.ConnectionState
import com.androsync.remote.ui.common.SyncError
import com.androsync.remote.ui.common.mapApiError
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class PairsUiState(
    val loading: Boolean = true,
    val refreshing: Boolean = false,
    val pairs: List<PairDto> = emptyList(),
    /** Set only while there is no reachable [com.androsync.remote.data.api.ApiClient] to load with. */
    val error: String? = null,
    /** `pair name -> count of pending "conflict: ask" entries` from `GET /v1/pending/<pair>` (task 23). */
    val pendingCounts: Map<String, Int> = emptyMap(),
    /** `pair name -> count of saved conflict-copy backups` from `GET /v1/conflicts/<pair>` (task 29c). */
    val conflictCounts: Map<String, Int> = emptyMap(),
)

/** State of the conflicts bottom sheet (task 29c); null means it is closed. */
sealed class ConflictsSheetState {
    data class Loading(val pairName: String) : ConflictsSheetState()
    data class Loaded(
        val pairName: String,
        val versions: List<ConflictVersionDto>,
        /** Set once "Clean duplicates" has fetched its dry-run preview; null before then. */
        val cleanPreview: ConflictsCleanResponse? = null,
        val busy: Boolean = false,
    ) : ConflictsSheetState()
}

/** State of the runs bottom sheet (task 30c); null means it is closed. */
sealed class RunsSheetState {
    data class Loading(val pairName: String) : RunsSheetState()
    data class Loaded(val pairName: String, val runs: List<RunDto>, val busy: Boolean = false) : RunsSheetState()
}

/** One-shot UI effects the screen reacts to (snackbar / navigation), not part of persisted state. */
sealed class PairsEvent {
    data class Error(val error: SyncError) : PairsEvent()
    data class JobStarted(val jobId: String) : PairsEvent()
}

sealed class PreviewState {
    /** [pairName] null means "all pairs" (leftover L4: a "Preview" for "Sync all"). */
    data class Loading(val pairName: String?) : PreviewState()
    data class Loaded(val status: StatusDto) : PreviewState()
    data class LoadedAll(val statuses: List<StatusDto>) : PreviewState()
}

/**
 * Backs [PairsScreen] (PLAN.md task 19): loads `/v1/pairs` on start and on
 * pull-to-refresh, and drives Preview (`/v1/status/<pair>`) and Sync
 * (`POST /v1/run`) for one pair or "Sync all" (`pairs = null`).
 *
 * Reads the active [com.androsync.remote.data.api.ApiClient] from the
 * shared [connectionViewModel] rather than owning one itself, since which
 * transport is reachable can change under it at any time. Starting a job
 * goes through [syncLauncher] instead (PLAN.md task 20: "the tile and
 * widget all call it"), so it also starts the foreground service.
 */
class PairsViewModel(
    private val connectionViewModel: ConnectionViewModel,
    private val syncLauncher: SyncLauncher,
) : ViewModel() {

    val connectionState: StateFlow<ConnectionState> = connectionViewModel.connectionState

    private val _uiState = MutableStateFlow(PairsUiState())
    val uiState: StateFlow<PairsUiState> = _uiState.asStateFlow()

    private val _previewState = MutableStateFlow<PreviewState?>(null)
    val previewState: StateFlow<PreviewState?> = _previewState.asStateFlow()

    private val _conflictsSheetState = MutableStateFlow<ConflictsSheetState?>(null)
    val conflictsSheetState: StateFlow<ConflictsSheetState?> = _conflictsSheetState.asStateFlow()

    private val _runsSheetState = MutableStateFlow<RunsSheetState?>(null)
    val runsSheetState: StateFlow<RunsSheetState?> = _runsSheetState.asStateFlow()

    private val _events = Channel<PairsEvent>(Channel.BUFFERED)
    val events: Flow<PairsEvent> = _events.receiveAsFlow()

    init {
        refresh(initial = true)
        // The very first refresh() commonly races ConnectionViewModel's first probe (its 5s
        // poll loop hasn't returned yet when this ViewModel is constructed), so it snapshots
        // activeApiClient as null and leaves uiState.error stuck on "Laptop not reachable"
        // forever - nothing else re-triggers refresh() once the app *is* reachable. Retry
        // once a client becomes available, but only to recover from that stale error, not to
        // re-fetch an already-successful (possibly legitimately empty) pairs list.
        viewModelScope.launch {
            connectionViewModel.activeApiClient.collect { client ->
                if (client != null && _uiState.value.error != null) {
                    refresh()
                }
            }
        }
    }

    fun refresh(initial: Boolean = false) {
        viewModelScope.launch {
            _uiState.update { if (initial) it.copy(loading = true) else it.copy(refreshing = true) }
            val client = connectionViewModel.activeApiClient.value
            if (client == null) {
                _uiState.update { it.copy(loading = false, refreshing = false, error = "Laptop not reachable") }
                return@launch
            }
            try {
                val pairs = client.pairs()
                _uiState.update { it.copy(loading = false, refreshing = false, pairs = pairs, error = null) }
                // Best-effort: a pending-count fetch failing for one pair should not
                // blank out the ones that succeeded, or the pairs list itself.
                val counts = pairs.mapNotNull { pair ->
                    runCatching { client.pending(pair.name).size }.getOrNull()?.let { pair.name to it }
                }.toMap()
                val conflictCounts = pairs.mapNotNull { pair ->
                    runCatching { client.conflicts(pair.name).size }.getOrNull()?.let { pair.name to it }
                }.toMap()
                _uiState.update { it.copy(pendingCounts = counts, conflictCounts = conflictCounts) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _uiState.update { it.copy(loading = false, refreshing = false) }
                _events.trySend(PairsEvent.Error(mapApiError(e)))
            }
        }
    }

    /** Deletes [name] (task 23c's Pairs-card overflow menu), then refreshes the list. */
    fun deletePair(name: String, purgeState: Boolean) {
        viewModelScope.launch {
            val client = connectionViewModel.activeApiClient.value
            if (client == null) {
                _events.trySend(PairsEvent.Error(SyncError.Connection("Laptop not reachable")))
                return@launch
            }
            try {
                client.deletePair(name, purgeState)
                refresh()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _events.trySend(PairsEvent.Error(mapApiError(e)))
            }
        }
    }

    fun preview(pairName: String) {
        viewModelScope.launch {
            val client = connectionViewModel.activeApiClient.value
            if (client == null) {
                _events.trySend(PairsEvent.Error(SyncError.Connection("Laptop not reachable")))
                return@launch
            }
            _previewState.value = PreviewState.Loading(pairName)
            try {
                val status = client.status(pairName)
                _previewState.value = PreviewState.Loaded(status)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _previewState.value = null
                _events.trySend(PairsEvent.Error(mapApiError(e)))
            }
        }
    }

    /** "Preview" for "Sync all" (leftover L4): one `GET /v1/status/<pair>` per configured pair, in parallel. */
    fun previewAll() {
        viewModelScope.launch {
            val client = connectionViewModel.activeApiClient.value
            if (client == null) {
                _events.trySend(PairsEvent.Error(SyncError.Connection("Laptop not reachable")))
                return@launch
            }
            val pairs = _uiState.value.pairs
            if (pairs.isEmpty()) return@launch
            _previewState.value = PreviewState.Loading(null)
            try {
                // Each pair's status is fetched in its own async child; catch inside the
                // child itself (not just around awaitAll()) - an uncaught exception in one
                // async (e.g. "busy" for a pair mid-sync) otherwise crashes the whole app via
                // structured concurrency's job-cancellation path before the outer try/catch
                // ever sees it. Best-effort: a pair that fails is left out of the preview.
                val statuses = pairs.map { pair ->
                    async {
                        try {
                            client.status(pair.name)
                        } catch (e: CancellationException) {
                            throw e
                        } catch (e: Exception) {
                            null
                        }
                    }
                }.awaitAll().filterNotNull()
                _previewState.value = PreviewState.LoadedAll(statuses)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _previewState.value = null
                _events.trySend(PairsEvent.Error(mapApiError(e)))
            }
        }
    }

    fun dismissPreview() {
        _previewState.value = null
    }

    // -- conflicts bottom sheet (task 29c) -----------------------------------

    fun openConflicts(pairName: String) {
        viewModelScope.launch {
            _conflictsSheetState.value = ConflictsSheetState.Loading(pairName)
            val client = connectionViewModel.activeApiClient.value
            if (client == null) {
                _conflictsSheetState.value = null
                _events.trySend(PairsEvent.Error(SyncError.Connection("Laptop not reachable")))
                return@launch
            }
            try {
                val versions = client.conflicts(pairName)
                _conflictsSheetState.value = ConflictsSheetState.Loaded(pairName, versions)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _conflictsSheetState.value = null
                _events.trySend(PairsEvent.Error(mapApiError(e)))
            }
        }
    }

    fun dismissConflicts() {
        _conflictsSheetState.value = null
    }

    /** Copies a saved version back over the live local file, then reloads the list. */
    fun restoreConflictVersion(rel: String, whenStamp: String) {
        val pairName = (_conflictsSheetState.value as? ConflictsSheetState.Loaded)?.pairName ?: return
        viewModelScope.launch {
            val client = connectionViewModel.activeApiClient.value
            if (client == null) {
                _events.trySend(PairsEvent.Error(SyncError.Connection("Laptop not reachable")))
                return@launch
            }
            try {
                client.restoreConflict(pairName, rel, whenStamp)
                openConflicts(pairName)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _events.trySend(PairsEvent.Error(mapApiError(e)))
            }
        }
    }

    /** Fetches the dry-run preview of cleaning up old `*.conflict-*` duplicates. */
    fun previewCleanDuplicates() {
        val current = _conflictsSheetState.value as? ConflictsSheetState.Loaded ?: return
        viewModelScope.launch {
            _conflictsSheetState.value = current.copy(busy = true)
            val client = connectionViewModel.activeApiClient.value
            if (client == null) {
                _conflictsSheetState.value = current.copy(busy = false)
                _events.trySend(PairsEvent.Error(SyncError.Connection("Laptop not reachable")))
                return@launch
            }
            try {
                val result = client.cleanConflicts(current.pairName, dryRun = true)
                _conflictsSheetState.value = current.copy(cleanPreview = result, busy = false)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _conflictsSheetState.value = current.copy(busy = false)
                _events.trySend(PairsEvent.Error(mapApiError(e)))
            }
        }
    }

    fun cancelCleanPreview() {
        val current = _conflictsSheetState.value as? ConflictsSheetState.Loaded ?: return
        _conflictsSheetState.value = current.copy(cleanPreview = null)
    }

    /** Applies the previewed clean-up, then reloads the list (clean can add new folder backups). */
    fun confirmCleanDuplicates() {
        val current = _conflictsSheetState.value as? ConflictsSheetState.Loaded ?: return
        viewModelScope.launch {
            _conflictsSheetState.value = current.copy(busy = true)
            val client = connectionViewModel.activeApiClient.value
            if (client == null) {
                _conflictsSheetState.value = current.copy(busy = false)
                _events.trySend(PairsEvent.Error(SyncError.Connection("Laptop not reachable")))
                return@launch
            }
            try {
                client.cleanConflicts(current.pairName, dryRun = false)
                openConflicts(current.pairName)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _conflictsSheetState.value = current.copy(busy = false)
                _events.trySend(PairsEvent.Error(mapApiError(e)))
            }
        }
    }

    // -- runs / undo bottom sheet (task 30c) ---------------------------------

    fun openRuns(pairName: String) {
        viewModelScope.launch {
            _runsSheetState.value = RunsSheetState.Loading(pairName)
            val client = connectionViewModel.activeApiClient.value
            if (client == null) {
                _runsSheetState.value = null
                _events.trySend(PairsEvent.Error(SyncError.Connection("Laptop not reachable")))
                return@launch
            }
            try {
                val runs = client.runs(pairName)
                _runsSheetState.value = RunsSheetState.Loaded(pairName, runs)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _runsSheetState.value = null
                _events.trySend(PairsEvent.Error(mapApiError(e)))
            }
        }
    }

    fun dismissRuns() {
        _runsSheetState.value = null
    }

    /** Undoes [runId] for the sheet's pair, closes the sheet, and navigates to the new job. */
    fun undoRun(runId: String, pause: Boolean) {
        val current = _runsSheetState.value as? RunsSheetState.Loaded ?: return
        viewModelScope.launch {
            _runsSheetState.value = current.copy(busy = true)
            val client = connectionViewModel.activeApiClient.value
            if (client == null) {
                _runsSheetState.value = current.copy(busy = false)
                _events.trySend(PairsEvent.Error(SyncError.Connection("Laptop not reachable")))
                return@launch
            }
            try {
                val response = client.undo(current.pairName, runId = runId, pause = pause)
                _runsSheetState.value = null
                _events.trySend(PairsEvent.JobStarted(response.jobId))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _runsSheetState.value = current.copy(busy = false)
                _events.trySend(PairsEvent.Error(mapApiError(e)))
            }
        }
    }

    // -- pause / enable (task 30b) -------------------------------------------

    /** Re-enables a paused pair (the greyed card's Enable switch), then refreshes the list. */
    fun enablePair(pair: PairDto) {
        viewModelScope.launch {
            val client = connectionViewModel.activeApiClient.value
            if (client == null) {
                _events.trySend(PairsEvent.Error(SyncError.Connection("Laptop not reachable")))
                return@launch
            }
            try {
                // A full replacement request (PUT semantics), carrying over every field this
                // screen already knows about the pair - see PairEditorViewModel's own note on
                // why `enabled` specifically must always be sent explicitly.
                client.updatePair(
                    pair.name,
                    PairRequest(
                        name = pair.name,
                        local = pair.local,
                        remote = pair.remote,
                        direction = pair.direction,
                        conflict = pair.conflict,
                        enabled = true,
                    ),
                )
                refresh()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _events.trySend(PairsEvent.Error(mapApiError(e)))
            }
        }
    }

    /** [pairName] null means "Sync all" (`POST /v1/run` with `pairs: null`). */
    fun sync(pairName: String?) {
        viewModelScope.launch {
            try {
                val jobId = syncLauncher.start(pairs = pairName?.let { listOf(it) })
                _events.trySend(PairsEvent.JobStarted(jobId))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _events.trySend(PairsEvent.Error(mapApiError(e)))
            }
        }
    }
}
