package com.androsync.remote.widget

import com.androsync.remote.data.api.PairDto
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class WidgetStateTest {

    // --- encode/decode round trip -----------------------------------------

    @Test
    fun `round trips through JSON`() {
        val state = WidgetState(lastResultLine = "3 copied, 1 conflict", lastRunIso = "2026-09-01T12:03:41.512309")
        val decoded = decodeWidgetState(encodeWidgetState(state))
        assertEquals(state, decoded)
    }

    @Test
    fun `decoding null or blank yields the default state`() {
        assertEquals(WidgetState(), decodeWidgetState(null))
        assertEquals(WidgetState(), decodeWidgetState(""))
        assertEquals(WidgetState(), decodeWidgetState("   "))
    }

    @Test
    fun `decoding garbage falls back to the default state instead of throwing`() {
        assertEquals(WidgetState(), decodeWidgetState("not json"))
    }

    @Test
    fun `default state has no last run`() {
        assertNull(WidgetState().lastRunIso)
    }

    // --- widgetStateFromPairs ------------------------------------------------

    @Test
    fun `picks the most recently synced pair`() {
        val pairs = listOf(
            PairDto("photos", "/local", "/remote", "two-way", "newest-wins", lastRun = "2026-09-01T10:00:00", lastResult = "copied 2"),
            PairDto("vault", "/local2", "/remote2", "two-way", "newest-wins", lastRun = "2026-09-02T09:00:00", lastResult = "no changes"),
        )
        val state = widgetStateFromPairs(pairs)
        assertEquals("no changes", state.lastResultLine)
        assertEquals("2026-09-02T09:00:00", state.lastRunIso)
    }

    @Test
    fun `ignores pairs that have never run`() {
        val pairs = listOf(
            PairDto("photos", "/local", "/remote", "two-way", "newest-wins", lastRun = null, lastResult = null),
        )
        val state = widgetStateFromPairs(pairs)
        assertEquals(WidgetState(), state)
    }

    @Test
    fun `empty pair list yields the default state`() {
        assertEquals(WidgetState(), widgetStateFromPairs(emptyList()))
    }
}
