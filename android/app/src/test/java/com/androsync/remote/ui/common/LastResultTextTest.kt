package com.androsync.remote.ui.common

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LastResultTextTest {

    @Test
    fun `headline keeps only the first sentence of the wrong connection message`() {
        val full = "This pair works over USB or Wi-Fi debugging only, but the phone is connected over Tailscale right now. " +
            "no device found - check the USB cable. Or set this pair to \"This app (no debugging)\"."
        assertEquals(
            "This pair works over USB or Wi-Fi debugging only, but the phone is connected over Tailscale right now.",
            errorHeadline(full),
        )
    }

    @Test
    fun `headline leaves other errors unchanged`() {
        assertEquals("no device found. Check the cable.", errorHeadline("no device found. Check the cable."))
    }

    @Test
    fun `null result returns null`() {
        assertNull(formatLastResult(null))
    }

    @Test
    fun `blank result returns null`() {
        assertNull(formatLastResult("   "))
    }

    @Test
    fun `normal result passes through unchanged and is not a failure`() {
        val result = formatLastResult("copied_to_local=2")
        assertEquals("copied_to_local=2", result?.text)
        assertFalse(result!!.isFailure)
    }

    @Test
    fun `failed result is reworded and flagged`() {
        val result = formatLastResult("FAILED: no device attached")
        assertEquals("Last run failed: no device attached", result?.text)
        assertTrue(result!!.isFailure)
    }
}
