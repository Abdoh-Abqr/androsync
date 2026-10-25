package com.androsync.remote

import android.content.Context
import com.androsync.remote.data.SyncLauncher
import com.androsync.remote.data.api.ConnectionProbe
import com.androsync.remote.data.settings.SettingsRepository

/**
 * Hand-rolled dependency container (no Hilt, per PLAN.md task 18): holds the
 * app-scoped singletons that ViewModels need. One instance lives for the
 * process lifetime on [AndroSyncApplication]; screens reach it through
 * `LocalContext.current.applicationContext as AndroSyncApplication` and build
 * their `ViewModelProvider.Factory` from it (see `ui/AppViewModelProvider.kt`).
 *
 * Non-Compose entry points that have no ViewModel of their own -
 * `tile/SyncTileService`, `widget/SyncNowAction`, `service/SyncForegroundService` -
 * reach the same singletons the same way, via `applicationContext as AndroSyncApplication`.
 */
class AppContainer(context: Context) {
    val settingsRepository: SettingsRepository = SettingsRepository(context)
    val connectionProbe: ConnectionProbe = ConnectionProbe()

    /** PLAN.md task 20: the one place that POSTs `/v1/run` and starts the foreground service. */
    val syncLauncher: SyncLauncher = SyncLauncher(context.applicationContext, settingsRepository, connectionProbe)
}
