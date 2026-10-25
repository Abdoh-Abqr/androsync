package com.androsync.remote.agent

import java.io.File
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okio.Buffer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/** The HTTP side of the agent against a scripted laptop (MockWebServer). Task 37c (issue #15). */
class AgentLoopTest {
    @get:Rule
    val tmp = TemporaryFolder()

    private lateinit var server: MockWebServer
    private lateinit var client: AgentClient

    @Before
    fun setUp() {
        server = MockWebServer().also { it.start() }
        client = AgentClient(server.url("/").toString(), "tok", pollWaitSeconds = 1)
    }

    @After
    fun tearDown() {
        client.close()
        server.shutdown()
    }

    private fun opJson(id: String, op: String, args: String, upload: Boolean = false, download: Boolean = false) =
        """{"id":"$id","op":"$op","args":$args,"upload":$upload,"download":$download}"""

    @Test
    fun `next returns null on 204 and parses an operation on 200`() {
        server.enqueue(MockResponse().setResponseCode(204))
        server.enqueue(MockResponse().setBody(opJson("a1", "exists", """{"root":"/x"}""")))
        assertNull(client.next())
        val op = client.next()!!
        assertEquals("a1", op.id)
        assertEquals("exists", op.name)
        assertEquals("/x", op.args["root"]!!.jsonPrimitive.content)
        val request = server.takeRequest()
        assertEquals("Bearer tok", request.getHeader("Authorization"))
        assertTrue(request.path!!.startsWith("/v1/agent/next?wait=1"))
    }

    @Test
    fun `the loop runs an operation and posts the result`() = runBlocking {
        val root = tmp.newFolder("phone")
        server.enqueue(MockResponse().setBody(opJson("op7", "exists", """{"root":"${root.path}"}""")))
        server.enqueue(MockResponse().setBody("""{"ok":true}"""))
        val statuses = mutableListOf<AgentStatus>()
        val loop = AgentLoop(client, AgentOps(guard = { it }), onStatus = { statuses += it })
        val job = launch(Dispatchers.IO) { loop.run() }
        withTimeout(10_000) { while (server.requestCount < 2) kotlinx.coroutines.delay(20) }
        job.cancel()
        server.takeRequest() // the poll
        val post = server.takeRequest()
        assertEquals("POST", post.method)
        assertEquals("/v1/agent/result/op7", post.path)
        val body = Json.parseToJsonElement(post.body.readUtf8()).jsonObject
        assertTrue(body["ok"]!!.jsonPrimitive.boolean)
        assertTrue(body["result"]!!.jsonObject["exists"]!!.jsonPrimitive.boolean)
        assertTrue(statuses.any { it is AgentStatus.Working && it.operation == "exists" })
    }

    @Test
    fun `a failing operation is reported with its message and the loop carries on`() = runBlocking {
        server.enqueue(MockResponse().setBody(opJson("bad", "format_disk", "{}")))
        server.enqueue(MockResponse().setBody("""{"ok":true}"""))
        val loop = AgentLoop(client, AgentOps(guard = { it }))
        val job = launch(Dispatchers.IO) { loop.run() }
        withTimeout(10_000) { while (server.requestCount < 2) kotlinx.coroutines.delay(20) }
        job.cancel()
        server.takeRequest()
        val body = Json.parseToJsonElement(server.takeRequest().body.readUtf8()).jsonObject
        assertEquals(false, body["ok"]!!.jsonPrimitive.boolean)
        assertTrue(body["error"]!!.jsonPrimitive.content.contains("unknown operation"))
    }

    @Test
    fun `store downloads the body from the blob endpoint, then answers`() = runBlocking {
        val root = tmp.newFolder("phone")
        val args = """{"root":"${root.path}","rel":"d/f.txt","mtime":1700000000.0,"size":5}"""
        server.enqueue(MockResponse().setBody(opJson("s1", "store", args, upload = true)))
        server.enqueue(MockResponse().setBody(Buffer().writeUtf8("hello")))
        server.enqueue(MockResponse().setBody("""{"ok":true}"""))
        val job = launch(Dispatchers.IO) { AgentLoop(client, AgentOps(guard = { it })).run() }
        withTimeout(10_000) { while (server.requestCount < 3) kotlinx.coroutines.delay(20) }
        job.cancel()
        assertEquals("hello", File(root, "d/f.txt").readText())
        assertEquals(1_700_000_000_000L, File(root, "d/f.txt").lastModified())
        server.takeRequest()
        assertEquals("/v1/agent/blob/s1", server.takeRequest().path)
        assertEquals("/v1/agent/result/s1", server.takeRequest().path)
    }

    @Test
    fun `fetch puts the bytes to the blob endpoint with a content length`() = runBlocking {
        val root = tmp.newFolder("phone")
        File(root, "p.bin").writeText("twelve bytes")
        server.enqueue(MockResponse().setBody(opJson("f1", "fetch", """{"root":"${root.path}","rel":"p.bin"}""", download = true)))
        server.enqueue(MockResponse().setBody("""{"ok":true,"size":12}"""))
        server.enqueue(MockResponse().setBody("""{"ok":true}"""))
        val job = launch(Dispatchers.IO) { AgentLoop(client, AgentOps(guard = { it })).run() }
        withTimeout(10_000) { while (server.requestCount < 3) kotlinx.coroutines.delay(20) }
        job.cancel()
        server.takeRequest()
        val put = server.takeRequest()
        assertEquals("PUT", put.method)
        assertEquals("/v1/agent/blob/f1", put.path)
        assertEquals("12", put.getHeader("Content-Length"))
        assertEquals("twelve bytes", put.body.readUtf8())
    }

    @Test
    fun `a cancelled operation (410) is dropped without an error report`() = runBlocking {
        val root = tmp.newFolder("phone")
        server.enqueue(MockResponse().setBody(opJson("gone", "exists", """{"root":"${root.path}"}""")))
        server.enqueue(MockResponse().setResponseCode(410).setBody("""{"error":"cancelled"}"""))
        server.enqueue(MockResponse().setResponseCode(204))
        val statuses = mutableListOf<AgentStatus>()
        val job = launch(Dispatchers.IO) { AgentLoop(client, AgentOps(guard = { it }), onStatus = { statuses += it }).run() }
        withTimeout(10_000) { while (server.requestCount < 3) kotlinx.coroutines.delay(20) }
        job.cancel()
        assertTrue(statuses.none { it is AgentStatus.Reconnecting })
    }

    @Test
    fun `network trouble makes the loop wait and reconnect, with growing pauses`() = runBlocking {
        server.shutdown() // nothing listens any more: every poll fails
        val pauses = mutableListOf<Long>()
        val reasons = mutableListOf<String>()
        val loop = AgentLoop(
            client,
            AgentOps(guard = { it }),
            onStatus = { if (it is AgentStatus.Reconnecting) reasons += it.reason },
            sleep = { pauses += it; if (pauses.size >= 4) throw kotlinx.coroutines.CancellationException("enough") },
        )
        try {
            withTimeout(20_000) { loop.run() }
        } catch (_: kotlinx.coroutines.CancellationException) {
        }
        assertEquals(listOf(2_000L, 4_000L, 8_000L, 16_000L), pauses)
        assertTrue(reasons.isNotEmpty())
    }

    @Test
    fun `after the allowed number of failed polls in a row the loop returns so the caller can look for the laptop again`() = runBlocking {
        server.shutdown()
        var pauses = 0
        val loop = AgentLoop(client, AgentOps(guard = { it }), sleep = { pauses++ }, maxConsecutiveFailures = 3)
        withTimeout(20_000) { loop.run() }
        assertEquals(2, pauses) // slept after failures 1 and 2, returned on the 3rd
    }
}
