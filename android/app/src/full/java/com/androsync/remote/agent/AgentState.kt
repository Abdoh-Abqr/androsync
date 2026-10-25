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
}
