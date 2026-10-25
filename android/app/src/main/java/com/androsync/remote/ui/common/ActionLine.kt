package com.androsync.remote.ui.common

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp

/**
 * One action of a preview or sync log, split into what happens and where the file is on each side.
 *
 * The laptop writes every action as a single line:
 * `x  phone  notes/a.md   deleted on laptop, so removed from the phone   (laptop: /home/me/notes/a.md  ·  phone: /sdcard/Notes/notes/a.md)`
 * (`PATHS_SEPARATOR` in `executor.py`). Older daemons send lines without the paths; those parse with null paths.
 */
data class ActionLine(val head: String, val laptopPath: String?, val phonePath: String?)

private val ACTION_LINE = Regex("""^(.*?)\s{3}\(laptop: (.+?) {2}· {2}phone: (.+)\)$""")

/** Splits [line] into its description and the two full paths; never fails (an unknown shape is just a head). */
fun parseActionLine(line: String): ActionLine {
    val match = ACTION_LINE.matchEntire(line) ?: return ActionLine(head = line, laptopPath = null, phonePath = null)
    val (head, laptop, phone) = match.destructured
    return ActionLine(head = head, laptopPath = laptop, phonePath = phone)
}

/** The action on one line, then the laptop path and the phone path each on their own line. */
@Composable
fun ActionLineItem(line: String) {
    val parsed = parseActionLine(line)
    Column(Modifier.padding(vertical = 4.dp)) {
        Text(parsed.head, style = MaterialTheme.typography.bodySmall)
        if (parsed.laptopPath != null && parsed.phonePath != null) {
            Text("laptop: ${parsed.laptopPath}", style = MaterialTheme.typography.labelSmall, fontFamily = FontFamily.Monospace, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text("phone:  ${parsed.phonePath}", style = MaterialTheme.typography.labelSmall, fontFamily = FontFamily.Monospace, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}
