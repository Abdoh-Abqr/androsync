package com.androsync.remote.ui.pairs

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import com.androsync.remote.Distribution
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.androsync.remote.ui.ConnectionViewModel
import com.androsync.remote.ui.common.AndroSyncTopBar
import com.androsync.remote.ui.common.ConnectionState
import com.androsync.remote.ui.common.directionText
import com.androsync.remote.ui.common.FolderBrowserSheet
import com.androsync.remote.ui.rememberPairEditorViewModel

/**
 * Add/Edit sync pair screen (PLAN.md task 23c): name, laptop/phone folders
 * (each with a "Browse" button opening [FolderBrowserSheet]), direction,
 * conflict policy, propagate-deletes, ignore patterns, and (edit mode
 * only) Delete.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun PairEditorScreen(
    pairName: String?,
    connectionState: ConnectionState,
    connectionViewModel: ConnectionViewModel,
    onBack: () -> Unit,
    onSaved: () -> Unit,
    onDeleted: () -> Unit,
    viewModel: PairEditorViewModel = rememberPairEditorViewModel(pairName, connectionViewModel),
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }
    var showDeleteDialog by remember { mutableStateOf(false) }

    LaunchedEffect(viewModel) {
        viewModel.events.collect { event ->
            when (event) {
                PairEditorEvent.Saved -> onSaved()
                PairEditorEvent.Deleted -> onDeleted()
                is PairEditorEvent.Error -> snackbarHostState.showSnackbar(event.message)
            }
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            AndroSyncTopBar(
                title = if (uiState.isNew) "Add pair" else "Edit pair",
                connectionState = connectionState,
                onBack = onBack,
            )
        },
    ) { padding ->
        if (uiState.loading) {
            Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
            return@Scaffold
        }

        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            OutlinedTextField(
                value = uiState.name,
                onValueChange = viewModel::onNameChange,
                label = { Text("Name") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )

            FolderField(
                label = "Laptop folder",
                value = uiState.local,
                onValueChange = viewModel::onLocalChange,
                onBrowse = { viewModel.openBrowser("local") },
            )

            val pickPhoneFolder = rememberLauncherForActivityResult(
                ActivityResultContracts.OpenDocumentTree(),
            ) { uri -> viewModel.onRemoteFolderPicked(uri?.toString()) }

            PhoneFolderField(
                value = uiState.remote,
                onValueChange = viewModel::onRemoteChange,
                onPickOnPhone = { pickPhoneFolder.launch(null) },
                onBrowseViaLaptop = { viewModel.openBrowser("remote") },
            )

            val connected = connectionState as? ConnectionState.Connected
            val phones = phoneChoices(
                connected?.devices.orEmpty(),
                alsoKeep = listOf(uiState.device),
                agents = connected?.agents.orEmpty(),
                transport = uiState.transport,
            )
            val boundName = phones.firstOrNull { it.id == uiState.device }?.label
            val lapName = connected?.laptopName
            Text("Direction", style = MaterialTheme.typography.titleSmall)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                DirectionChip(Direction.LAPTOP_TO_PHONE, directionText("laptop-to-phone", lapName, boundName), uiState.direction, viewModel::onDirectionChange)
                DirectionChip(Direction.TWO_WAY, directionText("two-way", lapName, boundName), uiState.direction, viewModel::onDirectionChange)
                DirectionChip(Direction.PHONE_TO_LAPTOP, directionText("phone-to-laptop", lapName, boundName), uiState.direction, viewModel::onDirectionChange)
            }

            // Only the build that contains the agent can choose it (task 37d); the play build keeps what is saved.
            if (!Distribution.HAS_AGENT) {
                Text(
                    "This version reaches the phone over USB or Wi-Fi debugging. The full version can also sync without debugging.",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            if (Distribution.HAS_AGENT) {
                Text("Reach the phone via", style = MaterialTheme.typography.titleSmall)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(
                        selected = uiState.transport == "adb",
                        onClick = { viewModel.onTransportChange("adb") },
                        label = { Text("USB / Wi-Fi debugging") },
                    )
                    FilterChip(
                        selected = uiState.transport == "agent",
                        onClick = { viewModel.onTransportChange("agent") },
                        label = { Text("This app (no debugging)") },
                    )
                }
            }

            // Which phone the pair belongs to (task 33a). Always shown, so "Any phone" can be found (Phase V2).
            run {
                Text("Phone", style = MaterialTheme.typography.titleSmall)
                Text(
                    "Any phone = whichever phone is connected when the sync starts. With two phones connected at once " +
                        "the sync asks you to pick.",
                    style = MaterialTheme.typography.bodySmall,
                )
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(
                        selected = uiState.device == ANY_PHONE,
                        onClick = { viewModel.onDeviceChange(ANY_PHONE) },
                        label = { Text("Any phone") },
                    )
                    phones.forEach { phone ->
                        FilterChip(
                            selected = uiState.device == phone.id,
                            onClick = { viewModel.onDeviceChange(phone.id) },
                            label = { Text(phone.label) },
                        )
                    }
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text("Sync automatically")
                    Text(
                        "When files change. Only after you have synced this pair once by hand; a big change still waits for your OK.",
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
                Switch(checked = uiState.auto, onCheckedChange = viewModel::onAutoChange)
            }
            if (uiState.auto) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text("Only on Wi-Fi")
                    Switch(checked = uiState.wifiOnly, onCheckedChange = viewModel::onWifiOnlyChange)
                }
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text("Only while charging")
                    Switch(checked = uiState.chargingOnly, onCheckedChange = viewModel::onChargingOnlyChange)
                }
            }

            Text("Conflict policy", style = MaterialTheme.typography.titleSmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                ConflictChip(null, "newest wins", uiState.conflict, viewModel::onConflictChange)
                ConflictChip(ConflictPolicy.LOCAL_WINS, "laptop wins", uiState.conflict, viewModel::onConflictChange)
                ConflictChip(ConflictPolicy.REMOTE_WINS, "phone wins", uiState.conflict, viewModel::onConflictChange)
                ConflictChip(ConflictPolicy.ASK, "ask", uiState.conflict, viewModel::onConflictChange)
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Propagate deletes")
                Switch(
                    checked = uiState.propagateDeletes ?: defaultPropagateDeletes(uiState.direction),
                    onCheckedChange = viewModel::onPropagateDeletesChange,
                )
            }

            OutlinedTextField(
                value = uiState.ignore,
                onValueChange = viewModel::onIgnoreChange,
                label = { Text("Ignore patterns (comma-separated)") },
                modifier = Modifier.fillMaxWidth(),
            )

            if (uiState.foldersChanged) {
                Text(
                    "Folders changed: this pair's history is reset when you save, so the next sync only adds files and deletes nothing.",
                    color = MaterialTheme.colorScheme.tertiary,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            uiState.formError?.let { Text(it, color = MaterialTheme.colorScheme.error) }

            Button(
                onClick = viewModel::save,
                enabled = !uiState.saving,
                modifier = Modifier.fillMaxWidth(),
            ) {
                if (uiState.saving) CircularProgressIndicator(modifier = Modifier.padding(4.dp)) else Text("Save")
            }

            if (!uiState.isNew) {
                HorizontalDivider()
                OutlinedButton(
                    onClick = { showDeleteDialog = true },
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error),
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("Delete pair") }
            }
        }
    }

    uiState.browse?.let { browse ->
        FolderBrowserSheet(
            state = browse,
            onDismiss = viewModel::closeBrowser,
            onUp = viewModel::browseUp,
            onOpen = viewModel::browseInto,
            onNewFolder = viewModel::browseMkdir,
            onChoose = viewModel::browseChooseCurrent,
        )
    }

    if (showDeleteDialog) {
        DeletePairDialog(
            pairName = uiState.name,
            onConfirm = { purgeState ->
                showDeleteDialog = false
                viewModel.delete(purgeState)
            },
            onDismiss = { showDeleteDialog = false },
        )
    }
}

@Composable
private fun FolderField(label: String, value: String, onValueChange: (String) -> Unit, onBrowse: () -> Unit) {
    Column {
        OutlinedTextField(
            value = value,
            onValueChange = onValueChange,
            label = { Text(label) },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        TextButton(onClick = onBrowse) { Text("Browse") }
    }
}

/**
 * The phone-folder field gets two pickers (PLAN.md task 23c): the native
 * Android folder picker (fast, but only works for on-device storage
 * `SafPath` can resolve to a path), and "Browse via laptop" (`/v1/browse`
 * through the daemon, works for anything ADB can see, needs the phone
 * connected).
 */
@Composable
private fun PhoneFolderField(
    value: String,
    onValueChange: (String) -> Unit,
    onPickOnPhone: () -> Unit,
    onBrowseViaLaptop: () -> Unit,
) {
    Column {
        OutlinedTextField(
            value = value,
            onValueChange = onValueChange,
            label = { Text("Phone folder") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Row {
            TextButton(onClick = onPickOnPhone) { Text("Pick on phone") }
            TextButton(onClick = onBrowseViaLaptop) { Text("Browse via laptop") }
        }
    }
}

@Composable
private fun DirectionChip(value: String, label: String, selected: String, onSelect: (String) -> Unit) {
    FilterChip(selected = selected == value, onClick = { onSelect(value) }, label = { Text(label) })
}

@Composable
private fun ConflictChip(value: String?, label: String, selected: String?, onSelect: (String?) -> Unit) {
    FilterChip(selected = selected == value, onClick = { onSelect(value) }, label = { Text(label) })
}

@Composable
internal fun DeletePairDialog(pairName: String, onConfirm: (purgeState: Boolean) -> Unit, onDismiss: () -> Unit) {
    var purgeState by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Delete \"$pairName\"?") },
        text = {
            Column {
                Text("This removes it from the sync config. Files on both sides are never touched.")
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 8.dp)) {
                    Checkbox(checked = purgeState, onCheckedChange = { purgeState = it })
                    Text("Also delete sync history")
                }
            }
        },
        confirmButton = { TextButton(onClick = { onConfirm(purgeState) }) { Text("Delete") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
