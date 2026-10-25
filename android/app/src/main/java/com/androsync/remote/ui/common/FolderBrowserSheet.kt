package com.androsync.remote.ui.common

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.androsync.remote.data.api.BrowseEntryDto
import com.androsync.remote.ui.pairs.BrowseUiState

/**
 * Modal bottom sheet folder picker backed by `GET /v1/browse` (PLAN.md task 23c):
 * breadcrumb path, "Up", the current directory's subfolders, "New folder"
 * and "Choose this folder". [state] null means nothing to show - the
 * caller only renders this when it is non-null.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FolderBrowserSheet(
    state: BrowseUiState,
    onDismiss: () -> Unit,
    onUp: () -> Unit,
    onOpen: (String) -> Unit,
    onNewFolder: (String) -> Unit,
    onChoose: () -> Unit,
) {
    var showNewFolderDialog by remember { mutableStateOf(false) }

    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(Modifier.fillMaxWidth().padding(16.dp)) {
            Text(
                text = if (state.side == "local") "Choose a laptop folder" else "Choose a phone folder",
                style = MaterialTheme.typography.titleMedium,
            )
            Text(
                text = state.result?.path?.ifEmpty { "…" } ?: "…",
                style = MaterialTheme.typography.bodySmall,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.padding(top = 4.dp, bottom = 8.dp),
            )

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TextButton(onClick = onUp, enabled = state.result?.parent != null) { Text("Up") }
                TextButton(onClick = { showNewFolderDialog = true }, enabled = state.result?.writable == true) {
                    Text("New folder")
                }
            }

            when {
                state.loading -> Box(Modifier.fillMaxWidth().height(160.dp), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }

                state.error != null -> Text(state.error, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(16.dp))

                state.result != null -> {
                    val dirs = state.result.entries.filter { it.isDir }
                    if (dirs.isEmpty()) {
                        Text("No subfolders here.", modifier = Modifier.padding(16.dp))
                    } else {
                        LazyColumn(Modifier.heightIn(max = 320.dp)) {
                            items(dirs, key = { it.name }) { entry ->
                                FolderRow(entry = entry, onClick = { onOpen(entryPath(state, entry)) })
                            }
                        }
                    }
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth().padding(top = 12.dp),
                horizontalArrangement = Arrangement.End,
            ) {
                TextButton(onClick = onDismiss) { Text("Cancel") }
                TextButton(onClick = onChoose, enabled = state.result != null) { Text("Choose this folder") }
            }
        }
    }

    if (showNewFolderDialog) {
        NewFolderDialog(
            onConfirm = { name ->
                showNewFolderDialog = false
                onNewFolder(name)
            },
            onDismiss = { showNewFolderDialog = false },
        )
    }
}

/** The full path a tapped subfolder [entry] resolves to, given the currently listed [state]. */
private fun entryPath(state: BrowseUiState, entry: BrowseEntryDto): String {
    val base = state.result?.path.orEmpty()
    if (base.isEmpty()) return entry.name // browsing the root list: entries already carry full paths
    return if (base.endsWith("/")) base + entry.name else "$base/${entry.name}"
}

@Composable
private fun FolderRow(entry: BrowseEntryDto, onClick: () -> Unit) {
    Surface(onClick = onClick, modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = entry.name,
                style = MaterialTheme.typography.bodyLarge,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, contentDescription = null)
        }
    }
}

@Composable
private fun NewFolderDialog(onConfirm: (String) -> Unit, onDismiss: () -> Unit) {
    var name by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("New folder") },
        text = {
            OutlinedTextField(value = name, onValueChange = { name = it }, singleLine = true, modifier = Modifier.fillMaxWidth())
        },
        confirmButton = {
            TextButton(onClick = { onConfirm(name.trim()) }, enabled = name.isNotBlank()) { Text("Create") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
