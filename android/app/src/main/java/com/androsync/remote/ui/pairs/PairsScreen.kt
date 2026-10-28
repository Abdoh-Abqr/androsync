package com.androsync.remote.ui.pairs

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Switch
import androidx.compose.material3.FilterChip
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Settings
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.androsync.remote.data.api.ConflictVersionDto
import com.androsync.remote.data.api.ConflictsCleanResponse
import com.androsync.remote.data.api.PairDto
import com.androsync.remote.data.api.RunDto
import com.androsync.remote.data.api.StatusDto
import com.androsync.remote.ui.ConnectionViewModel
import com.androsync.remote.ui.common.ActionLineItem
import com.androsync.remote.ui.common.AndroSyncTopBar
import com.androsync.remote.ui.common.ConnectionState
import com.androsync.remote.ui.setup.DeviceReadiness
import com.androsync.remote.ui.setup.deviceReadiness
import com.androsync.remote.ui.setup.phoneProblemLead
import com.androsync.remote.ui.common.SyncError
import com.androsync.remote.ui.common.directionText
import com.androsync.remote.ui.common.formatLastResult
import com.androsync.remote.ui.common.formatRelativeTime
import com.androsync.remote.ui.common.snackbarMessageFor
import com.androsync.remote.ui.rememberPairsViewModel
import kotlinx.coroutines.launch

/**
 * Lists configured sync pairs (PLAN.md task 19): pull-to-refresh, per-pair
 * Preview/Sync, a top-bar "Sync all", and a settings shortcut. Sync/Preview
 * disable themselves while [ConnectionState] is [ConnectionState.Unreachable].
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PairsScreen(
    connectionViewModel: ConnectionViewModel,
    onNavigateToJob: (String) -> Unit,
    onNavigateToSetup: () -> Unit,
    onNavigateToSettings: () -> Unit,
    onNavigateToJobs: () -> Unit = {},
    onNavigateToConnect: () -> Unit = {},
    onNavigateToAddPair: () -> Unit,
    onNavigateToEditPair: (String) -> Unit,
    viewModel: PairsViewModel = rememberPairsViewModel(connectionViewModel),
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val connectionState by viewModel.connectionState.collectAsStateWithLifecycle()
    val previewState by viewModel.previewState.collectAsStateWithLifecycle()
    val conflictsSheetState by viewModel.conflictsSheetState.collectAsStateWithLifecycle()
    val runsSheetState by viewModel.runsSheetState.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }

    // PLAN.md task 20: request POST_NOTIFICATIONS (Android 13+) the first time a sync
    // is started, so SyncForegroundService's progress notification can actually show.
    // The sync itself proceeds either way - a denied permission only means no notification.
    val context = LocalContext.current
    val notificationPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { /* no-op: the sync already started regardless of the user's answer */ }
    val requestNotificationPermissionIfNeeded = {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val granted = ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.POST_NOTIFICATIONS,
            ) == PackageManager.PERMISSION_GRANTED
            if (!granted) notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }
    val syncAll = {
        requestNotificationPermissionIfNeeded()
        viewModel.sync(null)
    }
    val syncPair = { name: String ->
        requestNotificationPermissionIfNeeded()
        viewModel.sync(name)
    }

    LaunchedEffect(viewModel) {
        viewModel.events.collect { event ->
            when (event) {
                is PairsEvent.JobStarted -> onNavigateToJob(event.jobId)
                is PairsEvent.Error -> handlePairsError(
                    error = event.error,
                    snackbarHostState = snackbarHostState,
                    onNavigateToSetup = onNavigateToSetup,
                    onNavigateToJob = onNavigateToJob,
                )
            }
        }
    }

    val syncEnabled = connectionState is ConnectionState.Connected
    val laptops by connectionViewModel.laptops.collectAsStateWithLifecycle()
    val selectedLaptop by connectionViewModel.selectedLaptopId.collectAsStateWithLifecycle()
    // Task 33a: with pairs bound to different phones, a row of chips narrows the list to one phone's pairs plus the
    // "any phone" pairs. Null = show everything.
    val devices = (connectionState as? ConnectionState.Connected)?.devices.orEmpty()
    val laptopName = (connectionState as? ConnectionState.Connected)?.laptopName
    var phoneFilter by rememberSaveable { mutableStateOf<String?>(null) }
    val phonesInUse = phoneChoices(
        devices,
        alsoKeep = uiState.pairs.map { it.device },
        agents = (connectionState as? ConnectionState.Connected)?.agents.orEmpty(),
    )

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            AndroSyncTopBar(
                title = "Pairs",
                connectionState = connectionState,
                // Preview all and Sync all moved out of the bar: four actions plus the connection badge did not fit
                // a narrower phone (the title wrapped). They are a row of buttons under the bar now.
                actions = {
                    IconButton(onClick = onNavigateToJobs) {
                        Icon(Icons.AutoMirrored.Filled.List, contentDescription = "Recent syncs")
                    }
                    IconButton(onClick = onNavigateToSettings) {
                        Icon(Icons.Filled.Settings, contentDescription = "Settings")
                    }
                },
            )
        },
        floatingActionButton = {
            FloatingActionButton(onClick = onNavigateToAddPair) {
                Icon(Icons.Filled.Add, contentDescription = "Add pair")
            }
        },
    ) { padding ->
        Column(modifier = Modifier.fillMaxSize().padding(padding)) {
            // Task 33c: with more than one laptop saved, switch between them right here.
            if (laptops.size > 1) {
                Row(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    laptops.forEach { laptop ->
                        FilterChip(
                            selected = laptop.id == selectedLaptop,
                            onClick = { connectionViewModel.selectLaptop(laptop.id) },
                            label = { Text(laptop.name) },
                        )
                    }
                }
            }
            Row(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedButton(onClick = viewModel::previewAll, enabled = syncEnabled, modifier = Modifier.weight(1f)) {
                    Text("Preview all", maxLines = 1)
                }
                Button(onClick = syncAll, enabled = syncEnabled, modifier = Modifier.weight(1f)) {
                    Text("Sync all", maxLines = 1)
                }
            }
            if (!syncEnabled) {
                Text(
                    text = "Laptop not reachable — sync disabled",
                    modifier = Modifier.fillMaxWidth().padding(12.dp),
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                )
            }

            // Task 37f: the laptop answers but adb sees no usable phone, so say what to do about it.
            val phoneProblem = (connectionState as? ConnectionState.Connected)?.let {
                deviceReadiness(true, it.devices) != DeviceReadiness.READY
            } ?: false
            val lead = (connectionState as? ConnectionState.Connected)?.let { phoneProblemLead(it.via, it.agentConnected) }
            if (phoneProblem && lead != null) {
                Text(
                    text = lead,
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp),
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            if (phoneProblem || !syncEnabled) {
                TextButton(onClick = onNavigateToConnect, modifier = Modifier.padding(horizontal = 4.dp)) {
                    Text(if (phoneProblem) "Phone not detected \u2014 fix it" else "Help me connect my phone")
                }
            }

            PullToRefreshBox(
                isRefreshing = uiState.refreshing,
                onRefresh = { viewModel.refresh() },
                modifier = Modifier.fillMaxSize(),
            ) {
                when {
                    uiState.loading -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        CircularProgressIndicator()
                    }

                    uiState.pairs.isEmpty() -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text(uiState.error ?: "No pairs configured")
                    }

                    else -> LazyColumn(
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(12.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        if (phonesInUse.size > 1) {
                            item(key = "phone-filter") {
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    FilterChip(
                                        selected = phoneFilter == null,
                                        onClick = { phoneFilter = null },
                                        label = { Text("All phones") },
                                    )
                                    phonesInUse.forEach { phone ->
                                        FilterChip(
                                            selected = phoneFilter == phone.id,
                                            onClick = { phoneFilter = phone.id },
                                            label = { Text(phone.label) },
                                        )
                                    }
                                }
                            }
                        }
                        items(filterPairsForPhone(uiState.pairs, phoneFilter), key = { it.name }) { pair ->
                            PairCard(
                                                phoneLabel = pair.deviceName ?: phoneLabel(pair.device, devices),
                                laptopName = laptopName,
                                pair = pair,
                                syncEnabled = syncEnabled,
                                pendingCount = uiState.pendingCounts[pair.name] ?: 0,
                                conflictCount = uiState.conflictCounts[pair.name] ?: 0,
                                onPreview = { viewModel.preview(pair.name) },
                                onSync = { syncPair(pair.name) },
                                onEdit = { onNavigateToEditPair(pair.name) },
                                onDelete = { purgeState -> viewModel.deletePair(pair.name, purgeState) },
                                onOpenConflicts = { viewModel.openConflicts(pair.name) },
                                onOpenRuns = { viewModel.openRuns(pair.name) },
                                onEnable = { viewModel.enablePair(pair) },
                            )
                        }
                    }
                }
            }
        }
    }

    if (previewState != null) {
        PreviewBottomSheet(state = previewState, onDismiss = viewModel::dismissPreview)
    }

    if (conflictsSheetState != null) {
        ConflictsBottomSheet(
            state = conflictsSheetState,
            onDismiss = viewModel::dismissConflicts,
            onRestore = viewModel::restoreConflictVersion,
            onPreviewClean = viewModel::previewCleanDuplicates,
            onCancelCleanPreview = viewModel::cancelCleanPreview,
            onConfirmClean = viewModel::confirmCleanDuplicates,
        )
    }

    if (runsSheetState != null) {
        RunsBottomSheet(
            state = runsSheetState,
            onDismiss = viewModel::dismissRuns,
            onUndo = viewModel::undoRun,
        )
    }
}

/** Maps a [PairsEvent.Error] to the snackbar text/action PLAN.md task 19 specifies. */
private suspend fun handlePairsError(
    error: SyncError,
    snackbarHostState: SnackbarHostState,
    onNavigateToSetup: () -> Unit,
    onNavigateToJob: (String) -> Unit,
) {
    when (error) {
        SyncError.TokenRejected -> {
            val result = snackbarHostState.showSnackbar(
                message = snackbarMessageFor(error),
                actionLabel = "Setup",
            )
            if (result == SnackbarResult.ActionPerformed) onNavigateToSetup()
        }

        is SyncError.Busy -> {
            val jobId = error.jobId
            val result = snackbarHostState.showSnackbar(
                message = snackbarMessageFor(error),
                actionLabel = if (jobId != null) "Open" else null,
            )
            if (result == SnackbarResult.ActionPerformed && jobId != null) onNavigateToJob(jobId)
        }

        is SyncError.Connection -> {
            snackbarHostState.showSnackbar(message = snackbarMessageFor(error))
        }
    }
}

@Composable
private fun PairCard(
    phoneLabel: String? = null,
    laptopName: String? = null,
    pair: PairDto,
    syncEnabled: Boolean,
    pendingCount: Int,
    conflictCount: Int,
    onPreview: () -> Unit,
    onSync: () -> Unit,
    onEdit: () -> Unit,
    onDelete: (purgeState: Boolean) -> Unit,
    onOpenConflicts: () -> Unit,
    onOpenRuns: () -> Unit,
    onEnable: () -> Unit,
) {
    var menuExpanded by remember { mutableStateOf(false) }
    var showDeleteDialog by remember { mutableStateOf(false) }
    val paused = !pair.enabled

    ElevatedCard(modifier = Modifier.fillMaxWidth()) {
        Column(
            Modifier
                .padding(16.dp)
                .let { if (paused) it.alpha(0.6f) else it },
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(pair.name, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                if (paused) {
                    Text(
                        "Paused",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.padding(end = 8.dp),
                    )
                    Switch(checked = false, onCheckedChange = { onEnable() })
                }
                Box {
                    IconButton(onClick = { menuExpanded = true }) {
                        Icon(Icons.Filled.MoreVert, contentDescription = "Pair options")
                    }
                    DropdownMenu(expanded = menuExpanded, onDismissRequest = { menuExpanded = false }) {
                        DropdownMenuItem(text = { Text("Edit") }, onClick = { menuExpanded = false; onEdit() })
                        DropdownMenuItem(
                            text = { Text("Undo last sync…") },
                            onClick = { menuExpanded = false; onOpenRuns() },
                        )
                        DropdownMenuItem(
                            text = { Text("Delete") },
                            onClick = { menuExpanded = false; showDeleteDialog = true },
                        )
                    }
                }
            }
            Text(
                directionText(pair.direction, laptopName, phoneLabel) +
                    (if (pair.transport == "agent") "  ·  via this app" else "") +
                    autoBadge(pair),
                style = MaterialTheme.typography.bodyMedium,
            )
            Text(
                text = pair.local,
                style = MaterialTheme.typography.bodySmall,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                text = pair.remote,
                style = MaterialTheme.typography.bodySmall,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            val relative = formatRelativeTime(pair.lastRun)
            if (relative != null) {
                Text("Last run: $relative", style = MaterialTheme.typography.bodySmall)
            }
            formatLastResult(pair.lastResult)?.let { result ->
                Text(
                    text = result.text,
                    style = MaterialTheme.typography.bodySmall,
                    color = if (result.isFailure) MaterialTheme.colorScheme.error else Color.Unspecified,
                    maxLines = if (result.isFailure) 4 else 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            if (pendingCount > 0) {
                Text(
                    text = "$pendingCount conflict${if (pendingCount == 1) "" else "s"} waiting",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
            if (conflictCount > 0) {
                Text(
                    text = "$conflictCount saved conflict version${if (conflictCount == 1) "" else "s"}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.clickable(onClick = onOpenConflicts),
                )
            }
            Row(
                modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                horizontalArrangement = Arrangement.End,
            ) {
                TextButton(onClick = onPreview) { Text("Preview") }
                Spacer(Modifier.width(8.dp))
                Button(onClick = onSync, enabled = syncEnabled && !paused) { Text("Sync") }
            }
        }
    }

    if (showDeleteDialog) {
        DeletePairDialog(
            pairName = pair.name,
            onConfirm = { purgeState ->
                showDeleteDialog = false
                onDelete(purgeState)
            },
            onDismiss = { showDeleteDialog = false },
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun PreviewBottomSheet(state: PreviewState?, onDismiss: () -> Unit) {
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(Modifier.fillMaxWidth().padding(16.dp)) {
            when (state) {
                is PreviewState.Loading -> {
                    val title = state.pairName?.let { "Loading $it…" } ?: "Loading preview for all pairs…"
                    Text(title, style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.height(12.dp))
                    CircularProgressIndicator()
                }

                is PreviewState.Loaded -> PreviewContent(state.status)

                is PreviewState.LoadedAll -> PreviewAllContent(state.statuses)

                null -> Unit
            }
        }
    }
}

@Composable
private fun PreviewContent(status: StatusDto) {
    Text("Preview: ${status.pair}", style = MaterialTheme.typography.titleMedium)
    Spacer(Modifier.height(8.dp))
    val summary = status.summary
    Text("Copied to laptop: ${summary.copiedToLocal}   Copied to phone: ${summary.copiedToRemote}")
    Text("Deleted on laptop: ${summary.deletedLocal}   Deleted on phone: ${summary.deletedRemote}")
    Text("Conflicts: ${summary.conflicts}   Needs decision: ${summary.asks}   Errors: ${summary.errors}")
    Spacer(Modifier.height(12.dp))
    if (status.actions.isEmpty()) {
        Text("Nothing to do.")
    } else {
        LazyColumn(Modifier.heightIn(max = 320.dp)) {
            items(status.actions) { line -> ActionLineItem(line) }
        }
    }
}

/**
 * "Preview" for "Sync all" (leftover L4): a compact per-pair summary line
 * rather than the full itemized file list [PreviewContent] shows for one
 * pair - drill into a pair's own **Preview** for that level of detail.
 */
@Composable
private fun PreviewAllContent(statuses: List<StatusDto>) {
    Text("Preview: all pairs", style = MaterialTheme.typography.titleMedium)
    Spacer(Modifier.height(8.dp))
    if (statuses.all { it.actions.isEmpty() }) {
        Text("Nothing to do.")
        return
    }
    LazyColumn(Modifier.heightIn(max = 400.dp)) {
        items(statuses, key = { it.pair }) { status -> PreviewAllRow(status) }
    }
}

@Composable
private fun PreviewAllRow(status: StatusDto) {
    Column(Modifier.padding(vertical = 6.dp)) {
        Text(status.pair, style = MaterialTheme.typography.titleSmall)
        val summary = status.summary
        if (status.actions.isEmpty()) {
            Text("Nothing to do.", style = MaterialTheme.typography.bodySmall)
        } else {
            Text(
                "${summary.copiedToRemote} to phone, ${summary.copiedToLocal} to laptop, " +
                    "${summary.deletedLocal + summary.deletedRemote} deleted, " +
                    "${summary.conflicts} conflicts, ${summary.asks} to decide, ${summary.errors} errors",
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

/**
 * Bottom sheet for a pair's saved conflict-copy backups (task 29c): lists
 * `.androsync/conflicts/` versions with per-version **Restore**, plus a
 * **Clean duplicates** action for pre-task-29b `*.conflict-*` files that
 * shows the dry-run preview before applying it.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ConflictsBottomSheet(
    state: ConflictsSheetState?,
    onDismiss: () -> Unit,
    onRestore: (rel: String, whenStamp: String) -> Unit,
    onPreviewClean: () -> Unit,
    onCancelCleanPreview: () -> Unit,
    onConfirmClean: () -> Unit,
) {
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(Modifier.fillMaxWidth().padding(16.dp)) {
            when (state) {
                is ConflictsSheetState.Loading -> {
                    Text("Conflict versions: ${state.pairName}", style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.height(12.dp))
                    CircularProgressIndicator()
                }

                is ConflictsSheetState.Loaded -> ConflictsSheetContent(
                    state = state,
                    onRestore = onRestore,
                    onPreviewClean = onPreviewClean,
                    onCancelCleanPreview = onCancelCleanPreview,
                    onConfirmClean = onConfirmClean,
                )

                null -> Unit
            }
        }
    }
}

@Composable
private fun ConflictsSheetContent(
    state: ConflictsSheetState.Loaded,
    onRestore: (rel: String, whenStamp: String) -> Unit,
    onPreviewClean: () -> Unit,
    onCancelCleanPreview: () -> Unit,
    onConfirmClean: () -> Unit,
) {
    Text("Conflict versions: ${state.pairName}", style = MaterialTheme.typography.titleMedium)
    Spacer(Modifier.height(8.dp))

    if (state.versions.isEmpty()) {
        Text("No saved conflict versions.")
    } else {
        LazyColumn(Modifier.heightIn(max = 280.dp)) {
            items(state.versions, key = { "${it.whenStamp}/${it.rel}" }) { version ->
                ConflictVersionRow(version = version, onRestore = { onRestore(version.rel, version.whenStamp) })
            }
        }
    }

    Spacer(Modifier.height(12.dp))
    HorizontalDivider()
    Spacer(Modifier.height(12.dp))

    val preview = state.cleanPreview
    if (preview == null) {
        Text(
            "Old-style duplicate files left over from before the conflicts folder can be cleaned up.",
            style = MaterialTheme.typography.bodySmall,
        )
        Spacer(Modifier.height(8.dp))
        Button(onClick = onPreviewClean, enabled = !state.busy, modifier = Modifier.fillMaxWidth()) {
            if (state.busy) CircularProgressIndicator(modifier = Modifier.padding(4.dp)) else Text("Clean duplicates")
        }
    } else {
        CleanPreviewContent(
            preview = preview,
            busy = state.busy,
            onCancel = onCancelCleanPreview,
            onConfirm = onConfirmClean,
        )
    }
}

@Composable
private fun ConflictVersionRow(version: ConflictVersionDto, onRestore: () -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(
                text = version.rel,
                style = MaterialTheme.typography.bodyMedium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(formatConflictStamp(version.whenStamp), style = MaterialTheme.typography.bodySmall)
        }
        TextButton(onClick = onRestore) { Text("Restore") }
    }
}

@Composable
private fun CleanPreviewContent(
    preview: ConflictsCleanResponse,
    busy: Boolean,
    onCancel: () -> Unit,
    onConfirm: () -> Unit,
) {
    if (preview.actions.isEmpty()) {
        Text("No old-style duplicate files found.")
        return
    }
    Text("${preview.actions.size} duplicate file(s) found:", style = MaterialTheme.typography.bodyMedium)
    Spacer(Modifier.height(4.dp))
    LazyColumn(Modifier.heightIn(max = 200.dp)) {
        items(preview.actions, key = { it.rel }) { action ->
            val outcome = if (action.identicalToOriginal) "identical, will be deleted" else "differs, will be archived"
            Text("${action.rel}: $outcome", style = MaterialTheme.typography.bodySmall)
        }
    }
    Spacer(Modifier.height(8.dp))
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedButton(onClick = onCancel, enabled = !busy) { Text("Cancel") }
        Button(onClick = onConfirm, enabled = !busy) {
            if (busy) CircularProgressIndicator(modifier = Modifier.padding(4.dp)) else Text("Apply")
        }
    }
}

/** "20260905-153000" -> "2026-09-05 15:30:00"; the raw stamp unchanged if it doesn't match that shape. */
private fun formatConflictStamp(stamp: String): String {
    if (stamp.length != 15 || stamp[8] != '-') return stamp
    val date = stamp.substring(0, 8)
    val time = stamp.substring(9)
    return "${date.substring(0, 4)}-${date.substring(4, 6)}-${date.substring(6, 8)} " +
        "${time.substring(0, 2)}:${time.substring(2, 4)}:${time.substring(4, 6)}"
}

/**
 * Bottom sheet listing a pair's recorded runs (task 30c, "Undo last sync…"):
 * time, kind (sync/undo) and a one-line summary, each with an **Undo**
 * action that confirms (with an optional "pause the pair afterwards")
 * before posting `POST /v1/undo/<pair>` and navigating to the new job.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun RunsBottomSheet(
    state: RunsSheetState?,
    onDismiss: () -> Unit,
    onUndo: (runId: String, pause: Boolean) -> Unit,
) {
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(Modifier.fillMaxWidth().padding(16.dp)) {
            when (state) {
                is RunsSheetState.Loading -> {
                    Text("Runs: ${state.pairName}", style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.height(12.dp))
                    CircularProgressIndicator()
                }

                is RunsSheetState.Loaded -> RunsSheetContent(state = state, onUndo = onUndo)

                null -> Unit
            }
        }
    }
}

@Composable
private fun RunsSheetContent(state: RunsSheetState.Loaded, onUndo: (runId: String, pause: Boolean) -> Unit) {
    var undoTarget by remember { mutableStateOf<String?>(null) }

    Text("Runs: ${state.pairName}", style = MaterialTheme.typography.titleMedium)
    Spacer(Modifier.height(8.dp))

    if (state.runs.isEmpty()) {
        Text("No runs recorded yet.")
    } else {
        LazyColumn(Modifier.heightIn(max = 360.dp)) {
            items(state.runs, key = { it.runId }) { run ->
                RunRow(run = run, busy = state.busy, onUndo = { undoTarget = run.runId })
            }
        }
    }

    undoTarget?.let { runId ->
        UndoConfirmDialog(
            pairName = state.pairName,
            onConfirm = { pause ->
                undoTarget = null
                onUndo(runId, pause)
            },
            onDismiss = { undoTarget = null },
        )
    }
}

@Composable
private fun RunRow(run: RunDto, busy: Boolean, onUndo: () -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(
                text = "${formatConflictStamp(run.runId.substringBeforeLast('-'))} · ${run.kind}",
                style = MaterialTheme.typography.bodyMedium,
            )
            Text(formatRunSummary(run), style = MaterialTheme.typography.bodySmall)
        }
        TextButton(onClick = onUndo, enabled = !busy && run.finished) { Text("Undo") }
    }
}

/** One-line summary for a [RunDto], covering both a sync's and an undo's summary shape. */
private fun formatRunSummary(run: RunDto): String {
    if (!run.finished) return "unfinished (crashed or cancelled)"
    val summary = run.summary ?: return "no changes"
    return if (run.kind == "undo") {
        val skippedNote = if (summary.skipped.isEmpty()) "" else ", ${summary.skipped.size} skipped"
        "restored ${summary.restoredLocal} on laptop, ${summary.restoredRemote} on phone$skippedNote"
    } else {
        "${summary.copiedToRemote} to phone, ${summary.copiedToLocal} to laptop, " +
            "${summary.deletedLocal + summary.deletedRemote} deleted, ${summary.conflicts} conflicts"
    }
}

/** Confirms undoing one run (task 30c), with an optional "pause the pair afterwards". */
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

/** "  ·  auto" (or "auto paused: why") on a pair card whose automatic sync is on (Phase T2). */
internal fun autoBadge(pair: PairDto): String = when {
    !pair.auto -> ""
    pair.autoState?.state == "paused" -> "  ·  auto paused: ${pair.autoState.reason ?: "see the laptop"}"
    else -> "  ·  auto"
}

