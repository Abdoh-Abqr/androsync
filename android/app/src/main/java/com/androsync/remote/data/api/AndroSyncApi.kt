package com.androsync.remote.data.api

import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path
import retrofit2.http.Query

/**
 * Retrofit interface for the androsync daemon's HTTP API (see PLAN.md "HTTP API").
 *
 * Every function returns a raw [Response] rather than throwing on non-2xx,
 * so [ApiClient] can uniformly turn error bodies into [ApiException] in one
 * place instead of relying on interceptor-thrown exceptions (which do not
 * propagate safely through Retrofit's coroutine adapter on all call paths).
 */
interface AndroSyncApi {

    @GET("v1/ping")
    suspend fun ping(): Response<PingDto>

    @GET("v1/pairs")
    suspend fun pairs(): Response<List<PairDto>>

    @POST("v1/pairs")
    suspend fun createPair(@Body request: PairRequest): Response<List<PairDto>>

    @PUT("v1/pairs/{name}")
    suspend fun updatePair(@Path("name") name: String, @Body request: PairRequest): Response<List<PairDto>>

    @DELETE("v1/pairs/{name}")
    suspend fun deletePair(
        @Path("name") name: String,
        @Query("purge_state") purgeState: Int = 0,
    ): Response<List<PairDto>>

    @GET("v1/browse")
    suspend fun browse(@Query("side") side: String, @Query("path") path: String): Response<BrowseDto>

    @POST("v1/token/rotate")
    suspend fun rotateToken(@Body body: Map<String, String> = emptyMap()): Response<TokenResponse>

    @POST("v1/mkdir")
    suspend fun mkdir(@Body request: MkdirRequest): Response<MkdirResponse>

    @GET("v1/pending/{pair}")
    suspend fun pending(@Path("pair") pair: String): Response<List<PendingEntryDto>>

    @GET("v1/conflicts/{pair}")
    suspend fun conflicts(@Path("pair") pair: String): Response<List<ConflictVersionDto>>

    @POST("v1/conflicts/{pair}/restore")
    suspend fun restoreConflict(
        @Path("pair") pair: String,
        @Body request: ConflictRestoreRequest,
    ): Response<ConflictRestoreResponse>

    @POST("v1/conflicts/{pair}/clean")
    suspend fun cleanConflicts(
        @Path("pair") pair: String,
        @Body request: ConflictsCleanRequest,
    ): Response<ConflictsCleanResponse>

    @GET("v1/status/{pair}")
    suspend fun status(@Path("pair") pair: String): Response<StatusDto>

    @POST("v1/run")
    suspend fun run(@Body request: RunRequest): Response<RunResponse>

    @GET("v1/jobs")
    suspend fun jobs(): Response<List<JobDto>>

    @GET("v1/jobs/{id}")
    suspend fun job(@Path("id") id: String): Response<JobDto>

    @POST("v1/jobs/{id}/cancel")
    suspend fun cancelJob(@Path("id") id: String): Response<OkDto>

    @GET("v1/runs/{pair}")
    suspend fun runs(@Path("pair") pair: String): Response<List<RunDto>>

    @POST("v1/undo/{pair}")
    suspend fun undo(@Path("pair") pair: String, @Body request: UndoRequest): Response<RunResponse>
}
