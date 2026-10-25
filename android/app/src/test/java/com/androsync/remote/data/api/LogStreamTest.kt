package com.androsync.remote.data.api

import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import java.util.concurrent.TimeUnit

/**
 * Verifies [LogStream] against a fixed SSE body shaped like
 * `daemon/server.py`'s `_h_job_log`: `id: <seq>\ndata: <line>\n\n` frames
 * followed by a terminal `event: end\ndata: <state>\n\n` frame.
 */
class LogStreamTest {

    private lateinit var server: MockWebServer
    private lateinit var client: OkHttpClient

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        client = OkHttpClient.Builder()
            .readTimeout(0, TimeUnit.SECONDS)
            .build()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun `parses two data events and an end event`() = runTest {
        val sse = "id: 0\ndata: 12:00:00 photos: copy DCIM/a.jpg\n\n" +
            "id: 1\ndata: 12:00:01 photos: done\n\n" +
            "event: end\ndata: done\n\n"

        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "text/event-stream")
                .setBody(sse),
        )

        val url = server.url("/v1/jobs/abcd1234/log").toString()
        val events = LogStream(client, url).events().toList()

        assertEquals(3, events.size)

        val first = events[0] as LogEvent.Line
        assertEquals(0, first.seq)
        assertEquals("12:00:00 photos: copy DCIM/a.jpg", first.text)

        val second = events[1] as LogEvent.Line
        assertEquals(1, second.seq)
        assertEquals("12:00:01 photos: done", second.text)

        val end = events[2] as LogEvent.End
        assertEquals("done", end.state)
    }
}
