package com.androsync.remote.ui.setup

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.androsync.remote.data.settings.Preset
import com.androsync.remote.ui.common.AndroSyncTopBar
import com.androsync.remote.ui.common.ConnectionState
import com.androsync.remote.ui.common.ProjectLinks
import com.androsync.remote.ui.common.openUrl
import com.androsync.remote.ui.rememberSetupViewModel
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions

/**
 * "Connect to your laptop" screen (PLAN.md task 18): preset chips fill the
 * URL field, "Scan QR" reads the code from `androsync qr`, "Test connection"
 * pings the entered URL/token, "Save" persists and navigates on to `pairs`.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SetupScreen(
    connectionState: ConnectionState,
    onSaved: () -> Unit,
    onConnectPhone: () -> Unit = {},
    viewModel: SetupViewModel = rememberSetupViewModel(),
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val context = LocalContext.current
    // ZXing's scanner screen asks for the camera permission itself and returns the text, or null when the user backs out.
    val scanLauncher = rememberLauncherForActivityResult(ScanContract()) { result ->
        val text = result.contents
        if (text != null) viewModel.onQrScanned(text)
    }

    Scaffold(
        topBar = { AndroSyncTopBar(title = "Setup", connectionState = connectionState) },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Connect to your laptop", style = MaterialTheme.typography.titleLarge)

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(
                    selected = uiState.preset == Preset.USB,
                    onClick = { viewModel.onPresetSelected(Preset.USB) },
                    label = { Text("USB") },
                )
                FilterChip(
                    selected = uiState.preset == Preset.TAILSCALE,
                    onClick = { viewModel.onPresetSelected(Preset.TAILSCALE) },
                    label = { Text("Remote") },
                )
                FilterChip(
                    selected = uiState.preset == Preset.LAN,
                    onClick = { viewModel.onPresetSelected(Preset.LAN) },
                    label = { Text("LAN") },
                )
            }

            OutlinedTextField(
                value = uiState.url,
                onValueChange = viewModel::onUrlChange,
                label = { Text("Laptop URL") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                modifier = Modifier.fillMaxWidth(),
            )

            OutlinedTextField(
                value = uiState.token,
                onValueChange = viewModel::onTokenChange,
                label = { Text("Token") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )

            OutlinedButton(
                onClick = { scanLauncher.launch(qrScanOptions()) },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Scan QR") }

            Button(onClick = viewModel::testConnection, modifier = Modifier.fillMaxWidth()) {
                Text("Test connection")
            }

            when (val test = uiState.testState) {
                is TestConnectionState.Loading -> CircularProgressIndicator(modifier = Modifier.padding(8.dp))
                is TestConnectionState.Success -> Text(
                    "Connected: androsync ${test.ping.version}" +
                        (test.ping.transport?.let { " ($it)" } ?: ""),
                    color = MaterialTheme.colorScheme.primary,
                )
                is TestConnectionState.Error -> Text(test.message, color = MaterialTheme.colorScheme.error)
                TestConnectionState.Idle -> Unit
            }

            Button(
                onClick = { viewModel.save(onSaved) },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Save") }

            OutlinedButton(onClick = onConnectPhone, modifier = Modifier.fillMaxWidth()) {
                Text("Help me connect my phone (USB debugging)")
            }

            Text("No laptop program yet?", style = MaterialTheme.typography.bodySmall)
            OutlinedButton(
                onClick = { openUrl(context, ProjectLinks.INSTALL_GUIDE) },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("How to install it on Linux, macOS or Windows") }
        }
    }
}

/** QR codes only, a short prompt, free rotation. */
private fun qrScanOptions(): ScanOptions = ScanOptions()
    .setDesiredBarcodeFormats(ScanOptions.QR_CODE)
    .setPrompt("Point the camera at the code from `androsync qr`")
    .setBeepEnabled(false)
    .setOrientationLocked(false)
