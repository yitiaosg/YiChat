package org.yitiao.yichat

import kotlinx.coroutines.*
import org.junit.Assert.*
import org.junit.Test
import org.matrix.rustcomponents.sdk.ClientException
import org.matrix.rustcomponents.sdk.ErrorKind
import java.io.IOException

class SyncRecoveryTest {
    @Test fun transientFailureRecoversAndNextFailureStartsWithShortBackoff() = runBlocking {
        val first = mutableListOf<Boolean>()
        val retries = mutableListOf<Long>()
        syncWithRecovery(sync = {
            first.add(it)
            if (first.size != 3) throw IOException("offline")
        }, failed = { _, retry -> retries.add(retry); retries.size < 3 }, wait = {})
        assertEquals(listOf(true, true, true, false), first)
        assertEquals(listOf(1000L, 2000L, 1000L), retries)
    }
    @Test fun serverRetryAfterIsNeverShortened() = runBlocking {
        var calls = 0
        val waits = mutableListOf<Long>()
        syncWithRecovery(sync = {
            calls++
            throw ClientException.MatrixApi(ErrorKind.LimitExceeded(120000uL), "M_LIMIT_EXCEEDED", "wait", null)
        }, failed = { _, _ -> calls < 2 }, wait = { waits.add(it) })
        assertEquals(listOf(120000L), waits)
    }
    @Test fun lifecycleCancellationDoesNotStartRetry() = runBlocking {
        var failures = 0
        try {
            syncWithRecovery(sync = { throw CancellationException("background") }, failed = { _, _ -> failures++; true })
            fail("Cancellation must propagate")
        } catch (_: CancellationException) { }
        assertEquals(0, failures)
    }
    @Test fun hungRequestReachesFailureInsteadOfKeepingSyncingForever() = runBlocking {
        var error: Exception? = null
        syncWithRecovery(sync = { delay(10000) }, failed = { e, _ -> error = e; false }, timeoutMs = 10)
        assertTrue(error is TimeoutCancellationException)
    }
}
