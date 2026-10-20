package org.yitiao.yichat

import android.net.Uri
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.*
import java.security.MessageDigest
import java.security.SecureRandom
import android.util.Base64

@Serializable data class Account(val uid: String, val user: String, val mxid: String, val device: String, val token: String, val proof: String, val newDevice: Boolean = false) {
    val storage get() = SecureStore.hash("https://chat.yitiao.org|$uid|$mxid|$device")
}
@Serializable data class LoginAttempt(val state: String, val nonce: String, val verifier: String, val expires: Long, val resume: Boolean)
class AuthRepository(private val vault: SecureStore, private val gateway: Gateway) {
    private val mutex = Mutex()
    var account: Account? = null; private set
    fun restore(): Account? {
        account = vault.get("session")?.let { json.decodeFromString<Account>(it.decodeToString()) }
        gateway.token = account?.token
        return account
    }
    fun begin(resume: Boolean): Uri {
        fun random() = Base64.encodeToString(ByteArray(48).also { SecureRandom().nextBytes(it) }, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
        val attempt = LoginAttempt(random(), random(), random(), System.currentTimeMillis() + 600000, resume)
        vault.put("login", json.encodeToString(attempt).toByteArray())
        val challenge = Base64.encodeToString(MessageDigest.getInstance("SHA-256").digest(attempt.verifier.toByteArray()), Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)
        return Uri.parse(gateway.base + "/auth/native/login").buildUpon().appendQueryParameter("state", attempt.state)
            .appendQueryParameter("nonce", attempt.nonce).appendQueryParameter("code_challenge", challenge)
            .appendQueryParameter("code_challenge_method", "S256").appendQueryParameter("redirect_uri", gateway.base + "/app/callback").build()
    }
    suspend fun callback(uri: Uri): Account? = mutex.withLock {
        require(uri.scheme == "https" && uri.host == "chat.yitiao.org" && uri.port == -1 && uri.userInfo == null && uri.fragment == null) { "拒绝非白名单回跳" }
        if (uri.path == "/app/return") return@withLock null // Caller refreshes authority; never funds.
        require(uri.path == "/app/callback" && uri.queryParameterNames == setOf("code", "state")) { "回跳路径或参数无效" }
        val attempt = requireNotNull(vault.get("login")) { "无待处理登录" }.let { json.decodeFromString<LoginAttempt>(it.decodeToString()) }
        require(attempt.expires > System.currentTimeMillis()) { "登录已超时，请重新开始" }
        require(uri.getQueryParameters("state").size == 1 && uri.getQueryParameters("code").size == 1)
        require(MessageDigest.isEqual(attempt.state.toByteArray(), uri.getQueryParameter("state").orEmpty().toByteArray())) { "登录 state 校验失败" }
        val data = buildJsonObject {
            put("code", uri.getQueryParameter("code")); put("state", attempt.state); put("nonce", attempt.nonce)
            put("code_verifier", attempt.verifier); put("redirect_uri", gateway.base + "/app/callback")
            if (attempt.resume) vault.get("device-resume")?.let { put("device_proof", it.decodeToString()) }
        }
        // One attempt, no retry on transport loss. Restart authorization if the code was consumed.
        vault.delete("login")
        val r = gateway.call("/auth/native/exchange", data, authenticated = false).jsonObject
        require(r.s("homeserver") == gateway.base && r.s("uid").matches(Regex("[a-f0-9]{32}")))
        val a = Account(r.s("uid"), r.s("user"), r.s("mxid"), r.s("device_id"), r.s("token"), r.s("device_proof"),r.b("new_device"))
        require(a.mxid.endsWith(":chat.yitiao.org") && a.device.isNotBlank() && a.token.isNotBlank())
        vault.put("session", json.encodeToString(a).toByteArray()); vault.put("device-resume", a.proof.toByteArray())
        account = a; gateway.token = a.token; a
    }
    fun lock() { vault.delete("session"); vault.delete("login"); account = null; gateway.token = null }
    fun acknowledgeDevice(): Account? {
        val a=account?.copy(newDevice=false) ?: return null
        vault.put("session",json.encodeToString(a).toByteArray());account=a;return a
    }
    fun cancel() { vault.delete("login") }
}
