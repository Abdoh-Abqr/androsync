package com.androsync.remote.agent

import android.content.Context
import android.os.Build
import android.os.Environment

/** Brings the agent service back when the user had it switched on (after a restart, an update or a killed process). */
object AgentAutoStart {
    fun ensureRunning(context: Context) {
        val allFiles = Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && Environment.isExternalStorageManager()
        if (!AgentState.isEnabled(context) || !allFiles) return
        try {
            AgentService.start(context)
        } catch (_: RuntimeException) {
            // Android 12+ refuses a foreground service from the background; the next open of the app starts it.
        }
    }
}
