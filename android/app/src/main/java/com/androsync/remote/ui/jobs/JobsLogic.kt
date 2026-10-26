package com.androsync.remote.ui.jobs

import com.androsync.remote.ui.common.errorHeadline
import com.androsync.remote.data.api.JobDto
import com.androsync.remote.ui.common.formatElapsed

/**
 * Pure text for the "Recent syncs" list (task 36b, issue #7). Job times from the daemon are epoch seconds.
 */
fun jobRowTitle(job: JobDto): String {
    val what = when {
        job.kind == "undo" -> "Undo"
        job.dryRun -> "Preview"
        else -> "Sync"
    }
    val pairs = if (job.pairs.isEmpty()) "all pairs" else job.pairs.joinToString(", ")
    return "$what $pairs"
}

/** "5 min ago" style age of a job, from its creation time. */
fun jobAge(createdEpochSeconds: Double, nowEpochSeconds: Double): String {
    val seconds = (nowEpochSeconds - createdEpochSeconds).toLong().coerceAtLeast(0)
    return when {
        seconds < 60 -> "just now"
        seconds < 3600 -> "${seconds / 60} min ago"
        seconds < 86_400 -> "${seconds / 3600} hr ago"
        else -> "${seconds / 86_400} days ago"
    }
}

/** One line of outcome: counts for a finished sync, the error for a failed one, a hint for a running one. */
fun jobRowSummary(job: JobDto): String {
    if (job.state == "running" || job.state == "queued") return "running"
    if (job.state == "cancelling") return "stopping"
    val parts = mutableListOf<String>()
    val toPhone = job.summaries.values.sumOf { it.copiedToRemote }
    val toLaptop = job.summaries.values.sumOf { it.copiedToLocal }
    val deleted = job.summaries.values.sumOf { it.deletedLocal + it.deletedRemote }
    val errors = job.summaries.values.sumOf { it.errors }
    if (toPhone > 0) parts += "$toPhone to phone"
    if (toLaptop > 0) parts += "$toLaptop to laptop"
    if (deleted > 0) parts += "$deleted deleted"
    if (errors > 0) parts += "$errors errors"
    if (parts.isEmpty() && job.error == null) parts += "nothing changed"
    val started = job.started
    val finished = job.finished
    if (started != null && finished != null) parts += formatElapsed((finished - started).toLong())
    job.error?.let { parts += errorHeadline(it).take(80) }
    return parts.joinToString(" · ")
}
