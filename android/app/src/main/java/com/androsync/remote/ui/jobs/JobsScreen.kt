package com.androsync.remote.ui.jobs

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.androsync.remote.ui.ConnectionViewModel
import com.androsync.remote.ui.common.AndroSyncTopBar
import com.androsync.remote.ui.common.ConnectionState
import com.androsync.remote.ui.job.jobChipFor
import com.androsync.remote.ui.rememberJobsViewModel

/** "Recent syncs": every sync the laptop still remembers, newest first; tapping one opens its full log. */
@Composable
fun JobsScreen(
    connectionState: ConnectionState,
    connectionViewModel: ConnectionViewModel,
    onBack: () -> Unit,
    onOpenJob: (String) -> Unit,
    viewModel: JobsViewModel = rememberJobsViewModel(connectionViewModel),
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    Scaffold(
        topBar = {
            AndroSyncTopBar(
                title = "Recent syncs",
                connectionState = connectionState,
                onBack = onBack,
                actions = { TextButton(onClick = viewModel::refresh) { Text("Refresh") } },
            )
        },
    ) { padding ->
        Box(modifier = Modifier.fillMaxSize().padding(padding)) {
            when {
                uiState.loading && uiState.jobs.isEmpty() -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                uiState.error != null && uiState.jobs.isEmpty() -> Text(
                    uiState.error!!,
                    modifier = Modifier.align(Alignment.Center).padding(16.dp),
                    color = MaterialTheme.colorScheme.error,
                )
                uiState.jobs.isEmpty() -> Text(
                    "No syncs yet. A sync you start shows up here with its full log.",
                    modifier = Modifier.align(Alignment.Center).padding(16.dp),
                )
                else -> LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    items(uiState.jobs, key = { it.id }) { job ->
                        val now = System.currentTimeMillis() / 1000.0
                        Card(modifier = Modifier.fillMaxWidth().clickable { onOpenJob(job.id) }) {
                            Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    Text(
                                        jobRowTitle(job),
                                        modifier = Modifier.weight(1f),
                                        style = MaterialTheme.typography.titleSmall,
                                        fontWeight = FontWeight.SemiBold,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                    Text(jobChipFor(job.state).label, style = MaterialTheme.typography.labelMedium)
                                }
                                Text(
                                    "${jobAge(job.created, now)} · ${jobRowSummary(job)}",
                                    style = MaterialTheme.typography.bodySmall,
                                    maxLines = 2,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}
