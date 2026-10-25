package com.androsync.remote.ui.setup

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.androsync.remote.R

/** The "Setup hints" channel and its one notification (task 37f, issue #10). */
object SetupHintNotifier {
    const val CHANNEL_ID = "setup_hints"
    private const val NOTIFICATION_ID = 4101

    fun createChannel(context: Context) {
        val channel = NotificationChannel(CHANNEL_ID, "Setup hints", NotificationManager.IMPORTANCE_DEFAULT).apply {
            description = "Tells you when the cable is plugged in but USB debugging is off."
        }
        context.getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    /** Posts the hint; tapping it opens Developer options directly. Silently does nothing without permission. */
    fun postDebuggingOff(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) return
        val open = Intent(SettingsPages.DEVELOPER_OPTIONS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            .putExtra(SettingsPages.EXTRA_FRAGMENT_ARG_KEY, SettingsPages.highlightKeyFor(StepAction.OPEN_DEVELOPER_OPTIONS))
        val tap = PendingIntent.getActivity(context, 0, open, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle("USB connected, USB debugging is off")
            .setContentText("Tap to open Developer options and switch it on.")
            .setContentIntent(tap)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(context).notify(NOTIFICATION_ID, notification)
    }
}
