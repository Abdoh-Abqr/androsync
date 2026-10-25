package com.androsync.remote.ui.jobs

import com.androsync.remote.data.api.JobDto
import com.androsync.remote.data.api.SummaryDto
import org.junit.Assert.assertEquals
import org.junit.Test

class JobsLogicTest {

    private fun job(
        state: String = "done",
        pairs: List<String> = listOf("vault"),
        kind: String = "sync",
        dryRun: Boolean = false,
        summaries: Map<String, SummaryDto> = emptyMap(),
        error: String? = null,
        started: Double? = 100.0,
        finished: Double? = 165.0,
    ) = JobDto(
        id = "j", pairs = pairs, dryRun = dryRun, kind = kind, state = state, created = 100.0,
        started = started, finished = finished, summaries = summaries, error = error,
    )

    @Test
    fun `titles say what kind of job it was and for which pairs`() {
        assertEquals("Sync vault", jobRowTitle(job()))
        assertEquals("Undo vault", jobRowTitle(job(kind = "undo")))
        assertEquals("Preview vault", jobRowTitle(job(dryRun = true)))
        assertEquals("Sync all pairs", jobRowTitle(job(pairs = emptyList())))
    }

    @Test
    fun `age counts from creation`() {
        assertEquals("just now", jobAge(100.0, 130.0))
        assertEquals("5 min ago", jobAge(100.0, 100.0 + 5 * 60))
        assertEquals("3 hr ago", jobAge(100.0, 100.0 + 3 * 3600))
        assertEquals("2 days ago", jobAge(100.0, 100.0 + 2 * 86_400))
        assertEquals("just now", jobAge(500.0, 100.0)) // a clock difference never shows a negative age
    }

    @Test
    fun `a finished sync lists what it moved and how long it took`() {
        val summaries = mapOf(
            "vault" to SummaryDto(copiedToRemote = 3, copiedToLocal = 1, deletedLocal = 2, errors = 0),
        )
        assertEquals("3 to phone · 1 to laptop · 2 deleted · 1:05", jobRowSummary(job(summaries = summaries)))
    }

    @Test
    fun `a job that changed nothing says so`() {
        assertEquals("nothing changed · 1:05", jobRowSummary(job()))
    }

    @Test
    fun `running and stopping jobs show their state`() {
        assertEquals("running", jobRowSummary(job(state = "running", finished = null)))
        assertEquals("stopping", jobRowSummary(job(state = "cancelling", finished = null)))
    }

    @Test
    fun `a failed job shows its error`() {
        assertEquals("1:05 · no device found", jobRowSummary(job(state = "failed", error = "no device found")))
    }
}
