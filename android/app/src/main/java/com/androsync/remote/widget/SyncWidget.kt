package com.androsync.remote.widget

import android.content.ComponentName
import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.LocalContext
import androidx.glance.action.actionStartActivity
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.cornerRadius
import androidx.glance.appwidget.provideContent
import androidx.glance.background
import androidx.glance.color.ColorProvider
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.padding
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import com.androsync.remote.AndroSyncApplication
import com.androsync.remote.ui.MainActivity
import com.androsync.remote.ui.common.formatRelativeTime
import kotlinx.coroutines.flow.first

/** Colors are set explicitly: Glance widgets render on a transparent surface by
 * default, and a plain [Text] with no background reads as inert label text
 * rather than a button - both looked broken on a real device before this. */
private val WidgetBackground = ColorProvider(day = Color(0xFFFFFFFF), night = Color(0xFF1E1E1E))
private val WidgetForeground = ColorProvider(day = Color(0xFF1B1B1F), night = Color(0xFFE3E2E6))
private val ButtonBackground = ColorProvider(day = Color(0xFF1A73E8), night = Color(0xFF8AB4F8))
private val ButtonForeground = ColorProvider(day = Color(0xFFFFFFFF), night = Color(0xFF062E6F))

/**
 * Home-screen widget (PLAN.md task 21): last sync result, its relative
 * time, and a "Sync now" button, plus tap-anywhere-else to open the app.
 * State is read from DataStore (cached by [refreshWidgets]) rather than
 * fetched here, so drawing the widget never blocks on the network.
 */
class SyncWidget : GlanceAppWidget() {

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val container = (context.applicationContext as AndroSyncApplication).container
        val state = decodeWidgetState(container.settingsRepository.widgetStateJson.first())

        provideContent {
            WidgetContent(state)
        }
    }
}

@Composable
private fun WidgetContent(state: WidgetState) {
    val context = LocalContext.current
    Column(
        modifier = GlanceModifier
            .fillMaxSize()
            .background(WidgetBackground)
            .cornerRadius(16.dp)
            .padding(12.dp)
            .clickable(actionStartActivity(ComponentName(context, MainActivity::class.java))),
    ) {
        Text("AndroSync", style = TextStyle(fontWeight = FontWeight.Bold, color = WidgetForeground))
        Text(state.lastResultLine, style = TextStyle(color = WidgetForeground))
        Text(formatRelativeTime(state.lastRunIso) ?: "Never synced", style = TextStyle(color = WidgetForeground))
        Box(
            modifier = GlanceModifier
                .padding(top = 8.dp)
                .background(ButtonBackground)
                .cornerRadius(8.dp)
                .padding(horizontal = 12.dp, vertical = 6.dp)
                .clickable(actionRunCallback<SyncNowAction>()),
        ) {
            Text("Sync now", style = TextStyle(fontWeight = FontWeight.Bold, color = ButtonForeground))
        }
    }
}
