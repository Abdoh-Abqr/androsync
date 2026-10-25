package com.androsync.remote.ui.common

import com.androsync.remote.data.settings.Preset
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.assertNull
import org.junit.Test

class QrPayloadTest {

    @Test
    fun `parses the shape androsync qr produces`() {
        val raw = """{"urls": ["http://127.0.0.1:8765", "http://100.64.0.10:8765"], "token": "abc123"}"""

        val payload = parseQrPayload(raw)

        assertEquals(listOf("http://127.0.0.1:8765", "http://100.64.0.10:8765"), payload?.urls)
        assertEquals("abc123", payload?.token)
    }

    @Test
    fun `malformed JSON returns null`() {
        assertNull(parseQrPayload("not json"))
    }

    @Test
    fun `JSON with the wrong field type returns null`() {
        // Missing fields are fine (defaults apply, see below); a wrong *type* is not.
        assertNull(parseQrPayload("""{"urls": "not-a-list", "token": "x"}"""))
    }

    @Test
    fun `missing fields fall back to defaults instead of failing`() {
        val payload = parseQrPayload("""{}""")
        assertEquals(emptyList<String>(), payload?.urls)
        assertEquals("", payload?.token)
    }

    @Test
    fun `pickUrlForPreset returns the exact USB match`() {
        val urls = listOf("http://100.64.0.10:8765", "http://127.0.0.1:8765", "http://192.168.1.5:8765")
        assertEquals("http://127.0.0.1:8765", pickUrlForPreset(urls, Preset.USB))
    }

    @Test
    fun `pickUrlForPreset for Tailscale skips the USB-looking URL`() {
        // Tailscale has no fixed URL to match against (task 33e) - it prefers whichever
        // URL in the list is not the USB loopback one, rather than always taking the first.
        val urls = listOf("http://127.0.0.1:8765", "http://100.64.0.10:8765")
        assertEquals("http://100.64.0.10:8765", pickUrlForPreset(urls, Preset.TAILSCALE))
    }

    @Test
    fun `pickUrlForPreset for Tailscale falls back to the first URL when every URL looks like USB`() {
        val urls = listOf("http://127.0.0.1:8765")
        assertEquals("http://127.0.0.1:8765", pickUrlForPreset(urls, Preset.TAILSCALE))
    }

    @Test
    fun `pickUrlForPreset matches by host when scheme or trailing slash differs`() {
        val urls = listOf("http://127.0.0.1:8765/")
        assertEquals("http://127.0.0.1:8765/", pickUrlForPreset(urls, Preset.USB))
    }

    @Test
    fun `pickUrlForPreset falls back to the first URL for LAN`() {
        val urls = listOf("http://192.168.1.5:8765", "http://127.0.0.1:8765")
        assertEquals("http://192.168.1.5:8765", pickUrlForPreset(urls, Preset.LAN))
    }

    @Test
    fun `pickUrlForPreset falls back to the first URL when no fixed preset matches`() {
        val urls = listOf("http://192.168.1.5:8765", "http://192.168.1.6:8765")
        assertEquals("http://192.168.1.5:8765", pickUrlForPreset(urls, Preset.USB))
    }

    @Test
    fun `pickUrlForPreset returns null for an empty list`() {
        assertNull(pickUrlForPreset(emptyList(), Preset.USB))
    }

    @Test
    fun `classifyQrUrls puts the Tailscale address in its own field and ignores the USB loopback`() {
        val result = classifyQrUrls(listOf("http://127.0.0.1:8765", "http://100.77.1.2:8765"))
        assertEquals("http://100.77.1.2:8765", result.tailscale)
        assertEquals(null, result.lan)
    }

    @Test
    fun `classifyQrUrls keeps a LAN address apart from a Tailscale one`() {
        val result = classifyQrUrls(listOf("http://127.0.0.1:8765", "http://192.168.0.5:8765", "http://100.127.255.1:8765"))
        assertEquals("http://100.127.255.1:8765", result.tailscale)
        assertEquals("http://192.168.0.5:8765", result.lan)
    }

    @Test
    fun `classifyQrUrls treats 100 dot 63 and 100 dot 128 as ordinary addresses, and ts net names as Tailscale`() {
        assertEquals("http://100.63.0.1:8765", classifyQrUrls(listOf("http://100.63.0.1:8765")).lan)
        assertEquals("http://100.128.0.1:8765", classifyQrUrls(listOf("http://100.128.0.1:8765")).lan)
        assertEquals("http://laptop.tail1234.ts.net:8765", classifyQrUrls(listOf("http://laptop.tail1234.ts.net:8765")).tailscale)
    }

    @Test
    fun `classifyQrUrls of only the USB address finds nothing to save`() {
        assertEquals(QrAddresses(null, null), classifyQrUrls(listOf("http://127.0.0.1:8765")))
    }

    @Test
    fun `isTailscaleUrl recognises the Tailscale range and ts net names only`() {
        assertTrue(isTailscaleUrl("http://100.64.0.7:8765"))
        assertTrue(isTailscaleUrl("http://box.tail1.ts.net:8765"))
        assertFalse(isTailscaleUrl("http://192.168.0.5:8765"))
        assertFalse(isTailscaleUrl("http://127.0.0.1:8765"))
        assertFalse(isTailscaleUrl("not a url"))
    }

    @Test
    fun `repairSavedAddresses moves a Tailscale address out of the LAN field`() {
        assertEquals("" to "http://100.77.1.2:8765", repairSavedAddresses("http://100.77.1.2:8765", ""))
    }

    @Test
    fun `repairSavedAddresses never overwrites a saved Tailscale address, and leaves real LAN addresses alone`() {
        assertEquals("" to "http://100.64.0.9:8765", repairSavedAddresses("http://100.77.1.2:8765", "http://100.64.0.9:8765"))
        assertEquals(null, repairSavedAddresses("http://192.168.0.5:8765", ""))
        assertEquals(null, repairSavedAddresses("", "http://100.64.0.9:8765"))
    }

    @Test
    fun `the QR payload carries the laptop name, and older codes without one still parse`() {
        val withName = parseQrPayload("""{"name":"work-laptop","urls":["http://127.0.0.1:8765"],"token":"t"}""")!!
        assertEquals("work-laptop", withName.name)
        assertNull(parseQrPayload("""{"urls":["http://127.0.0.1:8765"],"token":"t"}""")!!.name)
    }

    @Test
    fun `normalizeAddresses drops loopback and moves a Tailscale address out of the LAN field`() {
        assertEquals("" to "", normalizeAddresses("", "http://127.0.0.1:8765"))
        assertEquals("http://100.64.0.7:8765" to "", normalizeAddresses("", "http://100.64.0.7:8765"))
        assertEquals("http://100.64.0.1:8765" to "", normalizeAddresses("http://100.64.0.1:8765", "http://100.64.0.7:8765"))
        assertEquals("http://100.64.0.1:8765" to "http://192.0.2.5:8765", normalizeAddresses(" http://100.64.0.1:8765 ", "http://192.0.2.5:8765"))
        assertEquals("" to "", normalizeAddresses("http://127.0.0.1:8765", ""))
    }

    @Test
    fun `repairSavedAddresses clears the USB loopback address that used to be the LAN default`() {
        assertEquals("" to "", repairSavedAddresses("http://127.0.0.1:8765", ""))
        assertEquals("" to "http://100.64.0.1:8765", repairSavedAddresses("http://127.0.0.1:8765", "http://100.64.0.1:8765"))
        assertEquals(null, repairSavedAddresses("", ""))
    }
}
