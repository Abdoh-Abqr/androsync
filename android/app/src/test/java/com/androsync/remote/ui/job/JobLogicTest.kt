package com.androsync.remote.ui.job

import com.androsync.remote.data.api.SummaryDto
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class JobLogicTest {

    // --- isTerminalState -----------------------------------------------

    @Test
    fun `active states are not terminal`() {
        assertFalse(isTerminalState("queued"))
        assertFalse(isTerminalState("running"))
        assertFalse(isTerminalState(null))
    }

    @Test
    fun `done, failed and cancelled are terminal`() {
        assertTrue(isTerminalState("done"))
        assertTrue(isTerminalState("failed"))
        assertTrue(isTerminalState("cancelled"))
        assertFalse(isTerminalState("cancelling")) // still busy until the killed call returns
    }

    // --- appendLogLine (ring buffer) -------------------------------------

    @Test
    fun `appendLogLine grows the list under the cap`() {
        val result = appendLogLine(listOf("a", "b"), "c", cap = 10)
        assertEquals(listOf("a", "b", "c"), result)
    }

    @Test
    fun `appendLogLine drops the oldest lines once past the cap`() {
        val lines = (1..5).map { "line$it" }
        val result = appendLogLine(lines, "line6", cap = 3)
        assertEquals(listOf("line4", "line5", "line6"), result)
    }

    @Test
    fun `appendLogLine at exactly the cap does not drop anything`() {
        val lines = listOf("a", "b")
        val result = appendLogLine(lines, "c", cap = 3)
        assertEquals(listOf("a", "b", "c"), result)
    }

    // --- shouldStickToBottom (auto-scroll decision) ----------------------

    @Test
    fun `sticks to bottom when the last visible item was the last item`() {
        assertTrue(shouldStickToBottom(lastVisibleItemIndex = 9, totalItemCountBeforeAppend = 10))
    }

    @Test
    fun `does not stick when the user scrolled away from the bottom`() {
        assertFalse(shouldStickToBottom(lastVisibleItemIndex = 3, totalItemCountBeforeAppend = 10))
    }

    @Test
    fun `sticks to bottom on an empty list`() {
        assertTrue(shouldStickToBottom(lastVisibleItemIndex = 0, totalItemCountBeforeAppend = 0))
    }

    // --- jobChipFor -------------------------------------------------------

    @Test
    fun `maps every known state to its label and colour`() {
        assertEquals(JobChip("Queued", JobChipColor.QUEUED), jobChipFor("queued"))
        assertEquals(JobChip("Running", JobChipColor.RUNNING), jobChipFor("running"))
        assertEquals(JobChip("Done", JobChipColor.DONE), jobChipFor("done"))
        assertEquals(JobChip("Failed", JobChipColor.FAILED), jobChipFor("failed"))
        assertEquals(JobChip("Cancelled", JobChipColor.CANCELLED), jobChipFor("cancelled"))
        assertEquals(JobChip("Stopping...", JobChipColor.STOPPING), jobChipFor("cancelling"))
    }

    @Test
    fun `falls back to a capitalized label for an unknown state`() {
        val chip = jobChipFor("weird")
        assertEquals("Weird", chip.label)
    }

    // --- summaryRowFor / formatSummaryRow ---------------------------------

    @Test
    fun `summaryRowFor maps daemon field names to phone-laptop wording`() {
        val summary = SummaryDto(
            copiedToLocal = 2,
            copiedToRemote = 5,
            deletedLocal = 1,
            deletedRemote = 3,
            conflicts = 1,
            errors = 0,
        )
        val row = summaryRowFor("vault", summary)
        assertEquals(PairSummaryRow("vault", copiedToPhone = 5, copiedToLaptop = 2, deleted = 4, conflicts = 1, errors = 0), row)
    }

    @Test
    fun `formatSummaryRow renders every field`() {
        val row = PairSummaryRow("photos", copiedToPhone = 1, copiedToLaptop = 2, deleted = 3, conflicts = 4, errors = 5)
        assertEquals(
            "photos: 1 to phone, 2 to laptop, 3 deleted, 4 conflicts, 5 errors",
            formatSummaryRow(row),
        )
    }

    @Test
    fun `needs_confirmation is final and has its own chip`() {
        assertTrue(isTerminalState("needs_confirmation"))
        assertEquals(JobChip("Needs your OK", JobChipColor.NEEDS_OK), jobChipFor("needs_confirmation"))
    }
}
