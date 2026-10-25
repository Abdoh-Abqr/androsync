package com.androsync.remote.ui.common

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LastResultTextTest {

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
