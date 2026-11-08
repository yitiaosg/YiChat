package org.yitiao.yichat

import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class AppUpdatesTest {
    private fun release(tag: String, vararg assets: Pair<String, String>) = buildJsonObject {
        put("tag_name", tag); put("html_url", "https://github.com/yitiaosg/YiChat/releases/tag/$tag"); put("body", "## 0.3.8\n- 修复")
        put("assets", buildJsonArray { assets.forEach { (name, url) -> add(buildJsonObject { put("name", name); put("browser_download_url", url) }) } })
    }
    @Test fun parsesApkAssetAndComparesVersionCode() {
        val r = requireNotNull(ReleaseInfo.parse(release("android-20261108-v0.3.8",
            "SHA256SUMS" to "https://github.com/yitiaosg/YiChat/releases/download/android-20261108-v0.3.8/SHA256SUMS",
            "YiChat-0.3.8-16.apk" to "https://github.com/yitiaosg/YiChat/releases/download/android-20261108-v0.3.8/YiChat-0.3.8-16.apk")))
        assertEquals("0.3.8", r.versionName); assertEquals(16, r.versionCode)
        assertTrue(ReleaseInfo.newer(r, "0.3.7", 15))
        assertFalse(ReleaseInfo.newer(r, "0.3.8", 16))
        assertFalse(ReleaseInfo.newer(r, "0.3.9", 17))
    }
    @Test fun rejectsForeignOrDraftDownloads() {
        assertNull(ReleaseInfo.parse(release("v9.9.9", "YiChat-9.9.9-99.apk" to "https://evil.example/YiChat-9.9.9-99.apk")))
        assertNull(ReleaseInfo.parse(release("v9.9.9", "YiChat-9.9.9-99.apk" to "https://github.com/other/YiChat/releases/download/v9.9.9/YiChat-9.9.9-99.apk")))
        val draft = buildJsonObject { release("v9.9.9", "YiChat-9.9.9-99.apk" to "https://github.com/yitiaosg/YiChat/releases/download/v9.9.9/YiChat-9.9.9-99.apk").forEach { (k, v) -> put(k, v) }; put("draft", true) }
        assertNull(ReleaseInfo.parse(draft))
        assertFalse(ReleaseInfo.trustedApk("https://github.com/yitiaosg/YiChat/releases/download/../x.apk"))
        assertFalse(ReleaseInfo.trustedApk("http://github.com/yitiaosg/YiChat/releases/download/v1/YiChat-1.0.0-1.apk"))
    }
    @Test fun semanticVersionFallback() {
        assertTrue(ReleaseInfo.compareVersions("0.3.10", "0.3.9") > 0)
        assertEquals(0, ReleaseInfo.compareVersions("0.4", "0.4.0"))
        val r = requireNotNull(ReleaseInfo.parse(release("android-20261108-v0.4.0", "app-release.apk" to "https://github.com/yitiaosg/YiChat/releases/download/android-20261108-v0.4.0/app-release.apk")))
        assertEquals(-1, r.versionCode); assertTrue(ReleaseInfo.newer(r, "0.3.7", 15))
    }
}
