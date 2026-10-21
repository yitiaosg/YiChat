package org.yitiao.yichat

import org.junit.Assert.*
import org.junit.Test

class BrowserUrlsTest {
    @Test fun loginAndWalletKeepHttpsOrigin() {
        assertTrue(BrowserUrls.allowed("https://chat.yitiao.org/auth/native/login?state=opaque"))
        assertTrue(BrowserUrls.allowed("https://yitiao.org/oidc/authorize"))
        assertTrue(BrowserUrls.allowed("https://yidt.yitiao.org/services/yichat/"))
        for (url in listOf("http://chat.yitiao.org/", "https://chat.yitiao.org:8443/", "https://chat.yitiao.org.evil.test/", "https://evil@chat.yitiao.org/", "javascript:alert(1)", "intent://chat.yitiao.org/", "https://chat.yitiao.org\\@evil.test/"))
            assertFalse(url, BrowserUrls.allowed(url))
    }
}
