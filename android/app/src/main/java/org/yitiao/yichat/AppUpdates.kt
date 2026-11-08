package org.yitiao.yichat

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*
import okhttp3.OkHttpClient
import okhttp3.Request
import java.net.URI
import java.util.concurrent.TimeUnit

/** Latest GitHub release metadata. The APK itself is downloaded by the system browser. */
data class AppRelease(val versionName: String, val versionCode: Int, val apkUrl: String, val pageUrl: String, val notes: String)

object ReleaseInfo {
    const val REPO = "yitiaosg/YiChat"
    const val LATEST_API = "https://api.github.com/repos/$REPO/releases/latest"
    const val LATEST_PAGE = "https://github.com/$REPO/releases/latest"
    private val apkName = Regex("YiChat-(\\d+(?:\\.\\d+){1,3})-(\\d+)\\.apk")
    private val tagVersion = Regex("v?(\\d+(?:\\.\\d+){1,3})$")

    /** Only GitHub release download links of this repository are opened. */
    fun trustedApk(value: String): Boolean = runCatching {
        val u = URI(value)
        u.scheme == "https" && u.host == "github.com" && u.port == -1 && u.rawUserInfo == null && u.rawQuery == null &&
            u.rawPath.startsWith("/$REPO/releases/download/") && !u.rawPath.contains("..") && u.rawPath.endsWith(".apk")
    }.getOrDefault(false)
    fun trustedPage(value: String): Boolean = runCatching {
        val u = URI(value)
        u.scheme == "https" && u.host == "github.com" && u.port == -1 && u.rawUserInfo == null && u.rawPath.startsWith("/$REPO/releases/")
    }.getOrDefault(false)

    fun parse(release: JsonObject): AppRelease? {
        if (release.b("draft") || release.b("prerelease")) return null
        val page = release.s("html_url").takeIf(::trustedPage) ?: LATEST_PAGE
        val assets = release.a("assets").mapNotNull { it as? JsonObject }
        for (asset in assets) {
            val match = apkName.matchEntire(asset.s("name")) ?: continue
            val url = asset.s("browser_download_url").takeIf(::trustedApk) ?: continue
            return AppRelease(match.groupValues[1], match.groupValues[2].toInt(), url, page, release.s("body").take(600))
        }
        val version = tagVersion.find(release.s("tag_name"))?.groupValues?.get(1) ?: return null
        val apk = assets.firstOrNull { it.s("name").endsWith(".apk") }?.s("browser_download_url")?.takeIf(::trustedApk) ?: return null
        return AppRelease(version, -1, apk, page, release.s("body").take(600))
    }

    fun compareVersions(a: String, b: String): Int {
        val x = a.split('.').map { it.toIntOrNull() ?: 0 }; val y = b.split('.').map { it.toIntOrNull() ?: 0 }
        for (i in 0 until maxOf(x.size, y.size)) { val d = x.getOrElse(i) { 0 }.compareTo(y.getOrElse(i) { 0 }); if (d != 0) return d }
        return 0
    }

    fun newer(release: AppRelease, currentName: String, currentCode: Int): Boolean =
        if (release.versionCode > 0) release.versionCode > currentCode || (release.versionCode == currentCode && compareVersions(release.versionName, currentName) > 0)
        else compareVersions(release.versionName, currentName) > 0
}

class AppUpdates(context: Context) {
    private val prefs = context.getSharedPreferences("app-updates", Context.MODE_PRIVATE)
    private val http = OkHttpClient.Builder().connectTimeout(10, TimeUnit.SECONDS).readTimeout(15, TimeUnit.SECONDS)
        .followRedirects(false).followSslRedirects(false).build()

    /** Returns a newer release, or null. Automatic checks run at most every six hours. */
    suspend fun check(force: Boolean = false): AppRelease? = withContext(Dispatchers.IO) {
        val now = System.currentTimeMillis()
        if (!force && now - prefs.getLong("checked", 0) in 0 until TimeUnit.HOURS.toMillis(6)) return@withContext cached()
        val request = Request.Builder().url(ReleaseInfo.LATEST_API)
            .header("Accept", "application/vnd.github+json").header("X-GitHub-Api-Version", "2022-11-28")
            .header("User-Agent", "YiChat-Android/${BuildConfig.VERSION_NAME}").build()
        val release = http.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw java.io.IOException("GitHub 返回 ${response.code}")
            val body = response.body?.string().orEmpty()
            require(body.length < 2_000_000) { "版本信息过大" }
            ReleaseInfo.parse(json.parseToJsonElement(body).jsonObject)
        }
        val edit = prefs.edit().putLong("checked", now)
        if (release != null) edit.putString("name", release.versionName).putInt("code", release.versionCode).putString("apk", release.apkUrl).putString("page", release.pageUrl).putString("notes", release.notes)
        else edit.remove("apk")
        edit.apply()
        release?.takeIf { ReleaseInfo.newer(it, BuildConfig.VERSION_NAME, BuildConfig.VERSION_CODE) }
    }
    private fun cached(): AppRelease? {
        val apk = prefs.getString("apk", null)?.takeIf(ReleaseInfo::trustedApk) ?: return null
        val release = AppRelease(prefs.getString("name", "").orEmpty(), prefs.getInt("code", -1), apk,
            prefs.getString("page", null)?.takeIf(ReleaseInfo::trustedPage) ?: ReleaseInfo.LATEST_PAGE, prefs.getString("notes", "").orEmpty())
        return release.takeIf { ReleaseInfo.newer(it, BuildConfig.VERSION_NAME, BuildConfig.VERSION_CODE) }
    }
    /** "Later" hides the automatic prompt for this version for one day. */
    fun snoozed(release: AppRelease) = prefs.getString("snoozed", null) == release.apkUrl &&
        System.currentTimeMillis() - prefs.getLong("snoozed-at", 0) in 0 until TimeUnit.DAYS.toMillis(1)
    fun snooze(release: AppRelease) { prefs.edit().putString("snoozed", release.apkUrl).putLong("snoozed-at", System.currentTimeMillis()).apply() }
}
