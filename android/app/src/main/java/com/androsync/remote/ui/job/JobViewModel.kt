package com.androsync.remote.ui.job

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.androsync.remote.data.api.ApiException
import com.androsync.remote.data.api.JobDto
import com.androsync.remote.data.api.LogEvent
import com.androsync.remote.data.api.LogStream
import com.androsync.remote.ui.ConnectionViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

data class JobUiState(
    val loading: Boolean = true,
    val job: JobDto? = null,
    val logLines: List<String> = emptyList(),
    /** Set when the job can't be loaded at all (never got a `JobDto`), per PLAN.md task 20. */
    val error: String? = null,
)

/** One-shot effects [JobScreen] reacts to (task 30b: starting an undo navigates to its job). */
sealed class JobEvent {
    data class UndoStarted(val jobId: String) : JobEvent()
    /** The user confirmed a large plan (task 36d); the screen opens the new job. */
    data class ConfirmStarted(val jobId: String) : JobEvent()
    data class Error(val message: String) : JobEvent()
}

/**
 * Backs [JobScreen] (PLAN.md task 20): polls `GET /v1/jobs/<id>` every 2 s
 * until the job reaches a terminal state (then once more, to catch the
 * final summary), and separately streams `GET /v1/jobs/<id>/log` for live
 * log lines, reconnecting with exponential backoff if the SSE connection
 * drops while the job is still active.
 *
 * Reads the active [com.androsync.remote.data.api.ApiClient] from the
 * shared [connectionViewModel], same as [com.androsync.remote.ui.pairs.PairsViewModel] -
 * both the poll and the stream loop re-check it on every iteration in case
 * the reachable transport changes mid-job.
 */
class JobViewModel(
    private val jobId: String,
    private val connectionViewModel: ConnectionViewModel,
) : ViewModel() {

    private val _uiState = MutableStateFlow(JobUiState())
    val uiState: StateFlow<JobUiState> = _uiState.asStateFlow()

    private val _events = Channel<JobEvent>(Channel.BUFFERED)
    val events: Flow<JobEvent> = _events.receiveAsFlow()

    private var pollJob: Job? = null
    private var streamJob: Job? = null

    init {
        pollJob = viewModelScope.launch { pollLoop() }
        streamJob = viewModelScope.launch { streamLoop() }
    }

    fun cancel() {
        viewModelScope.launch {
            val client = connectionViewModel.activeApiClient.value ?: return@launch
            runCatching { client.cancelJob(jobId) }
            connectionViewModel.refreshNow()
        }
    }

    /**
     * "Go ahead" on a sync the laptop stopped for being large (task 36d, issue #9): runs the pairs that were
     * stopped again, this time with `confirm_large`.
     */
    fun confirmLarge(pairNames: List<String>) {
        viewModelScope.launch {
            val client = connectionViewModel.activeApiClient.value
            if (client == null) {
                _events.trySend(JobEvent.Error("Laptop not reachable"))
                return@launch
            }
            try {
                val response = client.run(pairNames, confirmLarge = true)
                _events.trySend(JobEvent.ConfirmStarted(response.jobId))
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiException) {
                _events.trySend(JobEvent.Error(e.message))
            } catch (e: Exception) {
                _events.trySend(JobEvent.Error(e.message ?: "Could not start the sync"))
            }
        }
    }

    /** Starts undoing [pairName]'s run [runId] (task 30b); [JobScreen] navigates to the new job on success. */
    fun undo(pairName: String, runId: String?, pause: Boolean) {
        viewModelScope.launch {
            val client = connectionViewModel.activeApiClient.value
            if (client == null) {
                _events.trySend(JobEvent.Error("Laptop not reachable"))
                return@launch
            }
            try {
                val response = client.undo(pairName, runId = runId, pause = pause)
                _events.trySend(JobEvent.UndoStarted(response.jobId))
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiException) {
                _events.trySend(JobEvent.Error(e.message))
            } catch (e: Exception) {
                _events.trySend(JobEvent.Error(e.message ?: "Undo failed"))
            }
        }
    }

    private suspend fun pollLoop() {
        var extraPollDone = false
        while (currentCoroutineContext().isActive) {
            val client = connectionViewModel.activeApiClient.value
            if (client == null) {
                _uiState.update { it.copy(loading = false, error = "Laptop not reachable") }
            } else {
                try {
                    val job = client.job(jobId)
                    _uiState.update { it.copy(loading = false, job = job, error = null) }
                    if (isTerminalState(job.state)) {
                        if (extraPollDone) return
                        extraPollDone = true
                    }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: ApiException) {
                    _uiState.update { it.copy(loading = false, error = e.message) }
                } catch (e: Exception) {
                    _uiState.update { it.copy(loading = false, error = e.message ?: "Failed to load job") }
                }
            }
            delay(POLL_INTERVAL_MS)
        }
    }

    private suspend fun streamLoop() {
        var since = 0
        var backoffMs = INITIAL_BACKOFF_MS
        while (currentCoroutineContext().isActive) {
            if (isTerminalState(_uiState.value.job?.state)) return
            val client = connectionViewModel.activeApiClient.value
            if (client == null) {
                delay(backoffMs)
                continue
            }
            try {
                val url = client.jobLogUrl(jobId, since)
                LogStream(client.sseClient, url).events().collect { event ->
                    when (event) {
                        is LogEvent.Line -> {
                            since = event.seq + 1
                            _uiState.update { it.copy(logLines = appendLogLine(it.logLines, event.text)) }
                            backoffMs = INITIAL_BACKOFF_MS
                        }
                        is LogEvent.End -> Unit // the flow completes right after this frame.
                    }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // Dropped connection; fall through to the backoff/reconnect below
                // unless the job has since finished.
            }
            if (isTerminalState(_uiState.value.job?.state)) return
            delay(backoffMs)
            backoffMs = (backoffMs * 2).coerceAtMost(MAX_BACKOFF_MS)
        }
    }

    override fun onCleared() {
        pollJob?.cancel()
        streamJob?.cancel()
    }

    private companion object {
        const val POLL_INTERVAL_MS = 2_000L
        const val INITIAL_BACKOFF_MS = 1_000L
        const val MAX_BACKOFF_MS = 30_000L
    }
}
