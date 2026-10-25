package com.androsync.remote.ui.settings

import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext

const val SUPPORT_URL = "https://buymeacoffee.com/abqr"

/**
 * Optional donation link (task 25, issue #25), `full` build only. androsync is free software (GPL-3.0-or-later);
 * donations are a thank-you, not a requirement. The `play` build shows nothing: Google Play has strict rules about
 * links to outside payments inside an app.
 */
@Composable
fun SupportSection() {
    val context = LocalContext.current
    Text("Support", style = MaterialTheme.typography.titleMedium)
    Text(
        "AndroSync is free software. If it saves you time, you can buy the developer a coffee. This is optional.",
        style = MaterialTheme.typography.bodySmall,
    )
    OutlinedButton(
        onClick = {
            try {
                context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(SUPPORT_URL)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            } catch (_: ActivityNotFoundException) {
                // No browser installed: nothing to open.
            }
        },
        modifier = Modifier.fillMaxWidth(),
    ) { Text("Buy me a coffee") }
}
