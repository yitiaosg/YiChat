package org.yitiao.yichat

import org.junit.Assert.*
import org.junit.Test

class WebRoutesTest {
    @Test fun chatRootAndRecordPagesStayInsideTheApp() {
        assertTrue(WebRoutes.chat("https://chat.yitiao.org/?order="+"a".repeat(32)))
        for(url in listOf("https://chat.yitiao.org/auth/login","https://chat.yitiao.org/orders/id","https://yitiao.org/","https://yidt.yitiao.org/","https://chat.yitiao.org.evil.test/","https://user@chat.yitiao.org/","file:///data/local/tmp/","javascript:alert(1)")) assertFalse(url,WebRoutes.chat(url))
        assertTrue(WebRoutes.chat("https://chat.yitiao.org/records/"+"a".repeat(64)))
        assertFalse(WebRoutes.chat("https://chat.yitiao.org/records/invalid"))
        assertFalse(WebRoutes.chat("https://chat.yitiao.org/records/"+"a".repeat(64)+"?redirect=evil"))
        assertTrue(WebRoutes.external("https://yidt.yitiao.org/"))
        assertFalse(WebRoutes.external("intent://evil/"))
    }
    @Test fun recordPagesUseTheSameOfflineShell() {
        assertEquals("/",WebRoutes.shellPath("https://chat.yitiao.org/records/"+"a".repeat(64)))
        assertEquals("/render/ycards-render.js",WebRoutes.shellPath("https://chat.yitiao.org/render/ycards-render.js"))
        for(url in listOf("https://chat.yitiao.org/api/session","https://evil.test/records/"+"a".repeat(64),"https://chat.yitiao.org/records/invalid")) assertNull(WebRoutes.shellPath(url))
    }
    @Test fun cacheNeverAcceptsAuthenticatedDataOrTraversal() {
        assertTrue(WebRoutes.publicAsset("/assets/matrix-crypto.wasm"))
        assertTrue(WebRoutes.publicAsset("/render/ycards-render.js"))
        for(path in listOf("/api/session","/api/files/secret","/_matrix/client/v3/sync","/api/avatars/alice","/assets/../secrets.js","/assets/%2e%2e/secrets.js","/assets/private.json")) assertFalse(path,WebRoutes.publicAsset(path))
    }
}
