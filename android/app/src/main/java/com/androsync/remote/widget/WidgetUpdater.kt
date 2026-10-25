package com.androsync.remote.widget

import android.content.Context
import androidx.glance.appwidget.updateAll
import com.androsync.remote.AndroSyncApplication
import com.androsync.remote.data.api.ApiClient
import com.androsync.remote.data.api.ProbeOutcome
import com.androsync.remote.data.settings.Preset
import kotlinx.coroutines.flow.first

/**
 * Refreshes [SyncWidget] (PLAN.md task 21): re-probes the daemon, pulls
 * `/v1/pairs` if reachable, caches the result in DataStore via
 * [WidgetState], and asks Glance to redraw every placed instance.
 *
 * Called from [SyncNowAction] (right after a manual sync) and from
 * [com.androsync.remote.service.SyncForegroundService] (when a job started
 * elsewhere - Pairs screen or the tile - finishes), plus the system's own
 * `updatePeriodMillis` tick (declared in `res/xml/sync_widget_info.xml`).
 * Silently keeps the last cached state on failure - there's no error UI on
 * a home-screen widget.
 */
suspend fun refreshWidgets(context: Context) {
    val container = (context.applicationContext as AndroSyncApplication).container
    val settings = container.settingsRepository

    val token = settings.token.first()
    if (token.isNotBlank()) {
        val preset = settings.preset.first()
        val lanUrl = if (preset == Preset.LAN) settings.baseUrl.first() else null
        val tailscaleUrl = settings.tailscaleUrl.first()

        val outcome = container.connectionProbe.probe(token, lanUrl, tailscaleUrl)
        if (outcome is ProbeOutcome.Reachable) {
            val client = ApiClient(outcome.result.baseUrl, token)
            val pairs = runCatching { client.pairs() }.getOrNull()
            if (pairs != null) {
                settings.setWidgetStateJson(encodeWidgetState(widgetStateFromPairs(pairs)))
            }
        }
    }

    SyncWidget().updateAll(context)
}
