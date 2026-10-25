package com.androsync.remote.agent

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.MediaScannerConnection
import android.os.Build
import android.os.Environment
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.androsync.remote.AndroSyncApplication
import com.androsync.remote.R
import com.androsync.remote.data.api.ProbeOutcome
import com.androsync.remote.ui.MainActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

/**
 * Agent mode (task 37c, issue #15): keeps the phone available to the laptop without USB debugging.
 *
 * A foreground service finds the laptop the way the rest of the app does (USB loopback, then the saved Tailscale
 * address, then the saved LAN address), then runs [AgentLoop] against it: long-poll for file operations, do them
 * with [AgentOps], answer. When the laptop goes away it looks for it again every few seconds. It uses the
 * `specialUse` foreground type, not `dataSync`: Android 15 stops a `dataSync` service after six hours, and this
 * one is meant to keep running. That type is not allowed on Google Play, which is why this lives in the `full` build.
 */
class AgentService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var runJob: Job? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var lastText = "Starting"

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val channel = NotificationChannel(CHANNEL_ID, "Agent mode", NotificationManager.IMPORTANCE_LOW).apply {
            description = "Shows that this phone is available to your laptop without USB debugging."
        }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            AgentState.setEnabled(this, false)
            stopSelf()
            return START_NOT_STICKY
        }
        startInForeground(notification("Starting"))
        if (runJob?.isActive != true) runJob = scope.launch { runUntilStopped() }
        return START_STICKY
    }

    private fun startInForeground(notification: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private suspend fun runUntilStopped() {
        val container = (application as AndroSyncApplication).container
        while (true) {
            val token = container.settingsRepository.token.first()
            if (token.isBlank()) {
                show("Set up the laptop connection first")
                delay(RETRY_MS)
                continue
            }
            val outcome = container.connectionProbe.probe(
                token = token,
                lanUrl = container.settingsRepository.lanUrl.first(),
                tailscaleUrl = container.settingsRepository.tailscaleUrl.first(),
            )
            if (outcome !is ProbeOutcome.Reachable) {
                show("Looking for the laptop")
                delay(RETRY_MS)
                continue
            }
            val client = AgentClient(outcome.result.baseUrl, token, agentId = AgentState.agentId(applicationContext))
            val loop = AgentLoop(
                client = client,
                ops = AgentOps(onChanged = ::scanMedia),
                onStatus = { show(describe(it)) },
                onBusy = ::setBusy,
                maxConsecutiveFailures = 3,
            )
            try {
                loop.run()
            } finally {
                client.close()
                setBusy(false)
            }
        }
    }

    private fun show(text: String) {
        AgentState.set(text)
        if (text == lastText) return
        lastText = text
        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(NOTIFICATION_ID, notification(text))
    }

    private fun notification(text: String): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, AgentService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle("AndroSync agent")
            .setContentText(text)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(open)
            .addAction(0, "Stop", stop)
            .build()
    }

    /** Keep the CPU awake while an operation runs, so a long copy survives the screen turning off. */
    private fun setBusy(busy: Boolean) {
        if (busy) {
            if (wakeLock == null) {
                wakeLock = getSystemService(PowerManager::class.java)
                    .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "androsync:agent")
            }
            wakeLock?.acquire(WAKE_LOCK_MAX_MS)
        } else {
            wakeLock?.takeIf { it.isHeld }?.release()
        }
    }

    /** Let the gallery and music apps see (or forget) what the laptop wrote or deleted. */
    private fun scanMedia(file: java.io.File) {
        val path = file.path
        val storage = Environment.getExternalStorageDirectory().path
        val relative = path.removePrefix(storage).removePrefix("/sdcard").trimStart('/')
        if (MEDIA_FOLDERS.none { relative == it || relative.startsWith("$it/") }) return
        MediaScannerConnection.scanFile(applicationContext, arrayOf(path), null, null)
    }

    override fun onDestroy() {
        super.onDestroy()
        scope.coroutineContext[Job]?.cancel()
        wakeLock?.takeIf { it.isHeld }?.release()
        AgentState.set(null)
    }

    companion object {
        const val CHANNEL_ID = "agent_mode"
        private const val NOTIFICATION_ID = 4201
        private const val ACTION_STOP = "com.androsync.remote.action.STOP_AGENT"
        private const val RETRY_MS = 5_000L
        private const val WAKE_LOCK_MAX_MS = 10 * 60 * 1000L
        private val MEDIA_FOLDERS = listOf("DCIM", "Pictures", "Movies", "Music", "Download", "Documents")

        fun start(context: Context) {
            AgentState.setEnabled(context, true)
            ContextCompat.startForegroundService(context, Intent(context, AgentService::class.java))
        }

        fun stop(context: Context) {
            AgentState.setEnabled(context, false)
            context.stopService(Intent(context, AgentService::class.java))
        }
    }
}
