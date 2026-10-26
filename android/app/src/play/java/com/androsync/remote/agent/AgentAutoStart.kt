package com.androsync.remote.agent

import android.content.Context

/** The `play` build has no agent, so there is nothing to bring back. */
object AgentAutoStart {
    @Suppress("UNUSED_PARAMETER")
    fun ensureRunning(context: Context) = Unit
}
