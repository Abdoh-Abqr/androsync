package com.androsync.remote.ui.common

import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneOffset
import java.time.temporal.ChronoUnit

/**
 * Formats a `last_run` timestamp from `/v1/pairs` (Python's naive
 * `datetime.isoformat()`, e.g. `"2026-09-01T12:03:41.512309"`, no timezone
 * suffix - the daemon runs on the same laptop clock the app compares
 * against, so it's treated as UTC-equivalent local wall time) into a short
 * relative string: "just now", "12 min ago", "3 hr ago", "5 days ago".
 *
 * Returns null when [isoTimestamp] is null/blank/unparsable - the daemon
 * sends `last_run: null` until a pair has synced at least once.
 */
fun formatRelativeTime(isoTimestamp: String?, now: Instant = Instant.now()): String? {
    if (isoTimestamp.isNullOrBlank()) return null
    val then = parseIsoTimestamp(isoTimestamp) ?: return null
    val seconds = ChronoUnit.SECONDS.between(then, now).coerceAtLeast(0)
    return when {
        seconds < 60 -> "just now"
        seconds < 3600 -> "${seconds / 60} min ago"
        seconds < 86_400 -> "${seconds / 3600} hr ago"
        else -> "${seconds / 86_400} days ago"
    }
}

private fun parseIsoTimestamp(raw: String): Instant? = runCatching {
    if (raw.endsWith("Z")) {
        Instant.parse(raw)
    } else {
        LocalDateTime.parse(raw).toInstant(ZoneOffset.UTC)
    }
}.getOrNull()
