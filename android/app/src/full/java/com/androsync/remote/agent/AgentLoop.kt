package com.androsync.remote.agent

import java.io.IOException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlin.coroutines.coroutineContext

/** What the loop is doing, for the notification and the Settings screen. */
sealed class AgentStatus {
    data object Starting : AgentStatus()
    data object Connected : AgentStatus()
    data class Working(val operation: String) : AgentStatus()
    data class Reconnecting(val reason: String) : AgentStatus()
}

/**
 * The agent's main loop (task 37c, issue #15): ask the laptop for work, do it, answer, repeat, until the coroutine is
 * cancelled. It never gives up on network trouble: it waits (2 s, doubling to 30 s) and asks again.
 *
 * The blocking HTTP and file calls run on the calling thread, so launch this on `Dispatchers.IO`.
 */
class AgentLoop(
    private val client: AgentClient,
    private val ops: AgentOps,
    private val onStatus: (AgentStatus) -> Unit = {},
    private val onBusy: (Boolean) -> Unit = {},
    private val sleep: suspend (Long) -> Unit = { delay(it) },
    /** After this many failed polls in a row [run] returns, so the caller can look for the laptop again. */
    private val maxConsecutiveFailures: Int = Int.MAX_VALUE,
) {
    /** Runs until cancelled, or until [maxConsecutiveFailures] polls failed in a row (then returns). */
    suspend fun run() {
        var backoffMs = FIRST_BACKOFF_MS
        var failures = 0
        onStatus(AgentStatus.Starting)
        while (true) {
            coroutineContext.ensureActive()
            try {
                val op = client.next()
                backoffMs = FIRST_BACKOFF_MS
                failures = 0
                onStatus(AgentStatus.Connected)
                if (op != null) handle(op)
            } catch (e: CancellationException) {
                throw e
            } catch (e: IOException) {
                onStatus(AgentStatus.Reconnecting(e.message ?: e.javaClass.simpleName))
                if (++failures >= maxConsecutiveFailures) return
                sleep(backoffMs)
                backoffMs = minOf(backoffMs * 2, MAX_BACKOFF_MS)
            }
        }
    }

    private fun handle(op: AgentOp) {
        onStatus(AgentStatus.Working(op.name))
        onBusy(true)
        try {
            val result = ops.run(op, client)
            client.result(op.id, result, null)
        } catch (e: OperationGone) {
            // The laptop cancelled it (Stop) or gave up: nothing to report.
        } catch (e: AgentException) {
            reportFailure(op, e.message ?: "failed")
        } catch (e: IOException) {
            // A network error while moving a body: tell the laptop if we still can, else the poll loop reconnects.
            reportFailure(op, "${op.name}: ${e.message ?: e.javaClass.simpleName}")
        } catch (e: RuntimeException) {
            // A bug or a malformed request must fail this one operation, never kill the loop.
            reportFailure(op, "${op.name}: ${e.javaClass.simpleName}: ${e.message}")
        } finally {
            onBusy(false)
            onStatus(AgentStatus.Connected)
        }
    }

    private fun reportFailure(op: AgentOp, message: String) {
        try {
            client.result(op.id, null, message)
        } catch (_: IOException) {
            // The laptop is unreachable too; its own timeout will fail the operation.
        }
    }

    companion object {
        const val FIRST_BACKOFF_MS = 2_000L
        const val MAX_BACKOFF_MS = 30_000L
    }
}
