package com.androsync.remote.data.api

import com.androsync.remote.data.settings.Preset
import com.androsync.remote.ui.common.isTailscaleUrl
import kotlinx.coroutines.CancellationException

/** What answered a [ConnectionProbe.probe] call: which preset, at which URL, with which ping reply. */
data class ProbeResult(val preset: Preset, val baseUrl: String, val ping: PingDto)

/** Outcome of one [ConnectionProbe.probe] pass over every candidate transport. */
sealed class ProbeOutcome {
    data class Reachable(val result: ProbeResult) : ProbeOutcome()

    /** [lastError] is the failure from the last candidate tried, for display in the UI. */
    data class Unreachable(val lastError: String) : ProbeOutcome()
}

/**
 * Tries the laptop daemon over USB, then Tailscale, then the saved LAN URL,
 * in that order (PLAN.md "Android app": "The app tries USB first, then
 * Tailscale, then LAN automatically"), returning the first one that answers
 * `GET /v1/ping`.
 */
class ConnectionProbe(
    private val clientFactory: (baseUrl: String, token: String) -> ApiClient = { url, token ->
        ApiClient(url, token)
    },
) {
    suspend fun probe(
        token: String,
        lanUrl: String?,
        tailscaleUrl: String? = null,
    ): ProbeOutcome {
        val candidates = buildList {
            add(Preset.USB to ApiClient.PRESET_USB_URL)
            if (!tailscaleUrl.isNullOrBlank()) add(Preset.TAILSCALE to tailscaleUrl)
            // A Tailscale address saved in the LAN field is still a Tailscale address: label it by what it is.
            if (!lanUrl.isNullOrBlank()) add((if (isTailscaleUrl(lanUrl)) Preset.TAILSCALE else Preset.LAN) to lanUrl)
        }.distinctBy { (_, url) -> url.trimEnd('/') }

        var lastError = "Laptop not reachable"
        for ((preset, url) in candidates) {
            try {
                val ping = clientFactory(url, token).ping()
                return ProbeOutcome.Reachable(ProbeResult(preset, url, ping))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // Not reachable over this transport; remember why and fall through to the next one.
                lastError = e.message ?: e.toString()
            }
        }
        return ProbeOutcome.Unreachable(lastError)
    }
}
