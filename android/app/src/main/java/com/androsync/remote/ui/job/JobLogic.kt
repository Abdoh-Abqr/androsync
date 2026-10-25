package com.androsync.remote.ui.job

import com.androsync.remote.data.api.SummaryDto

/**
 * Pure helpers backing [JobViewModel] / [JobScreen] (PLAN.md task 20). Kept
 * free of Compose and Android framework types so they're plain-JUnit
 * testable (see `app/src/test/java/com/androsync/remote/ui/job/JobLogicTest.kt`).
 */

/** The job states the daemon treats as terminal (`daemon/jobs.py`'s `_ACTIVE_STATES` complement). */
private val TERMINAL_STATES = setOf("done", "failed", "cancelled", "needs_confirmation")

fun isTerminalState(state: String?): Boolean = state in TERMINAL_STATES

/** Cap for the in-memory log (PLAN.md task 20: "keeps a list of log lines (cap 2000)"). */
const val LOG_LINE_CAP = 2000

/** Ring-buffer append: keeps at most [cap] most-recent lines. */
fun appendLogLine(lines: List<String>, newLine: String, cap: Int = LOG_LINE_CAP): List<String> {
    val updated = lines + newLine
    return if (updated.size > cap) updated.takeLast(cap) else updated
}

/**
 * Whether the log list should auto-scroll to the newly appended last line:
 * true unless the user had scrolled away from the bottom before the new
 * line arrived (PLAN.md task 20: "sticks to bottom unless the user scrolled
 * up"). [lastVisibleItemIndex] and [totalItemCountBeforeAppend] come from
 * the `LazyListState`'s layout info captured just before the new line(s)
 * land.
 */
fun shouldStickToBottom(lastVisibleItemIndex: Int, totalItemCountBeforeAppend: Int): Boolean =
    totalItemCountBeforeAppend == 0 || lastVisibleItemIndex >= totalItemCountBeforeAppend - 1

/** Colour bucket for [JobChip] - the composable maps this to a `MaterialTheme` colour. */
enum class JobChipColor { QUEUED, RUNNING, STOPPING, DONE, FAILED, CANCELLED, NEEDS_OK }

data class JobChip(val label: String, val color: JobChipColor)

/** Maps a job's `state` (from `JobDto.state`) to a chip label + colour (PLAN.md task 20). */
fun jobChipFor(state: String): JobChip = when (state) {
    "queued" -> JobChip("Queued", JobChipColor.QUEUED)
    "running" -> JobChip("Running", JobChipColor.RUNNING)
    "cancelling" -> JobChip("Stopping...", JobChipColor.STOPPING)
    "needs_confirmation" -> JobChip("Needs your OK", JobChipColor.NEEDS_OK)
    "done" -> JobChip("Done", JobChipColor.DONE)
    "failed" -> JobChip("Failed", JobChipColor.FAILED)
    "cancelled" -> JobChip("Cancelled", JobChipColor.CANCELLED)
    else -> JobChip(state.replaceFirstChar { it.uppercase() }, JobChipColor.QUEUED)
}

/** One pair's row in the job summary (PLAN.md task 20: "copied to phone / to laptop / deleted / conflicts / errors"). */
data class PairSummaryRow(
    val pairName: String,
    val copiedToPhone: Int,
    val copiedToLaptop: Int,
    val deleted: Int,
    val conflicts: Int,
    val errors: Int,
)

/** Builds a [PairSummaryRow] from one entry of `JobDto.summaries`. */
fun summaryRowFor(pairName: String, summary: SummaryDto): PairSummaryRow = PairSummaryRow(
    pairName = pairName,
    copiedToPhone = summary.copiedToRemote,
    copiedToLaptop = summary.copiedToLocal,
    deleted = summary.deletedLocal + summary.deletedRemote,
    conflicts = summary.conflicts,
    errors = summary.errors,
)

/** One-line text rendering of a [PairSummaryRow], used by both the job screen and the notification. */
fun formatSummaryRow(row: PairSummaryRow): String =
    "${row.pairName}: ${row.copiedToPhone} to phone, ${row.copiedToLaptop} to laptop, " +
        "${row.deleted} deleted, ${row.conflicts} conflicts, ${row.errors} errors"

/** One-line text rendering of an undo job's outcome (task 30b), from `JobDto.undoSummary`. */
fun formatUndoSummary(summary: SummaryDto): String {
    val skippedNote = if (summary.skipped.isEmpty()) "" else ", ${summary.skipped.size} skipped"
    return "Restored ${summary.restoredLocal} on laptop, ${summary.restoredRemote} on phone$skippedNote"
}
