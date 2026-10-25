package com.androsync.remote.widget

import android.content.Context
import androidx.glance.GlanceId
import androidx.glance.action.ActionParameters
import androidx.glance.appwidget.action.ActionCallback
import com.androsync.remote.AndroSyncApplication

/**
 * The widget's "Sync now" button (PLAN.md task 21): syncs every pair
 * through [com.androsync.remote.data.SyncLauncher] (same code path as the
 * Pairs screen and the Quick Settings tile), then refreshes the widget so
 * it reflects whatever the connection probe found - a failure here (laptop
 * unreachable, 409 already running) just leaves the widget's last cached
 * state in place; there's no room to show an error on a widget.
 */
class SyncNowAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val container = (context.applicationContext as AndroSyncApplication).container
        runCatching { container.syncLauncher.start(pairs = null) }
        refreshWidgets(context)
    }
}
