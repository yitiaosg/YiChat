package org.yitiao.yichat

import kotlinx.coroutines.*
import org.matrix.rustcomponents.sdk.ClientException
import org.matrix.rustcomponents.sdk.ErrorKind

/** syncV2's listener reports successes only; own the loop so failures cannot strand it. */
internal suspend fun syncWithRecovery(
    sync: suspend (first: Boolean) -> Unit,
    failed: (Exception, retryMs: Long) -> Boolean,
    wait: suspend (Long) -> Unit = { delay(it) },
    timeoutMs: Long = 90000,
) {
    var first = true
    var failures = 0
    while (currentCoroutineContext().isActive) {
        val error = try {
            withTimeout(timeoutMs) { sync(first) }
            first = false
            failures = 0
            wait(250) // Also pace homeservers that return empty syncs immediately.
            continue
        } catch (e: TimeoutCancellationException) {
            currentCoroutineContext().ensureActive()
            e
        } catch (e: CancellationException) { throw e }
        catch (e: Exception) { e }
        val retryAfter = ((error as? ClientException.MatrixApi)?.kind as? ErrorKind.LimitExceeded)
            ?.retryAfterMs?.coerceAtMost(Long.MAX_VALUE.toULong())?.toLong() ?: 0L
        val retry = maxOf(1000L shl failures.coerceAtMost(5), retryAfter)
        if (!failed(error, retry)) return
        failures = (failures + 1).coerceAtMost(5)
        wait(retry)
    }
}
