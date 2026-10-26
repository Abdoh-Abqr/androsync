package com.androsync.remote.ui.settings

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.os.PowerManager
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.androsync.remote.agent.AgentService
import com.androsync.remote.agent.AgentState
import com.androsync.remote.agent.vendorBatteryAdvice
import com.androsync.remote.ui.common.ConnectionState
import com.androsync.remote.ui.setup.StepAction
import com.androsync.remote.ui.setup.openSettingsPage

/**
 * Agent mode switch (task 37c, issue #15), `full` build only. Needs "All files access"; the switch stays off until it
 * is allowed. The laptop's own view ("agent connected") comes from the 5-second ping.
 */
@Composable
fun AgentSettingsSection(connectionState: ConnectionState) {
    val context = LocalContext.current
    var allFiles by remember { mutableStateOf(hasAllFilesAccess()) }
    var enabled by remember { mutableStateOf(AgentState.isEnabled(context)) }
    var unrestricted by remember { mutableStateOf(isIgnoringBatteryOptimizations(context)) }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) {
        unrestricted = isIgnoringBatteryOptimizations(context)
        allFiles = hasAllFilesAccess()
        enabled = AgentState.isEnabled(context)
    }
    val status by AgentState.status.collectAsStateWithLifecycle()
    val laptopSees = (connectionState as? ConnectionState.Connected)?.agentConnected == true

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Sync without USB debugging (agent mode)", style = MaterialTheme.typography.titleMedium)
        Text(
            "The app reads and writes your folders itself and the laptop asks it over Tailscale or Wi-Fi. " +
                "Keep this phone and the laptop on the same network, or both on Tailscale. A pair uses it only when it is set to the agent.",
            style = MaterialTheme.typography.bodySmall,
        )
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("Run the agent", modifier = Modifier.weight(1f))
            Switch(
                checked = enabled && allFiles,
                enabled = allFiles,
                onCheckedChange = { on ->
                    enabled = on
                    if (on) AgentService.start(context) else AgentService.stop(context)
                },
            )
        }
        var pauseOnMobile by remember { mutableStateOf(AgentState.pauseOnMobile(context)) }
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("Pause on mobile data", modifier = Modifier.weight(1f))
            Switch(checked = pauseOnMobile, onCheckedChange = { pauseOnMobile = it; AgentState.setPauseOnMobile(context, it) })
        }
        if (!allFiles) {
            Text(
                "Android must first allow this app to access all files.",
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodySmall,
            )
            OutlinedButton(
                onClick = { openSettingsPage(context, StepAction.OPEN_ALL_FILES_ACCESS) },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Allow access to all files") }
        } else {
            Text("All files access: allowed", style = MaterialTheme.typography.bodySmall)
        }
        if (enabled && allFiles) {
            Text("Agent: ${status ?: "not running"}", style = MaterialTheme.typography.bodySmall)
            if (status?.startsWith("Looking for the laptop") == true) {
                Text(
                    "Without USB debugging the agent finds the laptop through the Tailscale URL or LAN URL above, " +
                        "for example http://<laptop Tailscale address>:8765. Fill one in, and make sure Tailscale is on.",
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            Text(
                if (laptopSees) "The laptop sees the agent." else "The laptop does not see the agent yet.",
                style = MaterialTheme.typography.bodySmall,
            )
            if (unrestricted) {
                Text("Battery: unrestricted (good)", style = MaterialTheme.typography.bodySmall)
            } else {
                Text(
                    "Battery: Android may stop the agent after a while. Allow it to run without restrictions.",
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                )
                OutlinedButton(
                    onClick = { requestBatteryExemption(context) },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("Allow unrestricted battery use") }
            }
            Text(vendorBatteryAdvice(Build.MANUFACTURER), style = MaterialTheme.typography.bodySmall)
        }
        HorizontalDivider()
    }
}

private fun isIgnoringBatteryOptimizations(context: Context): Boolean =
    context.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(context.packageName)

/** Shows Android's own "let the app always run in the background?" dialog; falls back to the battery settings list. */
private fun requestBatteryExemption(context: Context) {
    val request = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${context.packageName}"))
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    try {
        context.startActivity(request)
    } catch (_: ActivityNotFoundException) {
        context.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}

/** `Environment.isExternalStorageManager()` exists from Android 11; the agent needs it. */
private fun hasAllFilesAccess(): Boolean =
    Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && Environment.isExternalStorageManager()
