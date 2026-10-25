package com.androsync.remote.ui.common

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri

/** Where the project lives. The install guide is the "Install on the laptop" section of the README. */
object ProjectLinks {
    const val REPO = "https://github.com/Abdoh-Abqr/androsync"
    const val INSTALL_GUIDE = "$REPO/blob/master/docs/install.md"
}

/** Opens [url] in the browser; does nothing on a phone without one. */
fun openUrl(context: Context, url: String) {
    try {
        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    } catch (_: ActivityNotFoundException) {
        // No browser installed: nothing to open.
    }
}
