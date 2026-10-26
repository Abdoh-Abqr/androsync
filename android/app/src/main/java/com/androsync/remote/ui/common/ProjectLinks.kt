package com.androsync.remote.ui.common

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri

/** Where the project lives. The install guide is the "Install on the laptop" section of the README. */
object ProjectLinks {
    const val REPO = "https://github.com/Abdoh-Abqr/androsync"
    const val INSTALL_GUIDE = "$REPO/blob/master/docs/install.md"
    const val SUPPORT_GUIDE = "$REPO/blob/master/docs/support.md"

    /** An address for private reports. Empty until the owner has made an alias; the email button is hidden then. */
    const val SUPPORT_EMAIL = ""

    /** The bug report form; only the version is prefilled, nothing personal. */
    fun issueFormFor(version: String) = "$REPO/issues/new?template=bug_report.yml&version=$version"
}

/** Opens [url] in the browser; does nothing on a phone without one. */
fun openUrl(context: Context, url: String) {
    try {
        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    } catch (_: ActivityNotFoundException) {
        // No browser installed: nothing to open.
    }
}
