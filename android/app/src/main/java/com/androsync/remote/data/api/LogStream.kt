package com.androsync.remote.data.api

import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources

/** One event parsed from `GET /v1/jobs/<id>/log`'s `text/event-stream` body. */
sealed class LogEvent {
    /** A plain `id: <seq>\ndata: <line>\n\n` frame - one log line from the job. */
    data class Line(val seq: Int, val text: String) : LogEvent()

    /** The terminal `event: end\ndata: <state>\n\n` frame; the stream closes right after. */
    data class End(val state: String) : LogEvent()
}

/**
 * Wraps OkHttp's SSE [EventSource] as a cold [Flow] of [LogEvent].
 *
 * [client] should already carry the auth interceptor (see [ApiClient.sseClient])
 * and have no read timeout, since the daemon holds this connection open -
 * sending `: ping` keep-alive comments (which the SSE parser swallows,
 * never reaching [EventSourceListener.onEvent]) - for as long as the job runs.
 */
class LogStream(private val client: OkHttpClient, private val url: String) {

    fun events(): Flow<LogEvent> = callbackFlow {
        val request = Request.Builder().url(url).build()

        val listener = object : EventSourceListener() {
            override fun onEvent(
                eventSource: EventSource,
                id: String?,
                type: String?,
                data: String,
            ) {
                val event = if (type == "end") {
                    LogEvent.End(state = data)
                } else {
                    LogEvent.Line(seq = id?.toIntOrNull() ?: 0, text = data)
                }
                trySend(event)
                if (event is LogEvent.End) {
                    close()
                }
            }

            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                close(t)
            }

            override fun onClosed(eventSource: EventSource) {
                close()
            }
        }

        val eventSource = EventSources.createFactory(client).newEventSource(request, listener)
        awaitClose { eventSource.cancel() }
    }
}
