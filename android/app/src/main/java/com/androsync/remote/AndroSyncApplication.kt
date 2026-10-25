package com.androsync.remote

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import com.androsync.remote.service.SyncForegroundService
import com.androsync.remote.ui.setup.SetupHintNotifier
import com.androsync.remote.ui.setup.UsbStateReceiver

/** Owns the process-lifetime [AppContainer] (see its kdoc for why there's no Hilt here). */
class AndroSyncApplication : Application() {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
        createNotificationChannel()
        SetupHintNotifier.createChannel(this)
        UsbStateReceiver.register(this, container.settingsRepository)
    }

    /**
     * One low-importance channel for [SyncForegroundService]'s progress and
     * final notifications (PLAN.md task 20). minSdk is 26 (Android O), the
     * same release that introduced notification channels, so this always
     * runs - no `Build.VERSION.SDK_INT` guard needed.
     */
    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            SyncForegroundService.CHANNEL_ID,
            "Sync progress",
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "Shows progress and the result of a sync started from this phone."
        }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }
}
