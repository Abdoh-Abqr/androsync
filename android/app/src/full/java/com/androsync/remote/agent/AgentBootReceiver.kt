package com.androsync.remote.agent

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * Starts the agent again after the phone restarts or the app is updated (an update kills the service), but only if
 * the user had it switched on and All files access is still allowed (task 37c). Without this the agent stayed off
 * until the app was opened.
 */
class AgentBootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action !in RESTART_ACTIONS) return
        AgentAutoStart.ensureRunning(context)
    }

    private companion object {
        val RESTART_ACTIONS = setOf(Intent.ACTION_BOOT_COMPLETED, Intent.ACTION_MY_PACKAGE_REPLACED)
    }
}
