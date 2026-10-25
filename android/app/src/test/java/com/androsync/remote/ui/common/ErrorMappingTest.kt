package com.androsync.remote.ui.common

import com.androsync.remote.data.api.ApiException
import java.io.IOException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ErrorMappingTest {

    @Test
    fun `401 maps to TokenRejected with the exact snackbar copy`() {
        val error = mapApiError(ApiException(401, "unauthorized"))

        assertTrue(error is SyncError.TokenRejected)
        assertEquals("Token rejected, check Setup", snackbarMessageFor(error))
    }

    @Test
    fun `409 maps to Busy carrying the job id, with the exact snackbar copy`() {
        val error = mapApiError(ApiException(409, "busy", jobId = "ab12cd34"))

        assertTrue(error is SyncError.Busy)
        assertEquals("ab12cd34", (error as SyncError.Busy).jobId)
        assertEquals("A sync is already running", snackbarMessageFor(error))
    }

    @Test
    fun `409 without a job id still maps to Busy with a null job id`() {
        val error = mapApiError(ApiException(409, "busy"))

        assertTrue(error is SyncError.Busy)
        assertNull((error as SyncError.Busy).jobId)
    }

    @Test
    fun `other ApiException codes map to Connection with the daemon message`() {
        val error = mapApiError(ApiException(500, "boom"))

        assertTrue(error is SyncError.Connection)
        assertEquals("boom", snackbarMessageFor(error))
    }

    @Test
    fun `a plain exception maps to Connection with its message`() {
        val error = mapApiError(IOException("timeout"))

        assertTrue(error is SyncError.Connection)
        assertEquals("timeout", snackbarMessageFor(error))
    }

    @Test
    fun `an exception with no message falls back to a generic Connection error`() {
        val error = mapApiError(RuntimeException())

        assertTrue(error is SyncError.Connection)
        assertEquals("Connection error", snackbarMessageFor(error))
    }
}
