package com.androsync.remote.ui.common

import com.androsync.remote.data.api.CurrentJobDto
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ActiveJobLogicTest {

    private val now = 1_000_000.0

    @Test
    fun `no running job means no bar`() {
        assertNull(activeJobBarModel(null, now))
    }

    @Test
    fun `a running sync shows the pair, the elapsed time and the latest log line`() {
        val job = CurrentJobDto(id = "ab12", pairs = listOf("vault"), state = "running", started = now - 42, lastLine = "batch 2: 50 files, 12.0 MB")
        val model = activeJobBarModel(job, now)!!
        assertEquals("ab12", model.jobId)
        assertEquals("Syncing vault · 0:42", model.title)
        assertEquals("batch 2: 50 files, 12.0 MB", model.detail)
        assertTrue(model.stopEnabled)
        assertEquals("Stop", model.stopLabel)
    }

    @Test
    fun `a job with no pair names is all pairs, an undo says undoing, a preview says previewing`() {
        assertEquals("Syncing all pairs", activeJobBarModel(CurrentJobDto(id = "a"), now)!!.title)
        assertEquals("Undoing p", activeJobBarModel(CurrentJobDto(id = "a", pairs = listOf("p"), kind = "undo"), now)!!.title)
        assertEquals("Previewing p", activeJobBarModel(CurrentJobDto(id = "a", pairs = listOf("p"), dryRun = true), now)!!.title)
    }

    @Test
    fun `while stopping the button is disabled and says so`() {
        val model = activeJobBarModel(CurrentJobDto(id = "a", pairs = listOf("p"), state = "cancelling"), now)!!
        assertFalse(model.stopEnabled)
        assertEquals("Stopping...", model.stopLabel)
    }

    @Test
    fun `a blank last line is treated as no detail`() {
        assertNull(activeJobBarModel(CurrentJobDto(id = "a", lastLine = "  "), now)!!.detail)
    }

    @Test
    fun `elapsed time formats minutes and hours and never goes negative`() {
        assertEquals("0:00", formatElapsed(-5))
        assertEquals("0:07", formatElapsed(7))
        assertEquals("12:03", formatElapsed(723))
        assertEquals("1:02:03", formatElapsed(3723))
    }
}
