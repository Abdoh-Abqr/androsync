package com.androsync.remote.agent

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Environment

/**
 * Starts the agent again after the phone restarts, but only if the user had it switched on and All files access is
 * still allowed (task 37c). Without this the agent stayed off until the app was opened.
 */
class AgentBootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        val allFiles = Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && Environment.isExternalStorageManager()
        if (AgentState.isEnabled(context) && allFiles) AgentService.start(context)
    }
}
