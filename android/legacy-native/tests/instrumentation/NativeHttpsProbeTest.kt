package org.yitiao.yichat

import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.matrix.rustcomponents.sdk.*

/** Anonymous transport probe only: no production credentials, rooms or writes. */
@RunWith(AndroidJUnit4::class)
class NativeHttpsProbeTest {
    @Test fun nativeRustAndGatewayReachSamePublicHttpsServer() = runBlocking<Unit> {
        val base = "https://chat.yitiao.org"
        val versions = Gateway(base).call("/_matrix/client/versions", authenticated = false)
        assertTrue(versions.toString().contains("versions"))
        println("PROBE OkHttp HTTPS versions OK")
        val client = ClientBuilder().homeserverUrl(base).disableWellKnownLookup(true)
            .slidingSyncVersionBuilder(SlidingSyncVersionBuilder.NONE)
            .requestConfig(RequestConfig(0uL, 15000uL, 1uL, 15000uL))
            .userAgent("YiChat-Android/anonymous-sync-probe").build()
        try {
            client.getProfile("@__anonymous_network_probe:chat.yitiao.org")
            fail("Gateway must reject anonymous profile access")
        } catch (e: ClientException.MatrixApi) {
            println("PROBE Rust HTTPS Matrix response: ${e.code}")
            assertEquals("M_UNKNOWN_TOKEN", e.code)
        } catch (e: Exception) {
            // No user session or token exists anywhere in this test.
            println("PROBE anonymous Rust HTTPS failure: ${e.javaClass.simpleName}: ${e.message}")
            throw e
        } finally { client.close() }
    }
}
