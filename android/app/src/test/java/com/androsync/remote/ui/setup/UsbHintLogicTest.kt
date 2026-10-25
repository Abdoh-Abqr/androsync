package com.androsync.remote.ui.setup

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class UsbHintLogicTest {
    private fun nudge(
        enabled: Boolean = true,
        connected: Boolean = true,
        adb: Boolean = false,
        was: Boolean = false,
        now: Long = 100_000,
        last: Long? = null,
    ) = shouldNudgeAboutDebugging(enabled, connected, adb, was, now, last)

    @Test
    fun `plugging in with debugging off nudges once`() = assertTrue(nudge())

    @Test
    fun `debugging already on does not nudge`() = assertFalse(nudge(adb = true))

    @Test
    fun `the switch in Settings turns the nudge off`() = assertFalse(nudge(enabled = false))

    @Test
    fun `unplugging or staying plugged in does not nudge`() {
        assertFalse(nudge(connected = false))
        assertFalse(nudge(was = true))
    }

    @Test
    fun `a second plug-in within the gap is quiet, after the gap it nudges again`() {
        assertFalse(nudge(now = 100_000, last = 80_000))
        assertTrue(nudge(now = 100_000, last = 60_000))
    }
}
