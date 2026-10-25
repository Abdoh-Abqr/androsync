package com.androsync.remote.service

import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.androsync.remote.AndroSyncApplication
import com.androsync.remote.R
import com.androsync.remote.data.api.ApiClient
import com.androsync.remote.data.api.JobDto
import com.androsync.remote.ui.MainActivity
import com.androsync.remote.ui.job.isTerminalState
import com.androsync.remote.widget.refreshWidgets
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Keeps a sync job alive on screen off (PLAN.md task 20): started by
 * [com.androsync.remote.data.SyncLauncher] right after `POST /v1/run`
 * succeeds, it polls `GET /v1/jobs/<id>` every 2 s, updates a persistent
 * "Syncing …" notification with the latest log line, and posts a final
 * "Sync done: …"/"Sync failed" notification before stopping itself.
 *
 * The notification's Cancel action re-delivers this same service with
 * [ACTION_CANCEL], which just calls `POST /v1/jobs/<id>/cancel` - the
 * running poll loop picks up the resulting `cancelled` state on its own.
 */
class SyncForegroundService : Service() {

    private val serviceJob = SupervisorJob()
    private val serviceScope = CoroutineScope(Dispatchers.IO + serviceJob)
    private var pollingJob: Job? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_CANCEL) {
            handleCancel(intent)
            return START_NOT_STICKY
        }

        val jobId = intent?.getStringExtra(EXTRA_JOB_ID)
        val baseUrl = intent?.getStringExtra(EXTRA_BASE_URL)
        val token = intent?.getStringExtra(EXTRA_TOKEN)
        val pairs = intent?.getStringArrayListExtra(EXTRA_PAIRS)?.toList() ?: emptyList()

        if (jobId == null || baseUrl == null || token == null) {
            stopSelf()
            return START_NOT_STICKY
        }

        // Must be called within a few seconds of the service starting (Android O+).
        startForeground(PROGRESS_NOTIFICATION_ID, buildProgressNotification(jobId, baseUrl, token, pairs, null))

        pollingJob?.cancel()
        pollingJob = serviceScope.launch { pollUntilFinished(jobId, baseUrl, token, pairs) }

        return START_NOT_STICKY
    }

    private fun handleCancel(intent: Intent) {
        val jobId = intent.getStringExtra(EXTRA_JOB_ID) ?: return
        val baseUrl = intent.getStringExtra(EXTRA_BASE_URL) ?: return
        val token = intent.getStringExtra(EXTRA_TOKEN) ?: return
        serviceScope.launch {
            runCatching { ApiClient(baseUrl, token).cancelJob(jobId) }
        }
    }

    private suspend fun pollUntilFinished(jobId: String, baseUrl: String, token: String, pairs: List<String>) {
        val client = ApiClient(baseUrl, token)
        var extraPollDone = false
        while (true) {
            val job = runCatching { client.job(jobId) }.getOrNull()
            if (job != null) {
                updateProgressNotification(jobId, baseUrl, token, pairs, job.logTail.lastOrNull())
                if (isTerminalState(job.state)) {
                    if (extraPollDone) {
                        postFinalNotification(job)
                        break
                    }
                    extraPollDone = true
                }
            }
            delay(POLL_INTERVAL_MS)
        }
        runCatching { refreshWidgets(applicationContext) }
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun buildProgressNotification(
        jobId: String,
        baseUrl: String,
        token: String,
        pairs: List<String>,
        lastLogLine: String?,
    ): android.app.Notification {
        val content = progressNotificationContent(pairs, lastLogLine)
        val openAppIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val cancelIntent = PendingIntent.getService(
            this,
            0,
            Intent(this, SyncForegroundService::class.java).apply {
                action = ACTION_CANCEL
                putExtra(EXTRA_JOB_ID, jobId)
                putExtra(EXTRA_BASE_URL, baseUrl)
                putExtra(EXTRA_TOKEN, token)
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(content.title)
            .setContentText(content.text)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(openAppIntent)
            .addAction(0, "Cancel", cancelIntent)
            .build()
    }

    private fun updateProgressNotification(
        jobId: String,
        baseUrl: String,
        token: String,
        pairs: List<String>,
        lastLogLine: String?,
    ) {
        val notification = buildProgressNotification(jobId, baseUrl, token, pairs, lastLogLine)
        if (hasNotificationPermission()) {
            NotificationManagerCompat.from(this).notify(PROGRESS_NOTIFICATION_ID, notification)
        }
    }

    private fun postFinalNotification(job: JobDto) {
        val content = finalNotificationContent(job)
        val openAppIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(content.title)
            .setContentText(content.text)
            .setAutoCancel(true)
            .setContentIntent(openAppIntent)
            .build()
        if (hasNotificationPermission()) {
            NotificationManagerCompat.from(this).notify(FINAL_NOTIFICATION_ID, notification)
        }
    }

    private fun hasNotificationPermission(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return true
        return ContextCompat.checkSelfPermission(
            this,
            android.Manifest.permission.POST_NOTIFICATIONS,
        ) == android.content.pm.PackageManager.PERMISSION_GRANTED
    }

    override fun onDestroy() {
        super.onDestroy()
        serviceJob.cancel()
    }

    companion object {
        /** Matches the channel created once in [AndroSyncApplication]. */
        const val CHANNEL_ID = "sync_progress"
        private const val PROGRESS_NOTIFICATION_ID = 42
        private const val FINAL_NOTIFICATION_ID = 43
        private const val POLL_INTERVAL_MS = 2_000L

        private const val ACTION_CANCEL = "com.androsync.remote.action.CANCEL"
        private const val EXTRA_JOB_ID = "job_id"
        private const val EXTRA_BASE_URL = "base_url"
        private const val EXTRA_TOKEN = "token"
        private const val EXTRA_PAIRS = "pairs"

        fun start(context: Context, jobId: String, baseUrl: String, token: String, pairs: List<String>) {
            val intent = Intent(context, SyncForegroundService::class.java).apply {
                putExtra(EXTRA_JOB_ID, jobId)
                putExtra(EXTRA_BASE_URL, baseUrl)
                putExtra(EXTRA_TOKEN, token)
                putStringArrayListExtra(EXTRA_PAIRS, ArrayList(pairs))
            }
            ContextCompat.startForegroundService(context, intent)
        }
    }
}
