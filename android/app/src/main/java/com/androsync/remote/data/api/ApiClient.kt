package com.androsync.remote.data.api

import java.util.concurrent.TimeUnit
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Response
import retrofit2.Retrofit
import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory

/**
 * Talks to one androsync daemon at [baseUrl] using [token] for auth.
 *
 * Builds two OkHttp clients that share the auth interceptor and connect
 * timeout: a normal one (5 s connect / 10 s read, for request/response
 * calls) and [sseClient] (5 s connect / no read timeout, since an SSE log
 * stream is a long-lived connection that would otherwise be killed by a
 * finite read timeout the moment the daemon goes quiet between log lines).
 */
class ApiClient(val baseUrl: String, val token: String) {

    // encodeDefaults = true so RunRequest always sends explicit "pairs" and
    // "dry_run" fields even when they equal their Kotlin default (null/false)
    // - kotlinx.serialization omits default-valued properties otherwise.
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    private val authInterceptor = AuthInterceptor(token)

    private val httpClient: OkHttpClient = OkHttpClient.Builder()
        .addInterceptor(authInterceptor)
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .build()

    /** For [LogStream]: same auth + connect timeout, but no read timeout. */
    val sseClient: OkHttpClient = httpClient.newBuilder()
        .readTimeout(0, TimeUnit.SECONDS)
        .build()

    private val retrofit: Retrofit = Retrofit.Builder()
        .baseUrl(normalizedBaseUrl(baseUrl))
        .client(httpClient)
        .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
        .build()

    private val api: AndroSyncApi = retrofit.create(AndroSyncApi::class.java)

    suspend fun ping(): PingDto = unwrap(api.ping())

    suspend fun pairs(): List<PairDto> = unwrap(api.pairs())

    suspend fun createPair(request: PairRequest): List<PairDto> = unwrap(api.createPair(request))

    suspend fun updatePair(name: String, request: PairRequest): List<PairDto> =
        unwrap(api.updatePair(name, request))

    suspend fun deletePair(name: String, purgeState: Boolean = false): List<PairDto> =
        unwrap(api.deletePair(name, if (purgeState) 1 else 0))

    suspend fun browse(side: String, path: String): BrowseDto = unwrap(api.browse(side, path))

    suspend fun mkdir(side: String, path: String): MkdirResponse = unwrap(api.mkdir(MkdirRequest(side, path)))

    suspend fun pending(pair: String): List<PendingEntryDto> = unwrap(api.pending(pair))

    suspend fun conflicts(pair: String): List<ConflictVersionDto> = unwrap(api.conflicts(pair))

    suspend fun restoreConflict(pair: String, rel: String, whenStamp: String? = null): ConflictRestoreResponse =
        unwrap(api.restoreConflict(pair, ConflictRestoreRequest(rel = rel, whenStamp = whenStamp)))

    suspend fun cleanConflicts(pair: String, dryRun: Boolean = true): ConflictsCleanResponse =
        unwrap(api.cleanConflicts(pair, ConflictsCleanRequest(dryRun = dryRun)))

    suspend fun status(pair: String): StatusDto = unwrap(api.status(pair))

    suspend fun run(pairs: List<String>? = null, dryRun: Boolean = false, confirmLarge: Boolean = false): RunResponse =
        unwrap(api.run(RunRequest(pairs = pairs, dryRun = dryRun, confirmLarge = confirmLarge)))

    suspend fun jobs(): List<JobDto> = unwrap(api.jobs())

    suspend fun job(id: String): JobDto = unwrap(api.job(id))

    suspend fun cancelJob(id: String): OkDto = unwrap(api.cancelJob(id))

    suspend fun runs(pair: String): List<RunDto> = unwrap(api.runs(pair))

    suspend fun undo(
        pair: String,
        runId: String? = null,
        pause: Boolean = false,
        force: Boolean = false,
        dryRun: Boolean = false,
    ): RunResponse = unwrap(api.undo(pair, UndoRequest(runId = runId, dryRun = dryRun, force = force, pause = pause)))

    /**
     * `GET /v1/jobs/<id>/log?since=<seq>` as a plain URL, for [LogStream] to
     * open via [sseClient]. [since] lets a caller resume a dropped SSE
     * connection without replaying lines it already has (see the daemon's
     * `_h_job_log`, which treats `since` as inclusive and defaults to 0).
     */
    fun jobLogUrl(id: String, since: Int = 0): String =
        normalizedBaseUrl(baseUrl) + "v1/jobs/$id/log?since=$since"

    /**
     * Returns the parsed body of a successful response, or throws [ApiException]
     * built from the daemon's `{"error": "..."}` JSON body for any other status.
     */
    private fun <T> unwrap(response: Response<T>): T {
        if (response.isSuccessful) {
            return response.body() ?: throw ApiException(response.code(), "empty response body")
        }
        val raw = response.errorBody()?.string()
        val error = raw?.takeIf { it.isNotBlank() }?.let {
            runCatching { json.decodeFromString(ErrorDto.serializer(), it) }.getOrNull()
        }
        throw ApiException(
            code = response.code(),
            message = error?.error ?: "HTTP ${response.code()}",
            jobId = error?.jobId,
        )
    }

    companion object {
        /** USB preset: reachable once `adb reverse tcp:8765 tcp:8765` is set up. */
        const val PRESET_USB_URL = "http://127.0.0.1:8765"

        private fun normalizedBaseUrl(url: String): String =
            if (url.endsWith("/")) url else "$url/"
    }
}
