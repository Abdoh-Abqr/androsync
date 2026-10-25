package com.androsync.remote.data

import android.content.Context
import com.androsync.remote.data.api.ApiClient
import com.androsync.remote.data.api.ApiException
import com.androsync.remote.data.api.ConnectionProbe
import com.androsync.remote.data.api.ProbeOutcome
import com.androsync.remote.data.settings.Preset
import com.androsync.remote.data.settings.SettingsRepository
import com.androsync.remote.service.SyncForegroundService
import kotlinx.coroutines.flow.first

/**
 * The one place that starts a sync job (PLAN.md task 20: "Put the 'start a
 * job' logic in one place"). Every entry point that can kick off a
 * sync - the Pairs screen's Sync/Sync all, the Quick Settings tile, and the
 * widget's "Sync now" button - calls [start] instead of talking to
 * [ApiClient] directly, so they all resolve the reachable transport, POST
 * `/v1/run` and start [SyncForegroundService] the same way.
 *
 * Unlike [com.androsync.remote.ui.ConnectionViewModel] (which keeps a
 * long-lived poll loop), [start] always probes fresh: the tile and widget
 * have no running poll loop to reuse, and probing is a handful of short
 * HTTP calls at worst.
 */
class SyncLauncher(
    private val context: Context,
    private val settingsRepository: SettingsRepository,
    private val connectionProbe: ConnectionProbe,
    private val clientFactory: (baseUrl: String, token: String) -> ApiClient = { url, token ->
        ApiClient(url, token)
    },
) {
    /** [pairs] null means "sync all". Returns the new job id, or throws [ApiException]. */
    suspend fun start(pairs: List<String>?, dryRun: Boolean = false): String {
        val token = settingsRepository.token.first()
        if (token.isBlank()) throw ApiException(code = 401, message = "No token saved")

        val lanUrl = settingsRepository.lanUrl.first()
        val tailscaleUrl = settingsRepository.tailscaleUrl.first()

        val result = when (val outcome = connectionProbe.probe(token, lanUrl, tailscaleUrl)) {
            is ProbeOutcome.Reachable -> outcome.result
            is ProbeOutcome.Unreachable -> throw ApiException(code = 0, message = outcome.lastError)
        }

        val client = clientFactory(result.baseUrl, token)
        val response = client.run(pairs = pairs, dryRun = dryRun)

        SyncForegroundService.start(
            context = context,
            jobId = response.jobId,
            baseUrl = result.baseUrl,
            token = token,
            pairs = pairs ?: emptyList(),
        )

        return response.jobId
    }
}
