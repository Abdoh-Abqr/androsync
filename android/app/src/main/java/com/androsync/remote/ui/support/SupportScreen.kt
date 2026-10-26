package com.androsync.remote.ui.support

import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.androsync.remote.Distribution
import com.androsync.remote.data.api.ApiClient
import com.androsync.remote.ui.common.AndroSyncTopBar
import com.androsync.remote.ui.common.ConnectionState
import com.androsync.remote.ui.common.ProjectLinks
import com.androsync.remote.ui.common.connectionBadgeLabel
import com.androsync.remote.ui.common.openUrl

/**
 * Help and support (Phase S1): the guide, and "Report a problem" which builds a redacted text report the person reads and
 * then shares, opens as a GitHub issue, or emails. The app sends nothing by itself.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SupportScreen(
    connectionState: ConnectionState,
    apiClient: ApiClient?,
    onBack: () -> Unit,
) {
    val context = LocalContext.current
    var report by remember { mutableStateOf("Preparing the report...") }
    LaunchedEffect(connectionState, apiClient) { report = buildReport(context, connectionState, apiClient) }

    Scaffold(
        topBar = { AndroSyncTopBar(title = "Help and support", connectionState = connectionState, onBack = onBack) },
    ) { padding ->
        Column(
            modifier = Modifier.fillMaxSize().padding(padding).padding(16.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Something wrong?", style = MaterialTheme.typography.titleMedium)
            OutlinedButton(onClick = { openUrl(context, ProjectLinks.SUPPORT_GUIDE) }, modifier = Modifier.fillMaxWidth()) {
                Text("Read the support guide")
            }

            Text("Report a problem", style = MaterialTheme.typography.titleMedium)
            Text(
                "This is the report. The token, addresses and phone serials are already replaced by placeholders. " +
                    "Read it, add what happened at the top, then share it. Nothing is sent by the app itself.",
                style = MaterialTheme.typography.bodySmall,
            )
            Text(report, style = MaterialTheme.typography.bodySmall, fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace)
            Button(onClick = { share(context, report) }, modifier = Modifier.fillMaxWidth()) { Text("Share the report") }
            OutlinedButton(
                onClick = { openUrl(context, ProjectLinks.issueFormFor(appVersion(context))) },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Open the problem form on GitHub") }
            if (ProjectLinks.SUPPORT_EMAIL.isNotBlank()) {
                OutlinedButton(
                    onClick = { email(context, report) },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("Email the report") }
            }
        }
    }
}

private fun appVersion(context: Context): String =
    runCatching { context.packageManager.getPackageInfo(context.packageName, 0).versionName }.getOrNull() ?: "unknown"

/** Gathers what the app knows and the laptop says (recent syncs, phones), then redacts all of it. */
private suspend fun buildReport(context: Context, state: ConnectionState, client: ApiClient?): String {
    val connected = state as? ConnectionState.Connected
    val jobs = runCatching { client?.jobs()?.take(5) }.getOrNull().orEmpty().map { job ->
        val error = job.error?.let { " error=$it" }.orEmpty()
        "${job.kind} ${job.state} pairs=${job.pairs.size}$error"
    }
    val info = ReportInfo(
        appVersion = appVersion(context),
        flavor = if (Distribution.IS_FULL) "full" else "play",
        androidVersion = "${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})",
        model = "${Build.MANUFACTURER} ${Build.MODEL}",
        laptopVersion = connected?.version,
        connection = connectionBadgeLabel(state),
        agentStatus = connected?.let { if (it.agentConnected) "connected" else "not seen by the laptop" },
        phones = connected?.devices.orEmpty().map { "${it.id ?: it.serial} ${it.state} ${it.transport.orEmpty()}".trim() },
        jobs = jobs,
        appLog = AppLog.snapshot().takeLast(120),
    )
    val serials = connected?.devices.orEmpty().flatMap { listOfNotNull(it.serial, it.id) }
    return ReportBuilder.build(info, Redactor(serials = serials))
}

private fun share(context: Context, text: String) {
    val send = Intent(Intent.ACTION_SEND).apply {
        type = "text/plain"
        putExtra(Intent.EXTRA_SUBJECT, "AndroSync problem report")
        putExtra(Intent.EXTRA_TEXT, text)
    }
    context.startActivity(Intent.createChooser(send, "Share the report").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
}

private fun email(context: Context, text: String) {
    val mail = Intent(Intent.ACTION_SENDTO).apply {
        data = android.net.Uri.parse("mailto:")
        putExtra(Intent.EXTRA_EMAIL, arrayOf(ProjectLinks.SUPPORT_EMAIL))
        putExtra(Intent.EXTRA_SUBJECT, "AndroSync problem report")
        putExtra(Intent.EXTRA_TEXT, text)
    }
    runCatching { context.startActivity(mail.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
}
