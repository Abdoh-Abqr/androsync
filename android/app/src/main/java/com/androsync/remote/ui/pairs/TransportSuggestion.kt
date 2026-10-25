package com.androsync.remote.ui.pairs

import com.androsync.remote.ui.common.ConnectionState

/**
 * Which way of reaching the phone to preselect for a NEW pair (task 37d follow-up): `adb` when the laptop sees a ready
 * phone, `agent` when it does not but the phone app's agent is polling, else `adb` (the long-standing default).
 * Without this a pair made for the agent silently got `adb` and then asked for a USB cable.
 */
fun suggestTransport(state: ConnectionState): String {
    val connected = state as? ConnectionState.Connected ?: return "adb"
    val adbReady = connected.device != null || connected.devices.any { it.state == "device" }
    return if (!adbReady && connected.agentConnected) "agent" else "adb"
}
