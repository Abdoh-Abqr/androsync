package com.androsync.remote.ui.setup

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.androsync.remote.data.api.ConnectionProbe
import com.androsync.remote.data.api.PingDto
import com.androsync.remote.data.api.ProbeOutcome
import com.androsync.remote.data.settings.LaptopProfile
import com.androsync.remote.data.settings.LaptopProfiles
import com.androsync.remote.data.settings.Preset
import com.androsync.remote.data.settings.SettingsRepository
import com.androsync.remote.ui.common.classifyQrUrls
import com.androsync.remote.ui.common.normalizeAddresses
import com.androsync.remote.ui.common.parseQrPayload
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** State of the "Test connection" button (PLAN.md task 18). */
sealed class TestConnectionState {
    data object Idle : TestConnectionState()
    data object Loading : TestConnectionState()
    data class Success(val ping: PingDto) : TestConnectionState()
    data class Error(val message: String) : TestConnectionState()
}

/**
 * The Setup form. Two clearly named addresses instead of one "URL": [tailscaleUrl] (works from anywhere Tailscale is on)
 * and [lanUrl] (same Wi-Fi, optional). The USB address needs no field: it is fixed and always tried.
 */
data class SetupUiState(
    val tailscaleUrl: String = "",
    val lanUrl: String = "",
    val token: String = "",
    /** The laptop's own name from the QR code (task 33c), used to name the saved laptop. */
    val scannedName: String? = null,
    val testState: TestConnectionState = TestConnectionState.Idle,
)

/**
 * Backs [SetupScreen] (PLAN.md task 18): the address and token fields, QR-scan fill-in, "Test connection" (tries USB, then
 * the Tailscale address, then the LAN address, like the rest of the app) and "Save" as a laptop in [SettingsRepository].
 */
class SetupViewModel(
    private val settingsRepository: SettingsRepository,
    /** True for "add another laptop": start blank and save as a new laptop instead of editing the current one. */
    private val addNew: Boolean = false,
    private val connectionProbe: ConnectionProbe = ConnectionProbe(),
) : ViewModel() {

    private val _uiState = MutableStateFlow(SetupUiState())
    val uiState: StateFlow<SetupUiState> = _uiState.asStateFlow()

    init {
        if (!addNew) {
            viewModelScope.launch {
                val (tailscale, lan) = normalizeAddresses(
                    settingsRepository.tailscaleUrl.first(),
                    settingsRepository.baseUrl.first(),
                )
                val token = settingsRepository.token.first()
                _uiState.update { it.copy(tailscaleUrl = tailscale, lanUrl = lan, token = token) }
            }
        }
    }

    fun onTailscaleUrlChange(url: String) {
        _uiState.update { it.copy(tailscaleUrl = url, testState = TestConnectionState.Idle) }
    }

    fun onLanUrlChange(url: String) {
        _uiState.update { it.copy(lanUrl = url, testState = TestConnectionState.Idle) }
    }

    fun onTokenChange(token: String) {
        _uiState.update { it.copy(token = token, testState = TestConnectionState.Idle) }
    }

    /** Called with the raw text of a scanned QR code: `{"name": ..., "urls": [...], "token": ...}`. */
    fun onQrScanned(raw: String) {
        val payload = parseQrPayload(raw)
        if (payload == null) {
            _uiState.update {
                it.copy(testState = TestConnectionState.Error("QR code is not an androsync pairing code"))
            }
            return
        }
        val addresses = classifyQrUrls(payload.urls)
        _uiState.update {
            it.copy(
                token = payload.token,
                tailscaleUrl = addresses.tailscale ?: it.tailscaleUrl,
                lanUrl = addresses.lan ?: it.lanUrl,
                scannedName = payload.name?.takeIf { n -> n.isNotBlank() } ?: it.scannedName,
                testState = TestConnectionState.Idle,
            )
        }
    }

    fun onQrScanFailed(message: String) {
        _uiState.update { it.copy(testState = TestConnectionState.Error(message)) }
    }

    fun testConnection() {
        val current = _uiState.value
        if (current.token.isBlank()) {
            _uiState.update { it.copy(testState = TestConnectionState.Error("Scan the QR code or enter the token first")) }
            return
        }
        val (tailscale, lan) = normalizeAddresses(current.tailscaleUrl, current.lanUrl)
        viewModelScope.launch {
            _uiState.update { it.copy(testState = TestConnectionState.Loading) }
            try {
                val outcome = connectionProbe.probe(current.token, lan.ifEmpty { null }, tailscale)
                _uiState.update {
                    it.copy(
                        testState = when (outcome) {
                            is ProbeOutcome.Reachable -> TestConnectionState.Success(outcome.result.ping)
                            is ProbeOutcome.Unreachable -> TestConnectionState.Error(outcome.lastError)
                        },
                    )
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _uiState.update { it.copy(testState = TestConnectionState.Error(e.message ?: "Connection failed")) }
            }
        }
    }

    /** Saves the form as a laptop and selects it, then calls [onSaved] (the caller navigates to `pairs`). */
    fun save(onSaved: () -> Unit) {
        val current = _uiState.value
        if (current.token.isBlank()) {
            _uiState.update { it.copy(testState = TestConnectionState.Error("Scan the QR code or enter the token first")) }
            return
        }
        val (tailscale, lan) = normalizeAddresses(current.tailscaleUrl, current.lanUrl)
        viewModelScope.launch {
            val existing = settingsRepository.laptops.first()
            val selectedId = settingsRepository.selectedLaptopId.first()
            // "Add another laptop" makes a new entry; the normal form edits the laptop that is selected now.
            val editing = if (addNew) null else existing.firstOrNull { it.id == selectedId }
            val laptop = LaptopProfile(
                id = editing?.id ?: if (existing.isEmpty()) LaptopProfiles.MIGRATED_ID else LaptopProfiles.newId(),
                name = current.scannedName ?: editing?.name ?: LaptopProfiles.displayName(null, existing),
                baseUrl = lan,
                tailscaleUrl = tailscale,
                token = current.token,
            )
            settingsRepository.saveLaptop(laptop, select = true)
            settingsRepository.setPreset(if (tailscale.isNotEmpty()) Preset.TAILSCALE else Preset.USB)
            onSaved()
        }
    }
}
