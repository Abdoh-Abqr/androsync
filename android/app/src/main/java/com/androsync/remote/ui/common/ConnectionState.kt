package com.androsync.remote.ui.common

import com.androsync.remote.data.api.CurrentJobDto
import com.androsync.remote.data.api.DeviceStateDto
import com.androsync.remote.data.settings.Preset

/**
 * Live reachability of the laptop daemon, refreshed every 5 s by
 * `ConnectionViewModel`'s poll loop while the app is in the foreground
 * (PLAN.md task 18).
 */
sealed class ConnectionState {
    data class Connected(
        val via: Preset,
        val baseUrl: String,
        val version: String,
        val device: String? = null,
        /** The sync running on the laptop right now (from the ping), or null (task 36b). */
        val currentJob: CurrentJobDto? = null,
        /** Every phone the laptop's adb knows, with its state (task 37f). */
        val devices: List<DeviceStateDto> = emptyList(),
        /** True when the laptop says the phone app's agent is polling it (task 37c). */
        val agentConnected: Boolean = false,
        /** What the computer is called, for labels like "Ubuntu ↔ S24 FE" (Phase V2); null from an older laptop. */
        val laptopName: String? = null,
    ) : ConnectionState()

    data class Unreachable(val lastError: String?) : ConnectionState()
}

/** Label for the top-bar [ConnectionBadge]: "USB", "Tailscale", "LAN", or "Laptop not reachable". */
fun connectionBadgeLabel(state: ConnectionState): String = when (state) {
    is ConnectionState.Connected -> when (state.via) {
        Preset.USB -> "USB"
        Preset.TAILSCALE -> "Tailscale"
        Preset.LAN -> "LAN"
    }
    is ConnectionState.Unreachable -> "Laptop not reachable"
}
