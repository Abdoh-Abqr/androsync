package com.androsync.remote.agent

import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okio.BufferedSink
import okio.source

/** The laptop dropped or cancelled this operation (HTTP 404 or 410). Not an error: poll again. */
class OperationGone(message: String) : IOException(message)

/**
 * The agent's side of the HTTP protocol in `docs/agent-protocol.md`: long-poll for work, move file bodies, answer.
 * [pollWaitSeconds] is how long the laptop may hold `GET /v1/agent/next` (25 s on the wire, short in tests).
 */
class AgentClient(
    baseUrl: String,
    private val token: String,
    private val pollWaitSeconds: Int = 25,
    /** Names this phone's app to the laptop, which lets only one phone's agent work at a time. */
    private val agentId: String? = null,
    /** Extra query text for each poll, like `&net=wifi&battery=charging`: how the phone is connected and powered. */
    private val statusQuery: () -> String = { "" },
) : AgentBlobs {
    private val base = baseUrl.trimEnd('/')
    private val json = Json { ignoreUnknownKeys = true }

    // One client for everything: no overall timeout, because a 1 GiB body may take minutes; the read timeout only has to
    // outlast the laptop's 25 s long poll.
    private val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout((pollWaitSeconds + 20).toLong(), TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    private fun request(path: String) = Request.Builder().url("$base$path").header("Authorization", "Bearer $token")

    /** Next operation, or null when the poll timed out with nothing to do. */
    fun next(): AgentOp? {
        http.newCall(request("/v1/agent/next?wait=$pollWaitSeconds" + (agentId?.let { "&agent=$it" } ?: "") + statusQuery()).get().build()).execute().use { response ->
            if (response.code == 204) return null
            if (response.code == 409) throw IOException("another phone's agent is already connected to this laptop")
            if (!response.isSuccessful) throw IOException("laptop answered ${response.code} to /v1/agent/next")
            val body = json.parseToJsonElement(response.body!!.string()).jsonObject
            return AgentOp(
                id = body.getValue("id").jsonPrimitive.content,
                name = body.getValue("op").jsonPrimitive.content,
                args = body["args"]?.jsonObject ?: JsonObject(emptyMap()),
                upload = body["upload"]?.jsonPrimitive?.boolean ?: false,
                download = body["download"]?.jsonPrimitive?.boolean ?: false,
            )
        }
    }

    /** Answer an operation. [error] non-null means failure. */
    fun result(opId: String, result: JsonElement?, error: String?) {
        val payload = buildJsonObject {
            put("ok", error == null)
            put("result", result ?: JsonNull)
            if (error != null) put("error", error)
        }
        val body = payload.toString().toRequestBody("application/json".toMediaType())
        http.newCall(request("/v1/agent/result/$opId").post(body).build()).execute().use { checkGone(it.code, "result") }
    }

    override fun download(opId: String, into: File) {
        http.newCall(request("/v1/agent/blob/$opId").get().build()).execute().use { response ->
            checkGone(response.code, "blob download")
            into.parentFile?.mkdirs()
            response.body!!.byteStream().use { input -> into.outputStream().use { input.copyTo(it, 1 shl 20) } }
        }
    }

    override fun upload(opId: String, from: File) {
        val body = object : RequestBody() {
            override fun contentType() = "application/octet-stream".toMediaType()
            override fun contentLength() = from.length()
            override fun writeTo(sink: BufferedSink) {
                from.source().use { sink.writeAll(it) }
            }
        }
        http.newCall(request("/v1/agent/blob/$opId").put(body).build()).execute().use { checkGone(it.code, "blob upload") }
    }

    private fun checkGone(code: Int, what: String) {
        if (code == 404 || code == 410) throw OperationGone("laptop dropped the operation ($what, HTTP $code)")
        if (code !in 200..299) throw IOException("laptop answered HTTP $code to $what")
    }

    fun close() {
        http.dispatcher.executorService.shutdown()
        http.connectionPool.evictAll()
    }
}
