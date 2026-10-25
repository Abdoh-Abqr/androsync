package com.androsync.remote.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.androsync.remote.AndroSyncApplication
import com.androsync.remote.AppContainer
import com.androsync.remote.ui.job.JobViewModel
import com.androsync.remote.ui.jobs.JobsViewModel
import com.androsync.remote.ui.pairs.PairEditorViewModel
import com.androsync.remote.ui.pairs.PairsViewModel
import com.androsync.remote.ui.settings.SettingsViewModel
import com.androsync.remote.ui.setup.SetupViewModel

/**
 * Small ViewModel-factory helpers (no Hilt, per PLAN.md task 18): each reads
 * the process-lifetime [AppContainer] off [AndroSyncApplication] and builds
 * a `ViewModelProvider.Factory` with it via the `viewModelFactory { initializer { } }`
 * DSL, so screens get their ViewModel through the normal `viewModel()` API.
 */
@Composable
fun rememberAppContainer(): AppContainer {
    val context = LocalContext.current
    return remember(context) { (context.applicationContext as AndroSyncApplication).container }
}

/** The single shared [ConnectionViewModel] - obtain it once, high in the tree, and thread it down. */
@Composable
fun rememberConnectionViewModel(): ConnectionViewModel {
    val container = rememberAppContainer()
    val factory = remember(container) {
        viewModelFactory {
            initializer { ConnectionViewModel(container.settingsRepository, container.connectionProbe) }
        }
    }
    return viewModel(factory = factory)
}

@Composable
fun rememberSetupViewModel(): SetupViewModel {
    val container = rememberAppContainer()
    val factory = remember(container) {
        viewModelFactory { initializer { SetupViewModel(container.settingsRepository) } }
    }
    return viewModel(factory = factory)
}

/** [connectionViewModel] should be the shared instance from [rememberConnectionViewModel]. */
@Composable
fun rememberPairsViewModel(connectionViewModel: ConnectionViewModel): PairsViewModel {
    val container = rememberAppContainer()
    val factory = remember(connectionViewModel, container) {
        viewModelFactory { initializer { PairsViewModel(connectionViewModel, container.syncLauncher) } }
    }
    return viewModel(factory = factory)
}

/** [connectionViewModel] should be the shared instance from [rememberConnectionViewModel]. [pairName] null means "new pair". */
@Composable
fun rememberPairEditorViewModel(pairName: String?, connectionViewModel: ConnectionViewModel): PairEditorViewModel {
    val factory = remember(pairName, connectionViewModel) {
        viewModelFactory { initializer { PairEditorViewModel(pairName, connectionViewModel) } }
    }
    return viewModel(factory = factory, key = "pair-editor-${pairName ?: "new"}")
}

/** [connectionViewModel] should be the shared instance from [rememberConnectionViewModel]. */
@Composable
fun rememberJobViewModel(jobId: String, connectionViewModel: ConnectionViewModel): JobViewModel {
    val factory = remember(jobId, connectionViewModel) {
        viewModelFactory { initializer { JobViewModel(jobId, connectionViewModel) } }
    }
    return viewModel(factory = factory, key = "job-$jobId")
}

@Composable
fun rememberJobsViewModel(connectionViewModel: ConnectionViewModel): JobsViewModel {
    val factory = remember(connectionViewModel) {
        viewModelFactory { initializer { JobsViewModel(connectionViewModel) } }
    }
    return viewModel(factory = factory, key = "jobs")
}

@Composable
fun rememberSettingsViewModel(): SettingsViewModel {
    val container = rememberAppContainer()
    val factory = remember(container) {
        viewModelFactory {
            initializer { SettingsViewModel(container.settingsRepository, container.connectionProbe) }
        }
    }
    return viewModel(factory = factory)
}
