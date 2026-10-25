package com.androsync.remote.ui.job

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.androsync.remote.data.api.JobDto
import com.androsync.remote.ui.ConnectionViewModel
import com.androsync.remote.ui.common.AndroSyncTopBar
import com.androsync.remote.ui.common.ConnectionState
import com.androsync.remote.ui.rememberJobViewModel

/**
 * Live view of one sync job (PLAN.md task 20): pair names, a state chip,
 * per-pair summary rows once the job reports them, an auto-scrolling
 * monospace log, Cancel while it's active, and "Back to pairs" once it's
 * finished.
 */
@Composable
fun JobScreen(
    jobId: String,
    connectionState: ConnectionState,
    connectionViewModel: ConnectionViewModel,
    onBack: () -> Unit,
    onNavigateToJob: (String) -> Unit = {},
    viewModel: JobViewModel = rememberJobViewModel(jobId, connectionViewModel),
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(viewModel) {
        viewModel.events.collect { event ->
            when (event) {
                is JobEvent.UndoStarted -> onNavigateToJob(event.jobId)
                is JobEvent.ConfirmStarted -> onNavigateToJob(event.jobId)
                is JobEvent.Error -> snackbarHostState.showSnackbar(event.message)
            }
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            AndroSyncTopBar(title = "Job", connectionState = connectionState, onBack = onBack, showActiveJob = false)
        },
    ) { padding ->
        val job = uiState.job
        Column(modifier = Modifier.fillMaxSize().padding(padding).padding(16.dp)) {
            when {
                job == null && uiState.error != null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text(uiState.error!!, color = MaterialTheme.colorScheme.error)
                }

                job == null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }

                else -> JobContent(
                    job = job,
                    logLines = uiState.logLines,
                    onCancel = viewModel::cancel,
                    onBackToPairs = onBack,
                    onUndo = viewModel::undo,
                    onConfirmLarge = viewModel::confirmLarge,
                )
            }
        }
    }
}

@Composable
private fun ColumnScope.JobContent(
    job: JobDto,
    logLines: List<String>,
    onCancel: () -> Unit,
    onBackToPairs: () -> Unit,
    onUndo: (pairName: String, runId: String?, pause: Boolean) -> Unit,
    onConfirmLarge: (pairNames: List<String>) -> Unit,
) {
    val terminal = isTerminalState(job.state)
    var undoTarget by remember { mutableStateOf<UndoTarget?>(null) }

    Text(
        text = if (job.pairs.isEmpty()) "All pairs" else job.pairs.joinToString(", "),
        style = MaterialTheme.typography.titleLarge,
    )
    Spacer(Modifier.height(4.dp))
    JobStateChip(job.state)
    Spacer(Modifier.height(12.dp))

    if (job.kind == "undo") {
        job.undoSummary?.let { summary ->
            val pairName = job.pairs.firstOrNull().orEmpty()
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
                Text(
                    text = formatUndoSummary(summary),
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.weight(1f),
                )
                if (terminal && summary.runId != null) {
                    TextButton(onClick = { undoTarget = UndoTarget(pairName, summary.runId) }) { Text("Undo this") }
                }
            }
            Spacer(Modifier.height(12.dp))
        }
    } else if (job.summaries.isNotEmpty()) {
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            job.summaries.forEach { (pairName, summary) ->
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
                    Text(
                        text = formatSummaryRow(summaryRowFor(pairName, summary)),
                        style = MaterialTheme.typography.bodyMedium,
                        modifier = Modifier.weight(1f),
                    )
                    if (terminal && summary.runId != null) {
                        TextButton(onClick = { undoTarget = UndoTarget(pairName, summary.runId) }) { Text("Undo") }
                    }
                }
            }
        }
        Spacer(Modifier.height(12.dp))
    }

    job.error?.let {
        Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium)
        Spacer(Modifier.height(8.dp))
    }

    if (job.state == "needs_confirmation" && job.confirmation.isNotEmpty()) {
        ConfirmationCard(job.confirmation, onGoAhead = { onConfirmLarge(job.confirmation.keys.toList()) }, onNotNow = onBackToPairs)
        Spacer(Modifier.height(12.dp))
    }

    Text("Log", style = MaterialTheme.typography.titleMedium)
    Spacer(Modifier.height(4.dp))
    LogView(logLines, modifier = Modifier.weight(1f).fillMaxWidth())
    Spacer(Modifier.height(12.dp))

    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
        if (!terminal) {
            // The daemon moves a job to "cancelling" the moment Stop is pressed and kills the running adb call.
            OutlinedButton(onClick = onCancel, enabled = job.state != "cancelling") {
                Text(if (job.state == "cancelling") "Stopping..." else "Stop")
            }
        } else {
            Button(onClick = onBackToPairs) { Text("Back to pairs") }
        }
    }

    undoTarget?.let { target ->
        UndoConfirmDialog(
            pairName = target.pairName,
            onConfirm = { pause ->
                undoTarget = null
                onUndo(target.pairName, target.runId, pause)
            },
            onDismiss = { undoTarget = null },
        )
    }
}

private data class UndoTarget(val pairName: String, val runId: String?)

/** Confirms undoing one pair's run (task 30b), with an optional "pause the pair afterwards". */
@Composable
private fun UndoConfirmDialog(pairName: String, onConfirm: (pause: Boolean) -> Unit, onDismiss: () -> Unit) {
    var pause by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Undo sync for \"$pairName\"?") },
        text = {
            Column {
                Text("Puts both sides back exactly how they were before this run.")
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 8.dp)) {
                    Checkbox(checked = pause, onCheckedChange = { pause = it })
                    Text("Pause the pair afterwards")
                }
            }
        },
        confirmButton = { TextButton(onClick = { onConfirm(pause) }) { Text("Undo") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

/** Monospace, auto-scrolling log (PLAN.md task 20): sticks to the bottom unless the user scrolled up. */
@Composable
private fun LogView(lines: List<String>, modifier: Modifier = Modifier) {
    val listState = rememberLazyListState()
    var stickToBottom by remember { mutableStateOf(true) }

    val isAtBottom by remember {
        derivedStateOf {
            val layoutInfo = listState.layoutInfo
            val lastVisible = layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0
            shouldStickToBottom(lastVisible, layoutInfo.totalItemsCount)
        }
    }
    LaunchedEffect(listState) {
        snapshotFlow { isAtBottom }.collect { stickToBottom = it }
    }

    LaunchedEffect(lines.size) {
        if (stickToBottom && lines.isNotEmpty()) {
            listState.scrollToItem(lines.size - 1)
        }
    }

    LazyColumn(
        state = listState,
        modifier = modifier
            .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(8.dp))
            .padding(8.dp),
        contentPadding = PaddingValues(4.dp),
    ) {
        items(lines) { line ->
            Text(text = line, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun JobStateChip(state: String) {
    val chip = jobChipFor(state)
    val color = when (chip.color) {
        JobChipColor.QUEUED -> MaterialTheme.colorScheme.secondary
        JobChipColor.RUNNING -> MaterialTheme.colorScheme.primary
        JobChipColor.STOPPING -> MaterialTheme.colorScheme.tertiary
        JobChipColor.DONE -> Color(0xFF2E7D32)
        JobChipColor.FAILED -> MaterialTheme.colorScheme.error
        JobChipColor.CANCELLED -> MaterialTheme.colorScheme.outline
        JobChipColor.NEEDS_OK -> MaterialTheme.colorScheme.tertiary
    }
    Box(
        modifier = Modifier
            .background(color, RoundedCornerShape(50))
            .padding(horizontal = 12.dp, vertical = 4.dp),
    ) {
        Text(chip.label, color = Color.White, style = MaterialTheme.typography.labelMedium)
    }
}

/**
 * Shown when the laptop stopped a sync before changing anything because it would delete or copy a lot (task 36d,
 * issue #9): the reasons per pair, then "Go ahead" (runs it again, confirmed) or "Not now".
 */
@Composable
private fun ConfirmationCard(confirmation: Map<String, List<String>>, onGoAhead: () -> Unit, onNotNow: () -> Unit) {
    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("Nothing was changed. This sync needs your OK.", style = MaterialTheme.typography.titleSmall)
            confirmation.forEach { (pair, reasons) ->
                reasons.forEach { reason -> Text("$pair: $reason", style = MaterialTheme.typography.bodyMedium) }
            }
            Text(
                "Check the list in the log below. If it is what you expect, go ahead; every file removed can be brought back with Undo.",
                style = MaterialTheme.typography.bodySmall,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = onGoAhead) { Text("Go ahead") }
                OutlinedButton(onClick = onNotNow) { Text("Not now") }
            }
        }
    }
}
