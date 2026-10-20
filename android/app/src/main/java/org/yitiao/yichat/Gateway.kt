package org.yitiao.yichat

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*
import okhttp3.*
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.io.File
import okio.BufferedSink
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

val json = Json { ignoreUnknownKeys = true }
fun JsonObject.s(name: String) = this[name]?.jsonPrimitive?.contentOrNull.orEmpty()
fun JsonObject.b(name: String) = this[name]?.jsonPrimitive?.booleanOrNull == true
fun JsonObject.o(name: String) = this[name] as? JsonObject ?: JsonObject(emptyMap())
fun JsonObject.a(name: String) = this[name] as? JsonArray ?: JsonArray(emptyList())
fun obj(vararg pairs: Pair<String, String>) = buildJsonObject { pairs.forEach { put(it.first, it.second) } }
class GatewayError(val status: Int, val reason: String, val code: String = "") : IOException(reason)

class Gateway(val base: String = BuildConfig.GATEWAY) {
    @Volatile var token: String? = null
    private val http = OkHttpClient.Builder().connectTimeout(15, TimeUnit.SECONDS).readTimeout(45, TimeUnit.SECONDS)
        .followRedirects(false).followSslRedirects(false).retryOnConnectionFailure(false).build()
    private val readHttp = http.newBuilder().retryOnConnectionFailure(true).build()
    private val mutex = Mutex()
    private val next = AtomicLong(0)
    private val cooldown = AtomicLong(0)
    private val uploadMutex = Mutex()
    @Volatile private var uploadCall: Call? = null
    fun cancelUpload() { uploadCall?.cancel() }
    suspend fun upload(room: String, file: File, progress: (Long, Long) -> Unit): JsonObject = uploadMutex.withLock {
        require(file.length() <= 100000000) { "实际上传字节不得超过100000000" }
        withContext(Dispatchers.IO) {
            val body = object : RequestBody() {
                override fun isOneShot() = true
                override fun contentType() = "application/octet-stream".toMediaType()
                override fun contentLength() = file.length()
                override fun writeTo(sink: BufferedSink) {
                    val started = System.nanoTime(); var sent = 0L
                    file.inputStream().use { input ->
                        val buffer = ByteArray(16384)
                        while (true) {
                            val n = input.read(buffer); if (n < 0) break
                            sent += n; require(sent <= 100000000)
                            val wait = sent * 1000000000L / 500000L - (System.nanoTime() - started)
                            if (wait > 0) TimeUnit.NANOSECONDS.sleep(wait)
                            sink.write(buffer, 0, n); progress(sent, file.length())
                        }
                    }
                }
            }
            val call = http.newCall(Request.Builder().url(base + "/api/files/" + segment(room)).header("Authorization", "Bearer " + requireNotNull(token)).post(body).build())
            uploadCall = call
            try { call.execute().use { response ->
                val parsed = json.parseToJsonElement(response.body!!.string()).jsonObject
                if (!response.isSuccessful) throw GatewayError(response.code, parsed.s("error"))
                parsed
            } } finally { uploadCall = null }
        }
    }
    suspend fun call(path: String, data: JsonElement? = null, method: String = if (data == null) "GET" else "POST", authenticated: Boolean = true): JsonElement {
        require(path.startsWith("/") && !path.startsWith("//") && '\\' !in path)
        // Business-only pacing. Rust requests share the server's native queue;
        // this mutex must never be described as intercepting Rust HTTP.
        mutex.withLock {
            val now = android.os.SystemClock.elapsedRealtime()
            delay((maxOf(next.get(), cooldown.get()) - now).coerceAtLeast(0))
            next.set(android.os.SystemClock.elapsedRealtime() + 180)
        }
        return withContext(Dispatchers.IO) {
            val request = Request.Builder().url(base + path).header("Accept", "application/json")
            if (authenticated) request.header("Authorization", "Bearer " + requireNotNull(token) { "请先登录" })
            val bodyBytes=(data?.toString() ?: "{}").toByteArray(Charsets.UTF_8)
            request.method(method, if (method in listOf("GET", "HEAD")) null else object : RequestBody() {
                override fun contentType() = "application/json".toMediaType()
                override fun contentLength() = bodyBytes.size.toLong()
                override fun isOneShot() = true
                override fun writeTo(sink: BufferedSink) { sink.write(bodyBytes) }
            })
            (if(method in listOf("GET","HEAD")) readHttp else http).newCall(request.build()).execute().use { r ->
                val bytes = r.body?.string().orEmpty()
                val parsed = runCatching { json.parseToJsonElement(bytes) }.getOrElse { JsonNull }
                if (!r.isSuccessful) {
                    val detail = parsed as? JsonObject ?: obj("error" to "服务器返回 HTTP ${r.code}")
                    if (r.code == 429) {
                        val ms = maxOf(r.header("Retry-After")?.toLongOrNull()?.times(1000) ?: 0, detail["retry_after_ms"]?.jsonPrimitive?.longOrNull ?: 1000)
                        cooldown.updateAndGet { maxOf(it, android.os.SystemClock.elapsedRealtime() + ms) }
                    }
                    throw GatewayError(r.code, detail.s("error").ifBlank { "HTTP ${r.code}" }, detail.s("code"))
                }
                parsed
            }
        }
    }
    companion object { fun segment(s: String) = "https://example.invalid/".toHttpUrl().newBuilder().addPathSegment(s).build().encodedPath.removePrefix("/") }
}
