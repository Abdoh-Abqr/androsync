package com.androsync.remote.data.api

/**
 * Thrown by [ApiClient] for any non-2xx HTTP response. [message] is taken
 * from the daemon's `{"error": "..."}` JSON body when present, otherwise a
 * generic "HTTP <code>" string.
 */
class ApiException(
    val code: Int,
    override val message: String,
    val jobId: String? = null,
) : Exception(message)
