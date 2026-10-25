package com.androsync.remote.data.api

import okhttp3.Interceptor
import okhttp3.Response

/** Adds `Authorization: Bearer <token>` to every request; the daemon requires it on every route. */
class AuthInterceptor(private val token: String) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request().newBuilder()
            .header("Authorization", "Bearer $token")
            .build()
        return chain.proceed(request)
    }
}
