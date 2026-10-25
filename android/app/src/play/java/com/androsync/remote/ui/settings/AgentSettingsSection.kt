package com.androsync.remote.ui.settings

import androidx.compose.runtime.Composable
import com.androsync.remote.ui.common.ConnectionState

/** The `play` build has no on-phone agent (it would need the all-files permission Google Play restricts): draws nothing. */
@Composable
@Suppress("UNUSED_PARAMETER")
fun AgentSettingsSection(connectionState: ConnectionState) = Unit
