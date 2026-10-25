package com.androsync.remote.ui.common

/**
 * Renders a pair's `direction` field from `/v1/pairs` (matching
 * `laptop/androsync/model.py`'s direction values: `"laptop-to-phone"`,
 * `"phone-to-laptop"`, `"two-way"`) as a fixed "laptop ? phone" arrow
 * string, so the two sides always appear in the same order and only the
 * arrow changes.
 */
fun directionArrowText(direction: String): String = when (direction) {
    "laptop-to-phone" -> "laptop → phone"
    "phone-to-laptop" -> "laptop ← phone"
    "two-way" -> "laptop ↔ phone"
    else -> direction
}
