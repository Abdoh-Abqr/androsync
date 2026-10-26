package com.androsync.remote.ui.common

/**
 * Renders a pair's `direction` field from `/v1/pairs` (matching
 * `laptop/androsync/model.py`'s direction values: `"laptop-to-phone"`,
 * `"phone-to-laptop"`, `"two-way"`) as a fixed "laptop ? phone" arrow
 * string, so the two sides always appear in the same order and only the
 * arrow changes.
 */
/**
 * The direction with the computer's and the phone's names: "Ubuntu ↔ S24 FE", or "Ubuntu ↔ any phone" for a pair that
 * is not bound to one phone (Phase V2). An older laptop sends no name, so it reads "Laptop".
 */
fun directionText(direction: String, laptopName: String?, phoneName: String?): String {
    val left = laptopName?.takeIf { it.isNotBlank() } ?: "Laptop"
    val right = phoneName?.takeIf { it.isNotBlank() } ?: "any phone"
    return when (direction) {
        "laptop-to-phone" -> "$left → $right"
        "phone-to-laptop" -> "$left ← $right"
        "two-way" -> "$left ↔ $right"
        else -> direction
    }
}

fun directionArrowText(direction: String): String = when (direction) {
    "laptop-to-phone" -> "laptop → phone"
    "phone-to-laptop" -> "laptop ← phone"
    "two-way" -> "laptop ↔ phone"
    else -> direction
}
