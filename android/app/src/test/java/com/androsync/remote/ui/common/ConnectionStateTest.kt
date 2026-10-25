package com.androsync.remote.ui.common

import com.androsync.remote.data.settings.Preset
import org.junit.Assert.assertEquals
import org.junit.Test

class ConnectionStateTest {

    @Test
    fun `USB connected label`() {
        val state = ConnectionState.Connected(via = Preset.USB, baseUrl = "http://127.0.0.1:8765", version = "0.3.0")
        assertEquals("USB", connectionBadgeLabel(state))
    }

    @Test
    fun `Tailscale connected label`() {
        val state = ConnectionState.Connected(via = Preset.TAILSCALE, baseUrl = "http://100.64.0.10:8765", version = "0.3.0")
        assertEquals("Tailscale", connectionBadgeLabel(state))
    }

    @Test
    fun `LAN connected label`() {
        val state = ConnectionState.Connected(via = Preset.LAN, baseUrl = "http://192.168.1.5:8765", version = "0.3.0")
        assertEquals("LAN", connectionBadgeLabel(state))
    }

    @Test
    fun `unreachable label ignores the error detail`() {
        assertEquals("Laptop not reachable", connectionBadgeLabel(ConnectionState.Unreachable("timeout")))
        assertEquals("Laptop not reachable", connectionBadgeLabel(ConnectionState.Unreachable(null)))
    }
}
