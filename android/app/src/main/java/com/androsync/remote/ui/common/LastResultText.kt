package com.androsync.remote.ui.common

/**
 * Formats a pair's `last_result` string from `/v1/pairs` for display (task
 * 32c). The laptop writes `"FAILED: <message>"` when the most recent sync or
 * undo attempt for this pair failed (`daemon/jobs.py`'s `_mark_pair_failed`)
 * - without this, a failure was only visible in `daemon.log`, never in the
 * app, so a card could silently keep showing the last *successful* result.
 */
data class LastResultDisplay(val text: String, val isFailure: Boolean)

private const val FAILED_PREFIX = "FAILED:"

private const val WRONG_CONNECTION_START = "This pair works over USB or Wi-Fi debugging only"

/**
 * The first sentence of the laptop's wrong-connection message ("This pair works over USB or Wi-Fi debugging only, but
 * the phone is connected over Tailscale right now."), for places with little room; other texts come back unchanged.
 */
fun errorHeadline(error: String): String =
    if (error.startsWith(WRONG_CONNECTION_START)) error.substringBefore(". ").trimEnd('.') + "." else error

fun formatLastResult(lastResult: String?): LastResultDisplay? {
    if (lastResult.isNullOrBlank()) return null
    if (lastResult.startsWith(FAILED_PREFIX)) {
        return LastResultDisplay("Last run failed: ${lastResult.removePrefix(FAILED_PREFIX).trim()}", true)
    }
    return LastResultDisplay(lastResult, false)
}
