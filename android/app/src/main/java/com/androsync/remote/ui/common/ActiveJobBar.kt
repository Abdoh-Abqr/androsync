package com.androsync.remote.ui.common

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.androsync.remote.data.api.CurrentJobDto
import kotlinx.coroutines.delay

/** What the bar's buttons do; provided once by the navigation host so every screen's top bar can use them. */
data class JobActions(val onOpen: (String) -> Unit, val onStop: (String) -> Unit)

val LocalJobActions = staticCompositionLocalOf { JobActions(onOpen = {}, onStop = {}) }

/**
 * Strip under the top bar of every screen while a sync runs on the laptop, whoever started it (task 36b, issue #7):
 * what is running, how long, the latest log line, **Open** (the live log) and **Stop** (after a confirmation).
 */
@Composable
fun ActiveJobBar(job: CurrentJobDto, actions: JobActions = LocalJobActions.current) {
    var now by remember { mutableDoubleStateOf(System.currentTimeMillis() / 1000.0) }
    LaunchedEffect(job.id) {
        while (true) {
            now = System.currentTimeMillis() / 1000.0
            delay(1_000)
        }
    }
    val model = activeJobBarModel(job, now) ?: return
    var confirmStop by remember { mutableStateOf(false) }

    Surface(color = MaterialTheme.colorScheme.secondaryContainer, modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(model.title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (model.detail != null) {
                    Text(model.detail, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
            TextButton(onClick = { actions.onOpen(model.jobId) }) { Text("Open") }
            Button(
                onClick = { confirmStop = true },
                enabled = model.stopEnabled,
                colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error),
            ) { Text(model.stopLabel) }
        }
    }

    if (confirmStop) {
        AlertDialog(
            onDismissRequest = { confirmStop = false },
            title = { Text("Stop the sync?") },
            text = { Text("Files already copied stay copied. A file that was being copied is not left half-written.") },
            confirmButton = {
                TextButton(onClick = {
                    confirmStop = false
                    actions.onStop(model.jobId)
                }) { Text("Stop") }
            },
            dismissButton = { TextButton(onClick = { confirmStop = false }) { Text("Keep going") } },
        )
    }
}
