package com.androsync.remote.ui.setup

/** Gap between two "USB debugging is off" notifications, so a flaky cable does not nag. */
const val USB_HINT_MIN_GAP_MS = 30_000L

/**
 * True when the app should post the "USB connected but USB debugging is off" notification (task 37f, issue #10):
 * only on the moment the cable goes in ([wasConnected] false to [usbConnected] true), only when debugging is off,
 * only if the user left the Settings switch on, and not twice within [USB_HINT_MIN_GAP_MS].
 */
fun shouldNudgeAboutDebugging(
    nudgesEnabled: Boolean,
    usbConnected: Boolean,
    adbEnabled: Boolean,
    wasConnected: Boolean,
    nowMs: Long,
    lastNudgeMs: Long?,
    minGapMs: Long = USB_HINT_MIN_GAP_MS,
): Boolean {
    if (!nudgesEnabled || !usbConnected || wasConnected || adbEnabled) return false
    return lastNudgeMs == null || nowMs - lastNudgeMs >= minGapMs
}
