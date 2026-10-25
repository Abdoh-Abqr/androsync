package com.androsync.remote.ui.setup

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings

/**
 * Which Settings page each checklist button opens (task 37f, issue #10). The action strings are plain constants
 * so the JVM tests can pin them; [openSettingsPage] falls back to the main Settings page when a phone has no
 * handler for the exact one (some vendors hide Developer options until they are switched on).
 */
object SettingsPages {
    const val ABOUT_PHONE = "android.settings.DEVICE_INFO_SETTINGS"
    const val DEVELOPER_OPTIONS = "android.settings.APPLICATION_DEVELOPMENT_SETTINGS"
    const val ALL_FILES_ACCESS = "android.settings.MANAGE_APP_ALL_FILES_ACCESS_PERMISSION"
    const val MAIN = "android.settings.SETTINGS"

    /**
     * The key of the setting to scroll to and highlight on the page that [action] opens (Settings' own
     * `:settings:fragment_args_key` extra, used by the Settings search). "enable_adb" is the USB debugging switch.
     * Phones whose Settings app ignores the extra simply open the page at the top.
     */
    fun highlightKeyFor(action: StepAction): String? =
        if (action == StepAction.OPEN_DEVELOPER_OPTIONS) "enable_adb" else null

    const val EXTRA_FRAGMENT_ARG_KEY = ":settings:fragment_args_key"
    const val EXTRA_SHOW_FRAGMENT_ARGS = ":settings:show_fragment_args"

    /** The Settings action behind a step's button, or null for [StepAction.NONE]. */
    fun actionFor(action: StepAction): String? = when (action) {
        StepAction.NONE -> null
        StepAction.OPEN_ABOUT_PHONE -> ABOUT_PHONE
        StepAction.OPEN_DEVELOPER_OPTIONS -> DEVELOPER_OPTIONS
        StepAction.OPEN_ALL_FILES_ACCESS -> ALL_FILES_ACCESS
    }
}

/** Opens the page for [action]; falls back to the main Settings page. Returns false if even that failed. */
fun openSettingsPage(context: Context, action: StepAction): Boolean {
    val name = SettingsPages.actionFor(action) ?: return false
    val intent = Intent(name).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    if (action == StepAction.OPEN_ALL_FILES_ACCESS) intent.data = Uri.parse("package:${context.packageName}")
    SettingsPages.highlightKeyFor(action)?.let { key ->
        intent.putExtra(SettingsPages.EXTRA_FRAGMENT_ARG_KEY, key)
        intent.putExtra(SettingsPages.EXTRA_SHOW_FRAGMENT_ARGS, android.os.Bundle().apply {
            putString(SettingsPages.EXTRA_FRAGMENT_ARG_KEY, key)
        })
    }
    return try {
        context.startActivity(intent)
        true
    } catch (_: ActivityNotFoundException) {
        try {
            context.startActivity(Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            true
        } catch (_: ActivityNotFoundException) {
            false
        }
    }
}

/** What the phone says about itself: both switches live in `Settings.Global` and any app may read them. */
data class PhoneSwitches(val developerOptionsOn: Boolean, val usbDebuggingOn: Boolean)

fun readPhoneSwitches(context: Context): PhoneSwitches {
    val resolver = context.contentResolver
    return PhoneSwitches(
        developerOptionsOn = Settings.Global.getInt(resolver, Settings.Global.DEVELOPMENT_SETTINGS_ENABLED, 0) == 1,
        usbDebuggingOn = Settings.Global.getInt(resolver, Settings.Global.ADB_ENABLED, 0) == 1,
    )
}
