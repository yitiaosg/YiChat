package org.yitiao.yichat

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.junit.Assert.*
import org.junit.Test
import java.io.IOException

class GatewayRecoveryTest {
    @Test fun readOnlyRequestRecoversClosedReusedConnection() = runBlocking<Unit> {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("{}"))
            server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST))
            server.enqueue(MockResponse().setBody("{\"ok\":true}"))
            server.start()
            val client=Gateway(server.url("/").toString().removeSuffix("/"))
            client.call("/warm",authenticated=false)
            assertTrue(client.call("/read",authenticated=false).toString().contains("true"))
            assertEquals(3,server.requestCount)
        }
    }
    @Test fun unknownWriteIsNotTransparentlyReissued() = runBlocking<Unit> {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("{}"))
            server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST))
            server.enqueue(MockResponse().setBody("{\"unexpected_repeat\":true}"))
            server.start()
            val client=Gateway(server.url("/").toString().removeSuffix("/"))
            client.call("/warm",authenticated=false)
            var failed=false
            try { client.call("/fund",obj(),authenticated=false) } catch(e: IOException) { failed=true }
            assertTrue(failed);assertEquals(2,server.requestCount)
        }
    }
    @Test fun unavailableWriteWithZeroRetryAfterIsStillExplicit() = runBlocking<Unit> {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setResponseCode(503).setHeader("Retry-After","0").setBody("{\"error\":\"dependency unavailable\"}"))
            server.enqueue(MockResponse().setBody("{\"unexpected_repeat\":true}"));server.start()
            val client=Gateway(server.url("/").toString().removeSuffix("/"))
            var failure: GatewayError?=null
            try { client.call("/fund",obj(),authenticated=false) } catch(e: GatewayError) { failure=e }
            assertEquals(503,failure?.status);assertEquals(1,server.requestCount)
        }
    }
}
