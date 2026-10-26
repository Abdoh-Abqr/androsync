package com.androsync.remote.ui

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import com.androsync.remote.ui.setup.ConnectPhoneScreen
import com.androsync.remote.ui.support.SupportScreen
import androidx.navigation.compose.rememberNavController
import com.androsync.remote.ui.common.ConnectionState
import com.androsync.remote.ui.job.JobScreen
import com.androsync.remote.ui.jobs.JobsScreen
import com.androsync.remote.ui.common.LocalJobActions
import com.androsync.remote.ui.common.JobActions
import com.androsync.remote.ui.pairs.PairEditorScreen
import com.androsync.remote.ui.pairs.PairsScreen
import com.androsync.remote.ui.settings.SettingsScreen
import com.androsync.remote.ui.setup.SetupScreen
import com.androsync.remote.ui.theme.AndroSyncRemoteTheme
import kotlinx.coroutines.flow.first

/**
 * Placeholder for task 17 became the real app in tasks 18-19: a Navigation
 * Compose graph over `setup`/`pairs`/`job/{id}`/`settings`, with a single
 * shared [ConnectionViewModel] polling connectivity for as long as the app
 * is in the foreground.
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            AndroSyncRemoteTheme {
                AndroSyncApp()
            }
        }
    }
}

@Composable
fun AndroSyncApp() {
    val container = rememberAppContainer()
    val connectionViewModel = rememberConnectionViewModel()

    // PLAN.md task 18: poll only while the app is in the foreground.
    LifecycleEventEffect(Lifecycle.Event.ON_START) { connectionViewModel.start() }
    LifecycleEventEffect(Lifecycle.Event.ON_STOP) { connectionViewModel.stop() }

    val connectionState by connectionViewModel.connectionState.collectAsStateWithLifecycle()

    // One-shot read to decide the start destination: "setup" if no token saved, else "pairs".
    var startDestinationToken by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) {
        startDestinationToken = container.settingsRepository.token.first()
    }

    Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        val token = startDestinationToken
        if (token == null) {
            Box(Modifier.fillMaxSize()) {
                CircularProgressIndicator(Modifier.align(Alignment.Center))
            }
        } else {
            AndroSyncNavHost(
                startDestination = if (token.isBlank()) Routes.SETUP else Routes.PAIRS,
                connectionViewModel = connectionViewModel,
                connectionState = connectionState,
            )
        }
    }
}

@Composable
private fun AndroSyncNavHost(
    startDestination: String,
    connectionViewModel: ConnectionViewModel,
    connectionState: ConnectionState,
) {
    val navController = rememberNavController()
    // The "sync running" bar under every top bar opens the live log and stops the sync (task 36b, issue #7).
    CompositionLocalProvider(
        LocalJobActions provides JobActions(
            onOpen = { id -> navController.navigate(Routes.job(id)) },
            onStop = connectionViewModel::stopJob,
        ),
    ) {
    NavHost(navController = navController, startDestination = startDestination) {
        composable(Routes.SETUP) {
            SetupScreen(
                connectionState = connectionState,
                onConnectPhone = { navController.navigate(Routes.CONNECT) },
                onSaved = {
                    navController.navigate(Routes.PAIRS) {
                        popUpTo(Routes.SETUP) { inclusive = true }
                    }
                },
            )
        }
        composable(Routes.SETUP_EDIT) {
            // Re-scan the QR code (or retype the addresses) for the laptop that is in use now.
            SetupScreen(
                connectionState = connectionState,
                onConnectPhone = { navController.navigate(Routes.CONNECT) },
                onSaved = {
                    connectionViewModel.refreshNow()
                    navController.navigate(Routes.PAIRS) { popUpTo(Routes.PAIRS) { inclusive = true } }
                },
            )
        }
        composable(Routes.SETUP_ADD) {
            SetupScreen(
                connectionState = connectionState,
                onConnectPhone = { navController.navigate(Routes.CONNECT) },
                addNew = true,
                onSaved = {
                    connectionViewModel.refreshNow()
                    navController.navigate(Routes.PAIRS) { popUpTo(Routes.PAIRS) { inclusive = true } }
                },
            )
        }
        composable(Routes.PAIRS) {
            PairsScreen(
                connectionViewModel = connectionViewModel,
                onNavigateToJob = { id -> navController.navigate(Routes.job(id)) },
                onNavigateToSetup = { navController.navigate(Routes.SETUP) },
                onNavigateToSettings = { navController.navigate(Routes.SETTINGS) },
                onNavigateToJobs = { navController.navigate(Routes.JOBS) },
                onNavigateToConnect = { navController.navigate(Routes.CONNECT) },
                onNavigateToAddPair = { navController.navigate(Routes.PAIR_NEW) },
                onNavigateToEditPair = { name -> navController.navigate(Routes.pairEdit(name)) },
            )
        }
        composable(Routes.PAIR_NEW) {
            PairEditorScreen(
                pairName = null,
                connectionState = connectionState,
                connectionViewModel = connectionViewModel,
                onBack = { navController.popBackStack() },
                onSaved = { navController.popBackStack() },
                onDeleted = { navController.popBackStack() },
            )
        }
        composable(Routes.PAIR_EDIT_PATTERN) { backStackEntry ->
            val name = backStackEntry.arguments?.getString("name").orEmpty()
            PairEditorScreen(
                pairName = name,
                connectionState = connectionState,
                connectionViewModel = connectionViewModel,
                onBack = { navController.popBackStack() },
                onSaved = { navController.popBackStack() },
                onDeleted = { navController.popBackStack() },
            )
        }
        composable(Routes.JOB_PATTERN) { backStackEntry ->
            val jobId = backStackEntry.arguments?.getString("jobId").orEmpty()
            JobScreen(
                jobId = jobId,
                connectionState = connectionState,
                connectionViewModel = connectionViewModel,
                onBack = { navController.popBackStack() },
                onNavigateToJob = { id -> navController.navigate(Routes.job(id)) },
            )
        }
        composable(Routes.JOBS) {
            JobsScreen(
                connectionState = connectionState,
                connectionViewModel = connectionViewModel,
                onBack = { navController.popBackStack() },
                onOpenJob = { id -> navController.navigate(Routes.job(id)) },
            )
        }
        composable(Routes.CONNECT) {
            ConnectPhoneScreen(
                connectionState = connectionState,
                onBack = { navController.popBackStack() },
            )
        }
        composable(Routes.SUPPORT) {
            SupportScreen(
                connectionState = connectionState,
                apiClient = connectionViewModel.activeApiClient.collectAsStateWithLifecycle().value,
                onBack = { navController.popBackStack() },
            )
        }
        composable(Routes.SETTINGS) {
            SettingsScreen(
                connectionState = connectionState,
                onBack = { navController.popBackStack() },
                onConnectPhone = { navController.navigate(Routes.CONNECT) },
                onAddLaptop = { navController.navigate(Routes.SETUP_ADD) },
                onOpenSupport = { navController.navigate(Routes.SUPPORT) },
                onEditLaptop = { navController.navigate(Routes.SETUP_EDIT) },
                onLaptopChanged = connectionViewModel::refreshNow,
                onForgotSetup = {
                    navController.navigate(Routes.SETUP) {
                        popUpTo(0) { inclusive = true }
                    }
                },
            )
        }
    }
    }
}
