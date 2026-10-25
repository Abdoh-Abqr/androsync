package com.androsync.remote.ui.setup

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import com.androsync.remote.Distribution
import com.androsync.remote.ui.common.AndroSyncTopBar
import com.androsync.remote.ui.common.ConnectionState

/**
 * "Connect your phone" (task 37f, issue #10): a live checklist that leads the user to the USB debugging switch.
 * Android does not let an app switch it on, so each row has a button that opens the exact Settings page, and the
 * rows tick by themselves when the user comes back (the two switches are re-read on every `ON_RESUME`, the
 * "computer allowed" row follows the laptop's ping).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConnectPhoneScreen(
    connectionState: ConnectionState,
    onBack: () -> Unit,
) {
    val context = LocalContext.current
    var switches by remember { mutableStateOf(readPhoneSwitches(context)) }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { switches = readPhoneSwitches(context) }

    val connected = connectionState as? ConnectionState.Connected
    val readiness = deviceReadiness(laptopReachable = connected != null, devices = connected?.devices.orEmpty())
    val steps = computeChecklist(
        developerOptionsOn = switches.developerOptionsOn,
        usbDebuggingOn = switches.usbDebuggingOn,
        readiness = readiness,
        isFull = Distribution.SHOWS_ALL_FILES_ROW,
        allFilesAccess = hasAllFilesAccess(),
    )
    val next = firstOpenStep(steps)
    var couldNotOpen by remember { mutableStateOf(false) }

    Scaffold(
        topBar = { AndroSyncTopBar(title = "Connect your phone", connectionState = connectionState, onBack = onBack) },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(
                text = if (isFullySetUp(steps)) "Your phone is ready to sync." else "Do the highlighted step, then come back here.",
                style = MaterialTheme.typography.titleMedium,
            )
            steps.forEach { step ->
                StepCard(
                    step = step,
                    isNext = step.id == next?.id,
                    onAction = { couldNotOpen = !openSettingsPage(context, step.action) },
                )
            }
            if (couldNotOpen) {
                Text(
                    "This phone would not open that page. Open Settings yourself and look for Developer options.",
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            Text(
                "Android does not let an app switch USB debugging on, so this screen can only show you where it is.",
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
private fun StepCard(step: ChecklistStep, isNext: Boolean, onAction: () -> Unit) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(12.dp), verticalAlignment = androidx.compose.ui.Alignment.Top) {
            Text(
                text = statusMark(step.status),
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.width(32.dp),
            )
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(step.title, style = MaterialTheme.typography.titleSmall)
                if (step.detail.isNotBlank()) Text(step.detail, style = MaterialTheme.typography.bodyMedium)
                val label = step.actionLabel
                if (label != null && step.action != StepAction.NONE && step.status != StepStatus.DONE) {
                    if (isNext) {
                        Button(onClick = onAction) { Text(label) }
                    } else {
                        OutlinedButton(onClick = onAction) { Text(label) }
                    }
                }
            }
        }
    }
}

private fun statusMark(status: StepStatus): String = when (status) {
    StepStatus.DONE -> "✓"
    StepStatus.TODO -> "●"
    StepStatus.WAITING -> "…"
    StepStatus.INFO -> "i"
}

/** `Environment.isExternalStorageManager()` exists from Android 11; before that the old storage permission rules apply. */
private fun hasAllFilesAccess(): Boolean =
    android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.R && android.os.Environment.isExternalStorageManager()
