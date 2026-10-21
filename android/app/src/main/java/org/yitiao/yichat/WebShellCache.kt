package org.yitiao.yichat

import android.content.Context
import android.net.Uri
import android.util.AtomicFile
import android.webkit.WebResourceResponse
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.ByteArrayInputStream
import java.io.File
import java.net.URI
import java.util.concurrent.TimeUnit

object WebRoutes {
    // The existing explicit isolation build is loopback-only; release cannot change origin.
    val ORIGIN = if(BuildConfig.DEBUG && BuildConfig.GATEWAY=="http://127.0.0.1:18018") BuildConfig.GATEWAY else "https://chat.yitiao.org"
    private val origin = URI(ORIGIN)
    fun chat(value: String): Boolean = runCatching {
        val u=URI(value);trusted(value) && u.path=="/" && u.fragment==null
    }.getOrDefault(false)
    fun trusted(value: String): Boolean = runCatching {
        val u=URI(value);u.scheme==origin.scheme && u.host==origin.host && u.port==origin.port && u.rawUserInfo==null
    }.getOrDefault(false)
    fun publicAsset(path: String): Boolean = !path.contains("..") && !path.contains('%') &&
        path.matches(Regex("/(assets|render|brand)/[A-Za-z0-9_./-]+\\.(js|css|wasm|svg|png|jpg|webp|woff2?)"))
    fun external(value: String): Boolean = runCatching {
        val u = URI(value)
        u.scheme in setOf("https", "http") && !u.host.isNullOrBlank() && u.rawUserInfo == null
    }.getOrDefault(false)
}

/** Cache public framework resources only; authenticated API/media never enter this cache. */
class WebShellCache(private val context: Context, private val online: () -> Boolean) {
    @Volatile var bundledOnly = false
    private val directory = File(context.noBackupFilesDir,"web-shell").apply { mkdirs() }
    private val http = OkHttpClient.Builder().connectTimeout(3,TimeUnit.SECONDS).readTimeout(5,TimeUnit.SECONDS)
        .followRedirects(false).followSslRedirects(false).build()
    private val csp = "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; worker-src 'self' blob:; connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    private fun file(path: String) = File(directory, SecureStore.hash(path))
    private fun bundled(path: String) = runCatching { context.assets.open("app-shell/" + if(path=="/") "index.html" else path.removePrefix("/")).use { it.readBytes() } }.getOrNull()
    fun intercept(uri: Uri): WebResourceResponse? {
        if (!WebRoutes.trusted(uri.toString())) return null
        val path = uri.path ?: return null
        if (path != "/" && !WebRoutes.publicAsset(path)) return null
        var bytes: ByteArray? = if (bundledOnly) bundled(path) else null
        val cached = file(path)
        // Hashed assets are immutable. Index is refreshed online; APK resources
        // provide a complete first-launch framework even without connectivity.
        if(bytes==null && path.startsWith("/assets/") && cached.isFile) bytes=runCatching { AtomicFile(cached).readFully() }.getOrNull()
        if(bytes==null && path.startsWith("/assets/")) bytes=bundled(path)
        if(bytes==null && online()) bytes=runCatching {
            http.newCall(Request.Builder().url(WebRoutes.ORIGIN+path).build()).execute().use { r ->
                if(!r.isSuccessful) return@use null
                val body=r.body ?: return@use null
                if(body.contentLength()>32*1024*1024) return@use null
                val data=body.bytes()
                check(data.size<=32*1024*1024)
                if(path=="/" && !data.toString(Charsets.UTF_8).contains("name=\"yichat-app-bridge\"")) return@use null
                val atomic=AtomicFile(cached);val stream=atomic.startWrite()
                try { stream.write(data);atomic.finishWrite(stream) } catch(e:Exception) { atomic.failWrite(stream);throw e }
                data
            }
        }.getOrNull()
        if(bytes==null && cached.isFile) bytes=runCatching { AtomicFile(cached).readFully() }.getOrNull()
        if(bytes==null) bytes=bundled(path)
        if(bytes==null) return null
        val mime=when(path.substringAfterLast('.')) { "js"->"application/javascript";"css"->"text/css";"wasm"->"application/wasm";"svg"->"image/svg+xml";"png"->"image/png";"jpg"->"image/jpeg";"webp"->"image/webp";"woff2"->"font/woff2";else->"text/html" }
        return WebResourceResponse(mime,if(mime.startsWith("text/")||mime=="application/javascript") "UTF-8" else null,200,"OK",
            mapOf("Content-Security-Policy" to csp,"Referrer-Policy" to "no-referrer","X-Content-Type-Options" to "nosniff","Cache-Control" to "no-store"), ByteArrayInputStream(bytes))
    }
}
