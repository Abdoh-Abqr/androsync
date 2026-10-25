package com.androsync.remote.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.androsync.remote.data.api.ApiClient
import com.androsync.remote.data.api.ConnectionProbe
import com.androsync.remote.data.api.ProbeOutcome
import com.androsync.remote.data.settings.DEFAULT_AUTO_REFRESH_SECONDS
import com.androsync.remote.data.settings.LaptopProfile
import com.androsync.remote.data.settings.SettingsRepository
import com.androsync.remote.ui.setup.TestConnectionState
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** Poll intervals offered by the screen (PLAN.md task 22). */
val POLL_INTERVAL_CHOICES_SECONDS = listOf(5, 10, 30)

data class SettingsUiState(
    val tailscaleUrl: String = "",
    val lanUrl: String = "",
    val token: String = "",
    val tokenRevealed: Boolean = false,
    val pollIntervalSeconds: Int = DEFAULT_AUTO_REFRESH_SECONDS,
    val testState: TestConnectionState = TestConnectionState.Idle,
    val setupHintsEnabled: Boolean = true,
    /** Result line of "Generate a new token" (task 33c follow-up), shown under the button. */
    val tokenMessage: String? = null,
    val laptops: List<LaptopProfile> = emptyList(),
    val selectedLaptopId: String? = null,
)

/**
 * Backs [SettingsScreen] (PLAN.md task 22): editable Tailscale/LAN URLs and
 * token (persisted on every keystroke, like [com.androsync.remote.ui.setup.SetupViewModel]
 * does for the initial form), the poll interval, "Test connection" against
 * whichever preset is currently active, and "Forget setup".
 */
class SettingsViewModel(
    private val settingsRepository: SettingsRepository,
    private val connectionProbe: ConnectionProbe,
) : ViewModel() {

    private val _uiState = MutableStateFlow(SettingsUiState())
    val uiState: StateFlow<SettingsUiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch { loadFields() }
        viewModelScope.launch { settingsRepository.laptops.collect { list -> _uiState.update { it.copy(laptops = list) } } }
        viewModelScope.launch {
            settingsRepository.selectedLaptopId.collect { id -> _uiState.update { it.copy(selectedLaptopId = id) } }
        }
    }

    /** Switch laptop (task 33c): the saved fields change, so the form is reloaded from them. */
    fun selectLaptop(id: String, onChanged: () -> Unit = {}) {
        viewModelScope.launch {
            settingsRepository.selectLaptop(id)
            loadFields()
            onChanged()
        }
    }

    fun removeLaptop(id: String, onChanged: () -> Unit = {}) {
        viewModelScope.launch {
            settingsRepository.removeLaptop(id)
            loadFields()
            onChanged()
        }
    }

    private suspend fun loadFields() {
        run {
            _uiState.update {
                it.copy(
                    tailscaleUrl = settingsRepository.tailscaleUrl.first(),
                    lanUrl = settingsRepository.baseUrl.first(),
                    token = settingsRepository.token.first(),
                    pollIntervalSeconds = settingsRepository.autoRefreshSeconds.first(),
                    setupHintsEnabled = settingsRepository.setupHintsEnabled.first(),
                )
            }
        }
    }

    fun onTailscaleUrlChange(url: String) {
        _uiState.update { it.copy(tailscaleUrl = url, testState = TestConnectionState.Idle) }
        viewModelScope.launch { settingsRepository.setTailscaleUrl(url) }
    }

    fun onLanUrlChange(url: String) {
        _uiState.update { it.copy(lanUrl = url, testState = TestConnectionState.Idle) }
        viewModelScope.launch { settingsRepository.setBaseUrl(url) }
    }

    fun onTokenChange(token: String) {
        _uiState.update { it.copy(token = token, testState = TestConnectionState.Idle) }
        viewModelScope.launch { settingsRepository.setToken(token) }
    }

    fun toggleTokenRevealed() {
        _uiState.update { it.copy(tokenRevealed = !it.tokenRevealed) }
    }

    fun onPollIntervalSelected(seconds: Int) {
        _uiState.update { it.copy(pollIntervalSeconds = seconds) }
        viewModelScope.launch { settingsRepository.setAutoRefreshSeconds(seconds) }
    }

    fun onSetupHintsChange(enabled: Boolean) {
        _uiState.update { it.copy(setupHintsEnabled = enabled) }
        viewModelScope.launch { settingsRepository.setSetupHintsEnabled(enabled) }
    }

    /**
     * Asks the laptop for a new token, saves it for the laptop in use and shows the result. Every other phone or tool
     * that still has the old token is locked out until it scans the QR code again.
     */
    fun generateNewToken() {
        val state = _uiState.value
        if (state.token.isBlank()) {
            _uiState.update { it.copy(tokenMessage = "Connect to a laptop first") }
            return
        }
        viewModelScope.launch {
            _uiState.update { it.copy(tokenMessage = "Asking the laptop...") }
            val message = try {
                val outcome = connectionProbe.probe(
                    token = state.token,
                    lanUrl = state.lanUrl.ifBlank { null },
                    tailscaleUrl = state.tailscaleUrl,
                )
                when (outcome) {
                    is ProbeOutcome.Unreachable -> "The laptop is not reachable: ${outcome.lastError}"
                    is ProbeOutcome.Reachable -> {
                        val newToken = ApiClient(outcome.result.baseUrl, state.token).rotateToken()
                        settingsRepository.setToken(newToken)
                        _uiState.update { it.copy(token = newToken, testState = TestConnectionState.Idle) }
                        "New token saved. Other phones must scan the QR code again."
                    }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                "Could not get a new token: ${e.message ?: e.javaClass.simpleName}"
            }
            _uiState.update { it.copy(tokenMessage = message) }
        }
    }

    /** Tries every transport (USB, the edited Tailscale URL, the edited LAN URL) and reports the first that answers. */
    fun testConnection() {
        val state = _uiState.value
        if (state.token.isBlank()) {
            _uiState.update { it.copy(testState = TestConnectionState.Error("Enter a token first")) }
            return
        }
        viewModelScope.launch {
            _uiState.update { it.copy(testState = TestConnectionState.Loading) }
            val outcome = connectionProbe.probe(
                token = state.token,
                lanUrl = state.lanUrl.ifBlank { null },
                tailscaleUrl = state.tailscaleUrl,
            )
            _uiState.update {
                it.copy(
                    testState = when (outcome) {
                        is ProbeOutcome.Reachable -> TestConnectionState.Success(outcome.result.ping)
                        is ProbeOutcome.Unreachable -> TestConnectionState.Error(outcome.lastError)
                    },
                )
            }
        }
    }

    /** "Forget setup" (PLAN.md task 22): wipes DataStore, then the caller navigates to Setup. */
    fun forgetSetup(onForgotten: () -> Unit) {
        viewModelScope.launch {
            settingsRepository.clear()
            onForgotten()
        }
    }
}
