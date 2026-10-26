package com.androsync.remote.ui.common

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent

/**
 * The text "Tell a friend" hands to Android's share sheet. Nothing is sent by the app itself and nothing is counted:
 * the person picks the app and the receiver.
 */
fun shareText(): String =
    "AndroSync keeps folders identical between your Linux computer and your Android phone - free, open source, " +
        "no cloud. ${ProjectLinks.REPO}"

/** Opens the share sheet with [shareText]; does nothing on a phone that cannot share text. */
fun shareApp(context: Context) {
    val send = Intent(Intent.ACTION_SEND)
        .setType("text/plain")
        .putExtra(Intent.EXTRA_TEXT, shareText())
    try {
        context.startActivity(Intent.createChooser(send, "Tell a friend").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    } catch (_: ActivityNotFoundException) {
        // No app can take text: nothing to share with.
    }
}
