package com.androsync.remote.ui.pairs

import com.androsync.remote.data.api.DeviceStateDto
import com.androsync.remote.data.settings.Preset
import com.androsync.remote.ui.common.ConnectionState
import org.junit.Assert.assertEquals
import org.junit.Test

class TransportSuggestionTest {
    private fun connected(
        device: String? = null,
        devices: List<DeviceStateDto> = emptyList(),
        agent: Boolean = false,
    ) = ConnectionState.Connected(
        via = Preset.TAILSCALE, baseUrl = "http://x:1", version = "0", device = device, devices = devices, agentConnected = agent,
    )

    @Test
    fun `an attached phone means adb, even if the agent is also polling`() {
        assertEquals("adb", suggestTransport(connected(device = "ABC", agent = true)))
        assertEquals("adb", suggestTransport(connected(devices = listOf(DeviceStateDto("x", "device")), agent = true)))
    }

    @Test
    fun `no adb phone but a polling agent means agent`() {
        assertEquals("agent", suggestTransport(connected(agent = true)))
        assertEquals("agent", suggestTransport(connected(devices = listOf(DeviceStateDto("x", "unauthorized")), agent = true)))
    }

    @Test
    fun `nothing available, or no connection at all, keeps adb`() {
        assertEquals("adb", suggestTransport(connected()))
        assertEquals("adb", suggestTransport(ConnectionState.Unreachable("down")))
    }
}
