package com.androsync.remote.service

import com.androsync.remote.data.api.JobDto
import com.androsync.remote.data.api.SummaryDto

/**
 * Pure text-building for [SyncForegroundService]'s notifications (PLAN.md
 * task 20). Kept free of `android.app.Notification`/`Context` so it's plain
 * JVM/JUnit testable (see
 * `app/src/test/java/com/androsync/remote/service/SyncNotificationsTest.kt`);
 * the service turns this into an actual `NotificationCompat.Builder`.
 */
data class NotificationContent(val title: String, val text: String)

/** The persistent notification shown while a job is running. */
fun progressNotificationContent(pairs: List<String>, lastLogLine: String?): NotificationContent {
    val title = if (pairs.isEmpty()) "Syncing all pairs…" else "Syncing ${pairs.joinToString(", ")}…"
    return NotificationContent(title = title, text = lastLogLine ?: "Starting…")
}

/**
 * The one-shot notification posted when a job reaches a terminal state
 * (PLAN.md task 20: `"Sync done: <summary>"` or `"Sync failed"`).
 */
fun finalNotificationContent(job: JobDto): NotificationContent = when (job.state) {
    "done" -> NotificationContent(
        title = "Sync done: ${summarizeTotals(job.summaries)}",
        text = job.pairs.joinToString(", "),
    )
    "cancelled" -> NotificationContent(title = "Sync cancelled", text = job.pairs.joinToString(", "))
    "needs_confirmation" -> NotificationContent(
        title = "Sync needs your OK, nothing was changed",
        text = job.confirmation.values.flatten().firstOrNull() ?: job.pairs.joinToString(", "),
    )
    else -> NotificationContent(title = "Sync failed", text = job.error ?: job.pairs.joinToString(", "))
}

/** Adds every pair's [SummaryDto] together into one short "N copied, N deleted, ..." line. */
fun summarizeTotals(summaries: Map<String, SummaryDto>): String {
    if (summaries.isEmpty()) return "no changes"
    var copied = 0
    var deleted = 0
    var conflicts = 0
    var errors = 0
    for (s in summaries.values) {
        copied += s.copiedToLocal + s.copiedToRemote
        deleted += s.deletedLocal + s.deletedRemote
        conflicts += s.conflicts
        errors += s.errors
    }
    return buildString {
        append("$copied copied")
        if (deleted > 0) append(", $deleted deleted")
        if (conflicts > 0) append(", $conflicts conflicts")
        if (errors > 0) append(", $errors errors")
    }
}
