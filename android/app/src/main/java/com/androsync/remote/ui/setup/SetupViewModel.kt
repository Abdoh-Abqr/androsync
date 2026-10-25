package com.androsync.remote.ui.setup

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.androsync.remote.data.api.ApiClient
import com.androsync.remote.data.api.ApiException
import com.androsync.remote.data.api.PingDto
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
    val testState: TestConnectionState = TestConnectionState.Idle,
)

/**
 * Backs [SetupScreen] (PLAN.md task 18): preset chips + URL/token fields,
 * QR-scan fill-in, "Test connection" (an ephemeral [ApiClient] independent
 * of the shared connection poll), and "Save" to [SettingsRepository].
 */
class SetupViewModel(
    private val settingsRepository: SettingsRepository,
    private val clientFactory: (baseUrl: String, token: String) -> ApiClient = { url, token ->
        ApiClient(url, token)
    },
) : ViewModel() {

    private val _uiState = MutableStateFlow(SetupUiState())
    val uiState: StateFlow<SetupUiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
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
            if (isTailscaleUrl(current.url)) {
                settingsRepository.setTailscaleUrl(current.url)
                settingsRepository.setBaseUrl("")
                settingsRepository.setPreset(Preset.TAILSCALE)
            } else {
                settingsRepository.setBaseUrl(current.url)
                settingsRepository.setPreset(current.preset)
            }
            settingsRepository.setToken(current.token)
            current.scannedTailscaleUrl?.let { settingsRepository.setTailscaleUrl(it) }
            onSaved()
        }
    }

    private fun errorMessage(e: Exception): String =
        if (e is ApiException) snackbarMessageFor(mapApiError(e)) else (e.message ?: "Connection failed")
}
