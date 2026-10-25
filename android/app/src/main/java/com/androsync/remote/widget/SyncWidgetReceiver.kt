package com.androsync.remote.widget

import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver

/** Registers [SyncWidget] with the launcher (PLAN.md task 21); see `res/xml/sync_widget_info.xml`. */
class SyncWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = SyncWidget()
}
