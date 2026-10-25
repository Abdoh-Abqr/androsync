package com.androsync.remote.ui

import com.androsync.remote.data.api.ApiClient
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ClientIsStaleTest {
    private val client = ApiClient("http://127.0.0.1:8765", "old-token")

    @Test
    fun `no client yet is stale`() = assertTrue(clientIsStale(null, "http://127.0.0.1:8765", "old-token"))

    @Test
    fun `same address and token keeps the client`() =
        assertFalse(clientIsStale(client, "http://127.0.0.1:8765", "old-token"))

    @Test
    fun `a new address rebuilds the client`() =
        assertTrue(clientIsStale(client, "http://100.64.0.1:8765", "old-token"))

    @Test
    fun `a new token rebuilds the client, so pairs load again after a token reset`() =
        assertTrue(clientIsStale(client, "http://127.0.0.1:8765", "new-token"))
}
