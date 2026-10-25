package com.androsync.remote.tile

import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import com.androsync.remote.AndroSyncApplication
import com.androsync.remote.data.api.ApiException
import com.androsync.remote.data.api.ProbeOutcome
import com.androsync.remote.data.settings.Preset
import com.androsync.remote.ui.common.ConnectionState
import com.androsync.remote.ui.common.connectionBadgeLabel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

/**
 * Quick Settings tile "AndroSync" (PLAN.md task 21): tap syncs every pair
 * through [com.androsync.remote.data.SyncLauncher]; the subtitle otherwise
 * shows which transport currently reaches the laptop (or why it doesn't).
 */
class SyncTileService : TileService() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private val container get() = (applicationContext as AndroSyncApplication).container

    override fun onStartListening() {
        super.onStartListening()
        scope.launch { refreshSubtitle() }
    }

    override fun onClick() {
        super.onClick()
        scope.launch {
            setTile("Syncing…", Tile.STATE_ACTIVE)
            try {
                container.syncLauncher.start(pairs = null)
                refreshSubtitle()
            } catch (e: ApiException) {
                if (e.code == 409) {
                    setTile("Already running", Tile.STATE_ACTIVE)
                } else {
                    setTile("Laptop not reachable", Tile.STATE_UNAVAILABLE)
                }
            } catch (e: Exception) {
                setTile("Laptop not reachable", Tile.STATE_UNAVAILABLE)
            }
        }
    }

    private suspend fun refreshSubtitle() {
        val settings = container.settingsRepository
        val token = settings.token.first()
        if (token.isBlank()) {
            setTile("Not set up", Tile.STATE_UNAVAILABLE)
            return
        }
        val preset = settings.preset.first()
        val lanUrl = if (preset == Preset.LAN) settings.baseUrl.first() else null
        val tailscaleUrl = settings.tailscaleUrl.first()

        when (val outcome = container.connectionProbe.probe(token, lanUrl, tailscaleUrl)) {
            is ProbeOutcome.Reachable -> setTile(
                connectionBadgeLabel(
                    ConnectionState.Connected(
                        via = outcome.result.preset,
                        baseUrl = outcome.result.baseUrl,
                        version = outcome.result.ping.version,
                    ),
                ),
                Tile.STATE_INACTIVE,
            )
            is ProbeOutcome.Unreachable -> setTile("Laptop not reachable", Tile.STATE_UNAVAILABLE)
        }
    }

    /**
     * [state] is [Tile.STATE_ACTIVE] only while a sync is actually in flight (or another one is
     * already running); otherwise [Tile.STATE_INACTIVE] (idle, available) or
     * [Tile.STATE_UNAVAILABLE] (tapping it would just fail right now). Every call used to force
     * `STATE_ACTIVE`, so the tile appeared permanently "on" even at rest.
     */
    private fun setTile(subtitle: String, state: Int) {
        val tile = qsTile ?: return
        tile.label = "AndroSync"
        tile.state = state
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            tile.subtitle = subtitle
        }
        tile.updateTile()
    }

    override fun onDestroy() {
        super.onDestroy()
        scope.cancel()
    }
}
