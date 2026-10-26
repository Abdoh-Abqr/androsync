package com.androsync.remote.agent

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** What the agent service is doing right now, for the Settings screen. `null` = the service is not running. */
object AgentState {
    private val _status = MutableStateFlow<String?>(null)
    val status: StateFlow<String?> = _status.asStateFlow()

    fun set(text: String?) {
        _status.value = text
    }

    private const val PREFS = "agent"
    private const val KEY_ENABLED = "enabled"
    private const val KEY_ID = "agent_id"
    private const val KEY_PAUSE_MOBILE = "pause_on_mobile"

    /** "Pause on mobile data": when on, the agent does not poll or work while the phone uses mobile data. */
    fun pauseOnMobile(context: Context): Boolean =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean(KEY_PAUSE_MOBILE, false)

    fun setPauseOnMobile(context: Context, on: Boolean) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean(KEY_PAUSE_MOBILE, on).apply()
    }

    /** A random id for this install, so the laptop can tell two phones' agents apart. Made once, then kept. */
    fun agentId(context: Context): String {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        return prefs.getString(KEY_ID, null) ?: java.util.UUID.randomUUID().toString().take(12).also {
            prefs.edit().putString(KEY_ID, it).apply()
        }
    }

    /** Whether the user switched agent mode on; the service restarts itself with the app process while true. */
    fun isEnabled(context: Context): Boolean =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean(KEY_ENABLED, false)

    fun setEnabled(context: Context, enabled: Boolean) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean(KEY_ENABLED, enabled).apply()
    }
}

/** Human text for an [AgentStatus]. */
fun describe(status: AgentStatus): String = when (status) {
    AgentStatus.Starting -> "Starting"
    AgentStatus.Connected -> "Connected, waiting for the laptop"
    is AgentStatus.Working -> "Working: ${status.operation}"
    is AgentStatus.Reconnecting -> "Reconnecting (${status.reason})"
    is AgentStatus.Paused -> "Paused: ${status.reason}"
}
