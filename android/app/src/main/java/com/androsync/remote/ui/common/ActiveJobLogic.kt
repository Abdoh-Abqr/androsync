package com.androsync.remote.ui.common

import com.androsync.remote.data.api.CurrentJobDto

/**
 * Pure logic behind the "sync running" bar (task 36b, issue #7): what it says and whether Stop is available.
 * No Compose or Android types, so it is plain-JUnit testable (see ActiveJobLogicTest).
 */
data class ActiveJobBarModel(
    val jobId: String,
    /** e.g. "Syncing vault · 0:42" */
    val title: String,
    /** The latest log line, or null before the first one. */
    val detail: String?,
    val stopEnabled: Boolean,
    val stopLabel: String,
)

/** "0:07", "12:03", "1:02:03" for a duration in seconds (negative values count as zero). */
fun formatElapsed(totalSeconds: Long): String {
    val s = totalSeconds.coerceAtLeast(0)
    val hours = s / 3600
    val minutes = (s % 3600) / 60
    val seconds = s % 60
    return if (hours > 0) "%d:%02d:%02d".format(hours, minutes, seconds) else "%d:%02d".format(minutes, seconds)
}

private fun pairsText(pairs: List<String>): String = if (pairs.isEmpty()) "all pairs" else pairs.joinToString(", ")

/** Model for the bar, or null when no job is running. [nowEpochSeconds] is injected so tests are deterministic. */
fun activeJobBarModel(job: CurrentJobDto?, nowEpochSeconds: Double): ActiveJobBarModel? {
    if (job == null) return null
    val verb = when {
        job.kind == "undo" -> "Undoing"
        job.dryRun -> "Previewing"
        else -> "Syncing"
    }
    val elapsed = job.started?.let { " · " + formatElapsed((nowEpochSeconds - it).toLong()) }.orEmpty()
    val stopping = job.state == "cancelling"
    return ActiveJobBarModel(
        jobId = job.id,
        title = "$verb ${pairsText(job.pairs)}$elapsed",
        detail = job.lastLine?.takeIf { it.isNotBlank() },
        stopEnabled = !stopping,
        stopLabel = if (stopping) "Stopping..." else "Stop",
    )
}
