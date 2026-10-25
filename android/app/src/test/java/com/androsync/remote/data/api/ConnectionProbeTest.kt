package com.androsync.remote.data.api

import com.androsync.remote.data.settings.Preset
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * [ConnectionProbe] tries USB, then Tailscale, then the saved LAN URL, in
 * that order, skipping Tailscale/LAN whenever that URL is null/blank (task
 * 33e: neither has a built-in default - only USB's loopback address does),
 * and reports why every candidate failed if none answer (PLAN.md task 18).
 * The injected [ConnectionProbe]'s `clientFactory` is used for every
 * candidate regardless of the URL it's asked to build a client for - a
 * single [MockWebServer] stands in for whichever daemon would have
 * answered, letting these tests control success/failure per call without
 * needing the real fixed URLs to be reachable.
 */
class ConnectionProbeTest {

    private lateinit var server: MockWebServer

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun probeAlwaysPointingAt(server: MockWebServer): ConnectionProbe =
        ConnectionProbe(clientFactory = { _, token -> ApiClient(server.url("/").toString(), token) })

    @Test
    fun `first reachable candidate wins`() = runTest {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"name":"androsync","version":"0.3.0","transport":"usb"}"""))

        val outcome = probeAlwaysPointingAt(server).probe(token = "t", lanUrl = null)

        assertTrue(outcome is ProbeOutcome.Reachable)
        val result = (outcome as ProbeOutcome.Reachable).result
        assertEquals(Preset.USB, result.preset)
        assertEquals("0.3.0", result.ping.version)
    }

    @Test
    fun `falls through to the next candidate when the first fails`() = runTest {
        // USB candidate: connection refused/reset.
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START))
        // Tailscale candidate: succeeds.
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"name":"androsync","version":"0.3.0"}"""))

        val outcome = probeAlwaysPointingAt(server)
            .probe(token = "t", lanUrl = null, tailscaleUrl = "http://198.51.100.5:8765")

        assertTrue(outcome is ProbeOutcome.Reachable)
        assertEquals(Preset.TAILSCALE, (outcome as ProbeOutcome.Reachable).result.preset)
    }

    @Test
    fun `blank tailscale URL skips that candidate entirely`() = runTest {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"name":"androsync","version":"0.3.0"}"""))

        val outcome = probeAlwaysPointingAt(server).probe(token = "t", lanUrl = null, tailscaleUrl = "")

        assertTrue(outcome is ProbeOutcome.Reachable)
        assertEquals(Preset.USB, (outcome as ProbeOutcome.Reachable).result.preset)
        assertEquals(1, server.requestCount)
    }

    @Test
    fun `tries the LAN URL last, only when one is supplied`() = runTest {
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START))
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START))
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"name":"androsync","version":"0.3.0"}"""))

        val outcome = probeAlwaysPointingAt(server).probe(
            token = "t",
            lanUrl = "http://192.168.1.5:8765",
            tailscaleUrl = "http://198.51.100.5:8765",
        )

        assertTrue(outcome is ProbeOutcome.Reachable)
        assertEquals(Preset.LAN, (outcome as ProbeOutcome.Reachable).result.preset)
    }

    @Test
    fun `no LAN or Tailscale URL means only USB is tried`() = runTest {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"error":"unauthorized"}"""))

        val outcome = probeAlwaysPointingAt(server).probe(token = "t", lanUrl = null)

        assertTrue(outcome is ProbeOutcome.Unreachable)
        assertEquals(1, server.requestCount)
    }

    @Test
    fun `unreachable outcome carries the last candidate's error`() = runTest {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"error":"unauthorized"}"""))
        server.enqueue(MockResponse().setResponseCode(500).setBody("""{"error":"boom"}"""))

        val outcome = probeAlwaysPointingAt(server)
            .probe(token = "t", lanUrl = null, tailscaleUrl = "http://198.51.100.5:8765")

        assertTrue(outcome is ProbeOutcome.Unreachable)
        assertEquals("boom", (outcome as ProbeOutcome.Unreachable).lastError)
    }

    @Test
    fun `a saved address equal to the USB address is not probed twice`() = runTest {
        val tried = mutableListOf<String>()
        val probe = ConnectionProbe(clientFactory = { url, token ->
            tried += url
            ApiClient(server.url("/").toString(), token)
        })
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START))

        probe.probe(token = "t", lanUrl = ApiClient.PRESET_USB_URL + "/")

        assertEquals(listOf(ApiClient.PRESET_USB_URL), tried)
    }

    @Test
    fun `a saved address is tried even when it is the only thing that works`() = runTest {
        // The connection poll now passes the saved address whatever the preset is (it used to do so for the LAN
        // preset only, so a scanned address looked unreachable while "Test connection" said OK).
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START))
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"name":"androsync","version":"0.3.0"}"""))

        val outcome = probeAlwaysPointingAt(server).probe(token = "t", lanUrl = "http://198.51.100.9:8765")

        assertEquals(Preset.LAN, (outcome as ProbeOutcome.Reachable).result.preset)
    }

    @Test
    fun `a Tailscale address saved in the LAN field is reported as Tailscale, a real LAN one as LAN`() = runTest {
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START))
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"name":"androsync","version":"0.3.0"}"""))
        val viaTailscaleAddress = probeAlwaysPointingAt(server).probe(token = "t", lanUrl = "http://100.77.1.2:8765")
        assertEquals(Preset.TAILSCALE, (viaTailscaleAddress as ProbeOutcome.Reachable).result.preset)

        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START))
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"name":"androsync","version":"0.3.0"}"""))
        val viaLan = probeAlwaysPointingAt(server).probe(token = "t", lanUrl = "http://192.168.0.5:8765")
        assertEquals(Preset.LAN, (viaLan as ProbeOutcome.Reachable).result.preset)
    }
}
