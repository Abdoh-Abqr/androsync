package com.androsync.remote.ui.jobs

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.androsync.remote.data.api.ApiException
import com.androsync.remote.data.api.JobDto
import com.androsync.remote.ui.ConnectionViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class JobsUiState(
    val loading: Boolean = true,
    val jobs: List<JobDto> = emptyList(),
    val error: String? = null,
)

/** Recent syncs the laptop still remembers (`GET /v1/jobs`, newest first), task 36b / issue #7. */
class JobsViewModel(private val connectionViewModel: ConnectionViewModel) : ViewModel() {
    private val _uiState = MutableStateFlow(JobsUiState())
    val uiState: StateFlow<JobsUiState> = _uiState.asStateFlow()

    init {
        refresh()
        // The client appears a moment after the app opens; load again once there is one.
        viewModelScope.launch {
            connectionViewModel.activeApiClient.collect { client ->
                if (client != null && _uiState.value.jobs.isEmpty()) refresh()
            }
        }
    }

    fun refresh() {
        viewModelScope.launch {
            val client = connectionViewModel.activeApiClient.value
            if (client == null) {
                _uiState.update { it.copy(loading = false, error = "Laptop not reachable") }
                return@launch
            }
            _uiState.update { it.copy(loading = true, error = null) }
            try {
                val jobs = client.jobs()
                _uiState.update { JobsUiState(loading = false, jobs = jobs) }
            } catch (e: ApiException) {
                _uiState.update { it.copy(loading = false, error = e.message ?: "Could not load syncs") }
            } catch (e: java.io.IOException) {
                _uiState.update { it.copy(loading = false, error = e.message ?: "Could not load syncs") }
            }
        }
    }
}
