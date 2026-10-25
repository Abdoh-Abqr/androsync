package com.androsync.remote.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.androsync.remote.data.api.ApiClient
import com.androsync.remote.data.api.ConnectionProbe
import com.androsync.remote.data.api.ProbeOutcome
import com.androsync.remote.data.settings.LaptopProfile
import com.androsync.remote.data.settings.SettingsRepository
import com.androsync.remote.ui.common.ConnectionState
import com.androsync.remote.ui.common.repairSavedAddresses
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Shared connection state (PLAN.md task 18): one instance lives for the
 * whole app (created once in `AndroSyncApp()`, threaded down to every
 * screen) and, while [start]ed, runs [ConnectionProbe] every 5 s to decide
 * which transport currently reaches the laptop daemon.
 *
 * [start]/[stop] are meant to be driven by `Lifecycle.Event.ON_START`/
 * `ON_STOP` from the composable (see `LifecycleEventEffect` usage in
 * `MainActivity.kt`), so the poll loop only runs while the app is in the
 * foreground.
 */
class ConnectionViewModel(
    private val settingsRepository: SettingsRepository,
    private val connectionProbe: ConnectionProbe,
    private val clientFactory: (baseUrl: String, token: String) -> ApiClient = { url, token ->
        ApiClient(url, token)
    },
) : ViewModel() {

    private val _connectionState = MutableStateFlow<ConnectionState>(ConnectionState.Unreachable(null))
    val connectionState: StateFlow<ConnectionState> = _connectionState.asStateFlow()

    /** Rebuilt only when the reachable base URL changes (PLAN.md task 18). */
    private val _activeApiClient = MutableStateFlow<ApiClient?>(null)
    val activeApiClient: StateFlow<ApiClient?> = _activeApiClient.asStateFlow()

    private var pollingJob: Job? = null

    /** Every laptop the app knows, and which one is in use (task 33c). */
    val laptops: StateFlow<List<LaptopProfile>> =
        settingsRepository.laptops.stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())
    val selectedLaptopId: StateFlow<String?> =
        settingsRepository.selectedLaptopId.stateIn(viewModelScope, SharingStarted.Eagerly, null)

    /** Switches the whole app (pairs, tile, widget, agent) to another laptop and looks for it at once. */
    fun selectLaptop(id: String) {
        viewModelScope.launch {
            settingsRepository.selectLaptop(id)
            _activeApiClient.value = null
            _connectionState.value = ConnectionState.Unreachable("Switching laptop")
            pollOnce()
        }
    }

    fun start() {
        if (pollingJob?.isActive == true) return
        pollingJob = viewModelScope.launch {
            while (isActive) {
                pollOnce()
                // PLAN.md task 22: the interval is user-configurable in Settings.
                val seconds = settingsRepository.autoRefreshSeconds.first()
                delay(seconds * 1_000L)
            }
        }
    }

    fun stop() {
        pollingJob?.cancel()
        pollingJob = null
    }

    /** Ask the laptop to stop job [jobId] (task 36a/36b), then refresh at once so the bar shows "Stopping...". */
    fun stopJob(jobId: String) {
        viewModelScope.launch {
            runCatching { _activeApiClient.value?.cancelJob(jobId) }
            pollOnce()
        }
    }

    /** Poll now instead of waiting for the next tick (after starting or stopping a sync). */
    fun refreshNow() {
        viewModelScope.launch { pollOnce() }
    }

    private suspend fun pollOnce() {
        val token = settingsRepository.token.first()
        if (token.isBlank()) {
            _connectionState.value = ConnectionState.Unreachable("No token saved")
            return
        }
        // The saved address is tried whatever the preset says: a QR scan or the Setup form stores it there, and the
        // Settings "Test connection" and the agent already use it that way. Only trying it for the LAN preset left the
        // app saying "laptop not reachable" while the test button said OK.
        var lanUrl = settingsRepository.lanUrl.first()
        var tailscaleUrl = settingsRepository.tailscaleUrl.first()
        // Earlier versions saved a Tailscale address in the LAN field: move it to where it belongs.
        repairSavedAddresses(settingsRepository.baseUrl.first(), tailscaleUrl)?.let { (lan, tailscale) ->
            settingsRepository.setBaseUrl(lan)
            settingsRepository.setTailscaleUrl(tailscale)
            lanUrl = lan.takeIf { it.isNotBlank() }
            tailscaleUrl = tailscale
        }

        when (val outcome = connectionProbe.probe(token, lanUrl, tailscaleUrl)) {
            is ProbeOutcome.Reachable -> {
                val result = outcome.result
                _connectionState.value = ConnectionState.Connected(
                    via = result.preset,
                    baseUrl = result.baseUrl,
                    version = result.ping.version,
                    device = result.ping.device,
                    currentJob = result.ping.currentJob,
                    devices = result.ping.devices,
                    agentConnected = result.ping.agent == "connected",
                )
                if (clientIsStale(_activeApiClient.value, result.baseUrl, token)) {
                    _activeApiClient.value = clientFactory(result.baseUrl, token)
                }
            }

            is ProbeOutcome.Unreachable -> {
                _connectionState.value = ConnectionState.Unreachable(outcome.lastError)
            }
        }
    }
}

/**
 * True when the shared [current] client must be rebuilt: there is none, the reachable address changed, or the token
 * changed. The token case matters: after "reset token" the old client kept sending the old token, every request got
 * 401 and the pairs list stayed empty until the app was restarted.
 */
internal fun clientIsStale(current: ApiClient?, baseUrl: String, token: String): Boolean =
    current == null || current.baseUrl != baseUrl || current.token != token
