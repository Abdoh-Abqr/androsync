package com.androsync.remote.service

import com.androsync.remote.data.api.JobDto
import com.androsync.remote.data.api.SummaryDto
import org.junit.Assert.assertEquals
import org.junit.Test

class SyncNotificationsTest {

    // --- progressNotificationContent ------------------------------------

    @Test
    fun `progress title lists the pairs being synced`() {
        val content = progressNotificationContent(listOf("photos", "vault"), lastLogLine = "12:00 copying a.jpg")
        assertEquals("Syncing photos, vault…", content.title)
        assertEquals("12:00 copying a.jpg", content.text)
    }

    @Test
    fun `progress title says all pairs when the list is empty (sync all)`() {
        val content = progressNotificationContent(emptyList(), lastLogLine = null)
        assertEquals("Syncing all pairs…", content.title)
        assertEquals("Starting…", content.text)
    }

    // --- finalNotificationContent ----------------------------------------

    @Test
    fun `done job reports a summary in the title`() {
        val job = JobDto(
            id = "abc",
            pairs = listOf("photos"),
            state = "done",
            summaries = mapOf("photos" to SummaryDto(copiedToRemote = 3, deletedLocal = 1)),
        )
        val content = finalNotificationContent(job)
        assertEquals("Sync done: 3 copied, 1 deleted", content.title)
        assertEquals("photos", content.text)
    }

    @Test
    fun `failed job reports the error`() {
        val job = JobDto(id = "abc", pairs = listOf("vault"), state = "failed", error = "device disconnected")
        val content = finalNotificationContent(job)
        assertEquals("Sync failed", content.title)
        assertEquals("device disconnected", content.text)
    }

    @Test
    fun `cancelled job has its own title`() {
        val job = JobDto(id = "abc", pairs = listOf("vault"), state = "cancelled")
        val content = finalNotificationContent(job)
        assertEquals("Sync cancelled", content.title)
    }

    // --- summarizeTotals ----------------------------------------------------

    @Test
    fun `summarizeTotals adds every pair together and omits zero buckets`() {
        val summaries = mapOf(
            "photos" to SummaryDto(copiedToRemote = 2, copiedToLocal = 1),
            "vault" to SummaryDto(copiedToRemote = 1, conflicts = 1, errors = 2),
        )
        assertEquals("4 copied, 1 conflicts, 2 errors", summarizeTotals(summaries))
    }

    @Test
    fun `summarizeTotals reports no changes for an empty map`() {
        assertEquals("no changes", summarizeTotals(emptyMap()))
    }

    @Test
    fun `a sync stopped for confirmation says nothing was changed and gives the first reason`() {
        val job = JobDto(
            id = "j", pairs = listOf("vault"), state = "needs_confirmation",
            confirmation = mapOf("vault" to listOf("It would delete 785 files on the laptop (100% of the 785 files this pair knows).")),
        )
        val content = finalNotificationContent(job)
        assertEquals("Sync needs your OK, nothing was changed", content.title)
        assertEquals("It would delete 785 files on the laptop (100% of the 785 files this pair knows).", content.text)
    }
}
