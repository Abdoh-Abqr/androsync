package com.androsync.remote.ui.setup

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.androsync.remote.data.api.ApiClient
import com.androsync.remote.data.api.ApiException
import com.androsync.remote.data.api.PingDto
import com.androsync.remote.data.settings.LaptopProfile
import com.androsync.remote.data.settings.LaptopProfiles
import com.androsync.remote.data.settings.Preset
import com.androsync.remote.data.settings.SettingsRepository
import com.androsync.remote.ui.common.classifyQrUrls
import com.androsync.remote.ui.common.isTailscaleUrl
import com.androsync.remote.ui.common.mapApiError
import com.androsync.remote.ui.common.parseQrPayload
import com.androsync.remote.ui.common.pickUrlForPreset
import com.androsync.remote.ui.common.snackbarMessageFor
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

data class SetupUiState(
    val url: String = ApiClient.PRESET_USB_URL,
    val token: String = "",
    val preset: Preset = Preset.USB,
    /** From the last QR scan: saved with [SetupViewModel.save] so the Tailscale URL need not be typed (task 37c). */
    val scannedTailscaleUrl: String? = null,
    /** The laptop's own name from the QR code (task 33c), used to name the saved laptop. */
    val scannedName: String? = null,
    val testState: TestConnectionState = TestConnectionState.Idle,
)

/**
 * Backs [SetupScreen] (PLAN.md task 18): preset chips + URL/token fields,
 * QR-scan fill-in, "Test connection" (an ephemeral [ApiClient] independent
 * of the shared connection poll), and "Save" to [SettingsRepository].
 */
class SetupViewModel(
    private val settingsRepository: SettingsRepository,
    /** True for "add another laptop": start blank and save as a new laptop instead of editing the current one. */
    private val addNew: Boolean = false,
    private val clientFactory: (baseUrl: String, token: String) -> ApiClient = { url, token ->
        ApiClient(url, token)
    },
) : ViewModel() {

    private val _uiState = MutableStateFlow(SetupUiState())
    val uiState: StateFlow<SetupUiState> = _uiState.asStateFlow()

    init {
        if (addNew) {
            _uiState.update { it.copy(url = "", token = "") }
        } else viewModelScope.launch {
            val savedPreset = settingsRepository.preset.first()
            val savedUrl = settingsRepository.baseUrl.first()
            val savedToken = settingsRepository.token.first()
            _uiState.update {
                it.copy(
                    preset = savedPreset,
                    url = savedUrl.ifBlank { it.url },
                    token = savedToken,
                )
            }
        }
    }

    fun onUrlChange(url: String) {
        _uiState.update { it.copy(url = url, testState = TestConnectionState.Idle) }
    }

    fun onTokenChange(token: String) {
        _uiState.update { it.copy(token = token, testState = TestConnectionState.Idle) }
    }

    fun onPresetSelected(preset: Preset) {
        val fixed = Preset.fixedUrlFor(preset)
        _uiState.update {
            it.copy(preset = preset, url = fixed ?: it.url, testState = TestConnectionState.Idle)
        }
    }

    /** Called with the raw text of a scanned QR code (PLAN.md: `androsync qr`'s `{"urls": [...], "token": "..."}`). */
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
                // The Tailscale address goes to its own field on save; only a LAN address replaces the URL field.
                url = addresses.lan ?: (if (addresses.tailscale == null) pickUrlForPreset(payload.urls, it.preset) else null) ?: it.url,
                scannedTailscaleUrl = addresses.tailscale ?: it.scannedTailscaleUrl,
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
        if (current.url.isBlank() || current.token.isBlank()) {
            _uiState.update { it.copy(testState = TestConnectionState.Error("Enter a URL and token first")) }
            return
        }
        viewModelScope.launch {
            _uiState.update { it.copy(testState = TestConnectionState.Loading) }
            try {
                val ping = clientFactory(current.url, current.token).ping()
                _uiState.update { it.copy(testState = TestConnectionState.Success(ping)) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _uiState.update { it.copy(testState = TestConnectionState.Error(errorMessage(e))) }
            }
        }
    }

    /** Persists the form and calls [onSaved] (the caller navigates to `pairs`). */
    fun save(onSaved: () -> Unit) {
        val current = _uiState.value
        if (current.url.isBlank() || current.token.isBlank()) {
            _uiState.update { it.copy(testState = TestConnectionState.Error("Enter a URL and token first")) }
            return
        }
        viewModelScope.launch {
            // A Tailscale address belongs in the Tailscale field, not the LAN one (it was saved there before, and the
            // app then called a working Tailscale connection "LAN").
            val typedIsTailscale = isTailscaleUrl(current.url)
            val tailscale = current.scannedTailscaleUrl ?: if (typedIsTailscale) current.url else ""
            val lan = if (typedIsTailscale) "" else current.url
            val existing = settingsRepository.laptops.first()
            val selectedId = settingsRepository.selectedLaptopId.first()
            // "Add another laptop" makes a new entry; the normal form edits the laptop that is selected now.
            val editing = if (addNew) null else existing.firstOrNull { it.id == selectedId }
            val laptop = LaptopProfile(
                id = editing?.id ?: if (existing.isEmpty()) LaptopProfiles.MIGRATED_ID else LaptopProfiles.newId(),
                name = current.scannedName ?: editing?.name ?: LaptopProfiles.displayName(null, existing),
                baseUrl = lan,
                tailscaleUrl = tailscale.ifEmpty { editing?.tailscaleUrl.orEmpty() },
                token = current.token,
            )
            settingsRepository.saveLaptop(laptop, select = true)
            settingsRepository.setPreset(if (typedIsTailscale) Preset.TAILSCALE else current.preset)
            onSaved()
        }
    }

    private fun errorMessage(e: Exception): String =
        if (e is ApiException) snackbarMessageFor(mapApiError(e)) else (e.message ?: "Connection failed")
}
