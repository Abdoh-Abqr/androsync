package com.androsync.remote.ui.support

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SupportTest {
    private val token = "Zk3pQ9vL2mT7xR1sW8yB4nC6dF0gH5jK-aE_uI"
    private val serial = "HWSERIAL12345"

    @Test
    fun `classifyIp names each kind and keeps loopback`() {
        assertEquals("<tailscale-ip>", Redactor.classifyIp("100.64.0.9"))
        assertEquals("<tailscale-ip>", Redactor.classifyIp("100.127.255.1"))
        assertEquals("<lan-ip>", Redactor.classifyIp("192.168.0.5"))
        assertEquals("<lan-ip>", Redactor.classifyIp("10.1.2.3"))
        assertEquals("<lan-ip>", Redactor.classifyIp("172.16.0.1"))
        assertEquals("<ip>", Redactor.classifyIp("8.8.8.8"))
        assertEquals("<ip>", Redactor.classifyIp("100.128.0.1"))
        assertNull(Redactor.classifyIp("127.0.0.1"))
        assertNull(Redactor.classifyIp("999.1.1.1"))
        assertNull(Redactor.classifyIp("1.2.3"))
    }

    @Test
    fun `the redactor removes tokens, serials, addresses and chosen names`() {
        val redactor = Redactor(secrets = listOf(token), serials = listOf(serial), names = mapOf("family photos" to "pair-1"))
        val out = redactor.redact(
            "token: $token Authorization: Bearer $token phone $serial pair family photos at 100.64.0.9 and 192.168.0.5 and 127.0.0.1",
        )
        for (secret in listOf(token, serial, "family photos", "100.64.0.9", "192.168.0.5")) assertFalse(secret, out.contains(secret))
        assertTrue(out.contains("127.0.0.1"))
        assertTrue(out.contains("<tailscale-ip>") && out.contains("<lan-ip>") && out.contains("<token>"))
        assertTrue(out.contains("phone-" + Redactor.shortHash(serial)))
    }

    @Test
    fun `a token nobody told us about is still caught by its shape`() {
        val out = Redactor().redact("""{"token": "$token"} Bearer $token""")
        assertFalse(out.contains(token))
    }

    @Test
    fun `the report has versions first and nothing identifying anywhere`() {
        val info = ReportInfo(
            appVersion = "0.2.0", flavor = "full", androidVersion = "14 (API 34)", model = "samsung SM-S721B",
            laptopVersion = "0.2.0", connection = "Tailscale", agentStatus = "connected",
            phones = listOf("$serial device usb"), jobs = listOf("sync done pairs=2"),
            appLog = listOf("10:00:01 [connection] lost: failed to connect to /100.64.0.9:8765 token=$token"),
        )
        val report = ReportBuilder.build(info, Redactor(secrets = listOf(token), serials = listOf(serial)))
        assertTrue(report.startsWith("AndroSync problem report"))
        assertTrue(report.contains("app: 0.2.0 (full)") && report.contains("laptop: 0.2.0"))
        for (secret in listOf(token, serial, "100.64.0.9")) assertFalse(secret, report.contains(secret))
    }

    @Test
    fun `a report without a laptop says so`() {
        val info = ReportInfo("0.2.0", "play", "14", "x y", null, "Laptop not reachable", null)
        assertTrue(ReportBuilder.build(info, Redactor()).contains("laptop: not reachable"))
    }

    @Test
    fun `the app log keeps only the newest lines`() {
        AppLog.clear()
        repeat(AppLog.CAPACITY + 25) { AppLog.add("t", "line $it", now = 0L) }
        val lines = AppLog.snapshot()
        assertEquals(AppLog.CAPACITY, lines.size)
        assertTrue(lines.last().endsWith("line ${AppLog.CAPACITY + 24}"))
        assertFalse(lines.any { it.endsWith("line 0") })
        AppLog.clear()
    }
}
