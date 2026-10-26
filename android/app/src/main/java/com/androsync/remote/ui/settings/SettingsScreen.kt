package com.androsync.remote.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.androsync.remote.ui.common.AndroSyncTopBar
import com.androsync.remote.ui.common.ConnectionState
import com.androsync.remote.ui.common.ProjectLinks
import com.androsync.remote.ui.common.openUrl
import com.androsync.remote.ui.common.shareApp
import com.androsync.remote.ui.rememberSettingsViewModel
import com.androsync.remote.ui.setup.TestConnectionState

/**
 * Settings screen (PLAN.md task 22): editable Tailscale/LAN URLs, a masked
 * token field with reveal, the poll interval, "Test connection", "Forget
 * setup", and an About section.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    connectionState: ConnectionState,
    onBack: () -> Unit,
    onForgotSetup: () -> Unit,
    onConnectPhone: () -> Unit = {},
    onAddLaptop: () -> Unit = {},
    onEditLaptop: () -> Unit = {},
    onOpenSupport: () -> Unit = {},
    onLaptopChanged: () -> Unit = {},
    viewModel: SettingsViewModel = rememberSettingsViewModel(),
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    var showForgetDialog by remember { mutableStateOf(false) }
    var showNewTokenDialog by remember { mutableStateOf(false) }

    Scaffold(
        topBar = { AndroSyncTopBar(title = "Settings", connectionState = connectionState, onBack = onBack) },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Laptops", style = MaterialTheme.typography.titleMedium)
            uiState.laptops.forEach { laptop ->
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        laptop.name + if (laptop.id == uiState.selectedLaptopId) "  (in use)" else "",
                        modifier = Modifier.weight(1f),
                    )
                    if (laptop.id != uiState.selectedLaptopId) {
                        TextButton(onClick = { viewModel.selectLaptop(laptop.id, onLaptopChanged) }) { Text("Use") }
                    }
                    TextButton(onClick = { viewModel.removeLaptop(laptop.id, onLaptopChanged) }) { Text("Remove") }
                }
            }
            OutlinedButton(onClick = onEditLaptop, modifier = Modifier.fillMaxWidth()) {
                Text("Scan the QR code again for the laptop in use")
            }
            OutlinedButton(onClick = onAddLaptop, modifier = Modifier.fillMaxWidth()) {
                Text("Add another laptop (scan its QR code)")
            }

            HorizontalDivider()

            Text("Connection to the laptop in use", style = MaterialTheme.typography.titleMedium)

            OutlinedTextField(
                value = uiState.tailscaleUrl,
                onValueChange = viewModel::onTailscaleUrlChange,
                label = { Text("Tailscale URL") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )

            OutlinedTextField(
                value = uiState.lanUrl,
                onValueChange = viewModel::onLanUrlChange,
                label = { Text("LAN URL") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )

            TokenField(
                token = uiState.token,
                revealed = uiState.tokenRevealed,
                onTokenChange = viewModel::onTokenChange,
                onToggleRevealed = viewModel::toggleTokenRevealed,
            )

            Button(onClick = viewModel::testConnection, modifier = Modifier.fillMaxWidth()) {
                Text("Test connection")
            }
            TestConnectionResult(uiState.testState)

            OutlinedButton(onClick = { showNewTokenDialog = true }, modifier = Modifier.fillMaxWidth()) {
                Text("Generate a new token")
            }
            uiState.tokenMessage?.let { Text(it, style = MaterialTheme.typography.bodySmall) }

            OutlinedButton(onClick = onConnectPhone, modifier = Modifier.fillMaxWidth()) {
                Text("Connect your phone (USB debugging)")
            }

            HorizontalDivider()

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Remind me when USB debugging is off", modifier = Modifier.weight(1f))
                Switch(checked = uiState.setupHintsEnabled, onCheckedChange = viewModel::onSetupHintsChange)
            }

            HorizontalDivider()

            Text("Poll interval", style = MaterialTheme.typography.titleMedium)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                POLL_INTERVAL_CHOICES_SECONDS.forEach { seconds ->
                    FilterChip(
                        selected = uiState.pollIntervalSeconds == seconds,
                        onClick = { viewModel.onPollIntervalSelected(seconds) },
                        label = { Text("${seconds}s") },
                    )
                }
            }

            HorizontalDivider()

            // Task 37c: the full build adds the agent-mode switch here; the play build draws nothing.
            AgentSettingsSection(connectionState)

            OutlinedButton(
                onClick = { showForgetDialog = true },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Forget setup") }

            HorizontalDivider()

            SupportSection()

            AboutSection(connectionState, onOpenSupport)
        }
    }

    if (showNewTokenDialog) {
        AlertDialog(
            onDismissRequest = { showNewTokenDialog = false },
            title = { Text("Generate a new token?") },
            text = {
                Text(
                    "The laptop makes a new token and this app saves it. Every other phone or tool that has the old token " +
                        "stops working until it scans the QR code again.",
                )
            },
            confirmButton = {
                TextButton(onClick = { showNewTokenDialog = false; viewModel.generateNewToken() }) { Text("Generate") }
            },
            dismissButton = { TextButton(onClick = { showNewTokenDialog = false }) { Text("Cancel") } },
        )
    }

    if (showForgetDialog) {
        ForgetSetupDialog(
            onConfirm = {
                showForgetDialog = false
                viewModel.forgetSetup(onForgotSetup)
            },
            onDismiss = { showForgetDialog = false },
        )
    }
}

@Composable
private fun TokenField(
    token: String,
    revealed: Boolean,
    onTokenChange: (String) -> Unit,
    onToggleRevealed: () -> Unit,
) {
    OutlinedTextField(
        value = token,
        onValueChange = onTokenChange,
        label = { Text("Token") },
        singleLine = true,
        visualTransformation = if (revealed) VisualTransformation.None else PasswordVisualTransformation(),
        trailingIcon = {
            // Text rather than an Icons.Filled.Visibility glyph: that icon only ships in
            // the (large) material-icons-extended artifact, which this app doesn't depend on.
            TextButton(onClick = onToggleRevealed) { Text(if (revealed) "Hide" else "Show") }
        },
        modifier = Modifier.fillMaxWidth(),
    )
}

@Composable
private fun TestConnectionResult(state: TestConnectionState) {
    when (state) {
        is TestConnectionState.Loading -> CircularProgressIndicator(modifier = Modifier.padding(8.dp))
        is TestConnectionState.Success -> Text(
            "Connected: androsync ${state.ping.version}" +
                (state.ping.transport?.let { " ($it)" } ?: "") +
                (state.ping.device?.let { " · $it" } ?: ""),
            color = MaterialTheme.colorScheme.primary,
        )
        is TestConnectionState.Error -> Text(state.message, color = MaterialTheme.colorScheme.error)
        TestConnectionState.Idle -> Unit
    }
}

@Composable
private fun ForgetSetupDialog(onConfirm: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Forget setup?") },
        text = { Text("This clears the saved URL and token. You'll need to set up the connection again.") },
        confirmButton = { TextButton(onClick = onConfirm) { Text("Forget") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun AboutSection(connectionState: ConnectionState, onOpenSupport: () -> Unit) {
    val context = LocalContext.current
    val appVersion = remember {
        runCatching {
            context.packageManager.getPackageInfo(context.packageName, 0).versionName
        }.getOrNull() ?: "unknown"
    }
    val laptopVersion = (connectionState as? ConnectionState.Connected)?.version

    Text("About", style = MaterialTheme.typography.titleMedium)
    Text("AndroSync Remote $appVersion")
    Text(if (laptopVersion != null) "Computer: androsync $laptopVersion" else "Computer version: not connected")
    Text("Free software, GPL-3.0-or-later. Made by Abdoh-Abqr.", style = MaterialTheme.typography.bodySmall)
    OutlinedButton(onClick = onOpenSupport, modifier = Modifier.fillMaxWidth()) {
        Text("Help and support (report a problem)")
    }
    OutlinedButton(onClick = { shareApp(context) }, modifier = Modifier.fillMaxWidth()) {
        Text("Tell a friend")
    }
    OutlinedButton(onClick = { openUrl(context, ProjectLinks.REPO) }, modifier = Modifier.fillMaxWidth()) {
        Text("Source code and guides - star it on GitHub")
    }
    OutlinedButton(onClick = { openUrl(context, ProjectLinks.INSTALL_GUIDE) }, modifier = Modifier.fillMaxWidth()) {
        Text("How to install the program on your Linux computer")
    }
}
