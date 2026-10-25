package com.androsync.remote.widget

import com.androsync.remote.data.api.PairDto
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * What the home-screen widget shows (PLAN.md task 21: "last result line and
 * last run relative time"), cached in [com.androsync.remote.data.settings.SettingsRepository]
 * as JSON so the widget has something to draw offline, before its next
 * `/v1/pairs` refresh succeeds.
 */
@Serializable
data class WidgetState(
    val lastResultLine: String = "No syncs yet",
    /** Raw `last_run` ISO timestamp from `/v1/pairs`, rendered via `formatRelativeTime`. */
    val lastRunIso: String? = null,
)

private val json = Json { ignoreUnknownKeys = true }

fun encodeWidgetState(state: WidgetState): String = json.encodeToString(WidgetState.serializer(), state)

/** Never throws: an absent/corrupt cache just means the widget's default empty state. */
fun decodeWidgetState(raw: String?): WidgetState {
    if (raw.isNullOrBlank()) return WidgetState()
    return runCatching { json.decodeFromString(WidgetState.serializer(), raw) }.getOrDefault(WidgetState())
}

/**
 * Picks the most recently synced pair out of a `/v1/pairs` response to
 * summarize on the widget - there's room for only one result line, so the
 * freshest one wins.
 */
fun widgetStateFromPairs(pairs: List<PairDto>): WidgetState {
    val latest = pairs.filter { !it.lastRun.isNullOrBlank() }.maxByOrNull { it.lastRun!! }
    return WidgetState(
        lastResultLine = latest?.lastResult ?: "No syncs yet",
        lastRunIso = latest?.lastRun,
    )
}
