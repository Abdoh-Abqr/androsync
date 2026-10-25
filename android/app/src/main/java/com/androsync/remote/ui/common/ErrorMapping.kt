package com.androsync.remote.ui.common

import com.androsync.remote.data.api.ApiException

/**
 * User-facing classification of a failed daemon call (PLAN.md task 19
 * "Error handling": 401 -> token rejected, 409 -> a job is already running,
 * anything else (timeouts, refused connections, 5xx...) -> a generic
 * connection error carrying the underlying message).
 */
sealed class SyncError {
    data object TokenRejected : SyncError()
    data class Busy(val jobId: String?) : SyncError()
    data class Connection(val message: String) : SyncError()
}

/** Maps an exception thrown by [com.androsync.remote.data.api.ApiClient] to a [SyncError]. */
fun mapApiError(throwable: Throwable): SyncError = when {
    throwable is ApiException && throwable.code == 401 -> SyncError.TokenRejected
    throwable is ApiException && throwable.code == 409 -> SyncError.Busy(throwable.jobId)
    throwable is ApiException -> SyncError.Connection(throwable.message)
    else -> SyncError.Connection(throwable.message ?: "Connection error")
}

/** Snackbar copy for a [SyncError] - the exact strings PLAN.md task 19 specifies for 401/409. */
fun snackbarMessageFor(error: SyncError): String = when (error) {
    SyncError.TokenRejected -> "Token rejected, check Setup"
    is SyncError.Busy -> "A sync is already running"
    is SyncError.Connection -> error.message
}
