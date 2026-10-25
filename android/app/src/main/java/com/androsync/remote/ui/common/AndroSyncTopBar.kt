package com.androsync.remote.ui.common

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

/**
 * Top app bar shared by every screen (PLAN.md task 18: "A small
 * ConnectionBadge composable shows ... in the top app bar of every
 * screen"): a title, an optional back button, the [ConnectionBadge], and
 * any screen-specific actions.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AndroSyncTopBar(
    title: String,
    connectionState: ConnectionState,
    onBack: (() -> Unit)? = null,
    /** Set false on the job screen itself, which already shows the running job in full. */
    showActiveJob: Boolean = true,
    actions: @Composable RowScope.() -> Unit = {},
) {
    Column {
    TopAppBar(
        title = { Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis) },
        navigationIcon = {
            if (onBack != null) {
                IconButton(onClick = onBack) {
                    Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                }
            }
        },
        actions = {
            actions()
            Spacer(modifier = Modifier.width(8.dp))
            ConnectionBadge(connectionState)
            Spacer(modifier = Modifier.width(4.dp))
        },
    )
    // The running sync is visible, and stoppable, from every screen and every device (task 36b, issue #7).
    val runningJob = (connectionState as? ConnectionState.Connected)?.currentJob
    if (showActiveJob && runningJob != null) {
        ActiveJobBar(runningJob)
    }
    }
}
