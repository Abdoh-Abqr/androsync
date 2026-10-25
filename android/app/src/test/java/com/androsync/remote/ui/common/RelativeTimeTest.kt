package com.androsync.remote.ui.common

import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class RelativeTimeTest {

    private val now = Instant.parse("2026-09-06T12:00:00Z")

    @Test
    fun `null timestamp returns null`() {
        assertNull(formatRelativeTime(null, now))
    }

    @Test
    fun `blank timestamp returns null`() {
        assertNull(formatRelativeTime("   ", now))
    }

    @Test
    fun `unparsable timestamp returns null`() {
        assertNull(formatRelativeTime("not-a-date", now))
    }

    @Test
    fun `under a minute is just now`() {
        assertEquals("just now", formatRelativeTime("2026-09-06T11:59:45", now))
    }

    @Test
    fun `90 seconds is 1 min ago`() {
        assertEquals("1 min ago", formatRelativeTime("2026-09-06T11:58:30", now))
    }

    @Test
    fun `exactly 60 seconds is 1 min ago`() {
        assertEquals("1 min ago", formatRelativeTime("2026-09-06T11:59:00", now))
    }

    @Test
    fun `two hours is 2 hr ago`() {
        assertEquals("2 hr ago", formatRelativeTime("2026-09-06T10:00:00", now))
    }

    @Test
    fun `two days is 2 days ago`() {
        assertEquals("2 days ago", formatRelativeTime("2026-09-04T12:00:00", now))
    }

    @Test
    fun `handles fractional seconds like Python isoformat`() {
        assertEquals("just now", formatRelativeTime("2026-09-06T11:59:41.512309", now))
    }

    @Test
    fun `handles trailing Z as UTC`() {
        assertEquals("just now", formatRelativeTime("2026-09-06T11:59:45Z", now))
    }

    @Test
    fun `future timestamp clamps to just now instead of going negative`() {
        assertEquals("just now", formatRelativeTime("2026-09-06T12:05:00", now))
    }
}
