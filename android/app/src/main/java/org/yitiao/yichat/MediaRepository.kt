package org.yitiao.yichat

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import android.util.Base64
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*
import org.matrix.rustcomponents.sdk.*
import java.io.File
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.spec.IvParameterSpec
import javax.crypto.spec.SecretKeySpec

data class Attachment(val name: String, val mime: String, val sourceJson: String)
class MediaRepository(private val context: Context, private val matrix: MatrixRepository, private val gateway: Gateway) {
    val progress = MutableStateFlow<Pair<Long, Long>?>(null)
    suspend fun send(uri: Uri, room: String, mimeOverride: String? = null) = withContext(Dispatchers.IO) {
        val a = requireNotNull(matrix.account)
        val dir = File(context.cacheDir, "temporary/${a.storage}").apply { mkdirs() }
        val plain = File(dir, UUID.randomUUID().toString()); val cipherFile = File(dir, UUID.randomUUID().toString())
        var name = "附件"
        if (uri.scheme == "file") name = File(requireNotNull(uri.path)).name
        else context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { if(it.moveToFirst()) name = it.getString(0).take(150) }
        val mime = mimeOverride ?: context.contentResolver.getType(uri) ?: "application/octet-stream"
        try {
            requireNotNull(context.contentResolver.openInputStream(uri)).use { input -> plain.outputStream().use { out ->
                val buffer = ByteArray(32768); var size = 0L
                while (true) { val n = input.read(buffer); if (n < 0) break; size += n; require(size <= 100000000) { "附件实际字节超过100000000" }; out.write(buffer, 0, n) }
            } }
            val encrypted = gateway.call("/api/rooms/${Gateway.segment(room)}").jsonObject.s("encryption") != "plain"
            val descriptor = if (encrypted) MatrixAttachment.encrypt(plain, cipherFile) else null
            val uploaded = gateway.upload(room, if (encrypted) cipherFile else plain) { done,total -> progress.value = done to total }
            val source = if (descriptor == null) MediaSource.fromUrl(uploaded.s("url")) else MediaSource.fromJson(buildJsonObject { put("file", JsonObject(descriptor + ("url" to JsonPrimitive(uploaded.s("url")))) ) }.toString())
            val msg = MessageType.File(FileMessageContent(name, null, null, source, FileInfo(mime, plain.length().toULong(), null, null)))
            matrix.sendMedia(room, msg, buildJsonObject { put("org.yitiao.attachments", JsonArray(listOf(JsonPrimitive(uploaded.s("id"))))) })
        } finally { plain.delete(); cipherFile.delete(); progress.value = null }
    }
    suspend fun download(attachment: Attachment): MediaFileHandle = withContext(Dispatchers.IO) {
        val a = requireNotNull(matrix.account); val dir = File(context.cacheDir, "temporary/${a.storage}").apply { mkdirs() }
        // SDK performs Matrix attachment authentication/decryption. No active
        // content viewer is ever used for downloaded HTML, SVG or scripts.
        requireNotNull(matrix.client).getMediaFile(MediaSource.fromJson(attachment.sourceJson), attachment.name, attachment.mime, false, dir.absolutePath)
    }
}
object MatrixAttachment {
    private fun b64(b: ByteArray, url: Boolean = false) = Base64.encodeToString(b, Base64.NO_WRAP or Base64.NO_PADDING or if(url) Base64.URL_SAFE else 0)
    fun encrypt(input: File, output: File): JsonObject {
        // Matrix encrypted attachment v2 (AES-256-CTR, SHA-256 ciphertext).
        // This is the specified Matrix file format, not a new chat cipher.
        val rng = SecureRandom(); val key = ByteArray(32).also(rng::nextBytes); val iv = ByteArray(16)
        rng.nextBytes(iv); iv.fill(0, 8, 16)
        val cipher = Cipher.getInstance("AES/CTR/NoPadding"); cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), IvParameterSpec(iv))
        val digest = MessageDigest.getInstance("SHA-256")
        input.inputStream().use { source -> output.outputStream().use { dest ->
            val buffer = ByteArray(32768)
            while (true) { val n = source.read(buffer); if (n < 0) break; val encrypted = cipher.update(buffer, 0, n); digest.update(encrypted); dest.write(encrypted) }
            val final = cipher.doFinal(); digest.update(final); dest.write(final)
        } }
        return buildJsonObject { put("v", "v2"); put("iv", b64(iv)); put("hashes", obj("sha256" to b64(digest.digest())))
            put("key", buildJsonObject { put("kty", "oct"); put("alg", "A256CTR"); put("ext", true); put("k", b64(key, true)); put("key_ops", JsonArray(listOf(JsonPrimitive("encrypt"), JsonPrimitive("decrypt")))) })
        }.also { key.fill(0) }
    }
}
