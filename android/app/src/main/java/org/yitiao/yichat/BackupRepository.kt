package org.yitiao.yichat

import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract
import android.util.Base64
import androidx.room.withTransaction
import androidx.work.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*
import org.bouncycastle.crypto.generators.SCrypt
import java.security.SecureRandom
import java.util.concurrent.TimeUnit
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/** Versioned, portable scrypt + AES-GCM backup; never exports Keystore wraps. */
class BackupRepository(private val context: Context, private val matrix: MatrixRepository, private val history: HistoryRepository, private val vault: SecureStore) {
    val status = MutableStateFlow("未配置外部加密备份")
    fun configure(tree: Uri, password: String) {
        require(password.length >= 12) { "备份口令至少12个字符，请另行妥善保存" }
        val a = requireNotNull(matrix.account)
        vault.put("${a.storage}.backup-tree", tree.toString().toByteArray())
        vault.put("${a.storage}.backup-pass", password.toByteArray())
        WorkManager.getInstance(context).enqueueUniquePeriodicWork("yichat-backup", ExistingPeriodicWorkPolicy.UPDATE,
            PeriodicWorkRequestBuilder<BackupWorker>(6, TimeUnit.HOURS).setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).setRequiresBatteryNotLow(true).build()).build())
        status.value = "自动备份已启用，每6小时由系统调度"
    }
    suspend fun export(): Uri = withContext(Dispatchers.IO) {
        val a = requireNotNull(matrix.account); val db = requireNotNull(matrix.database); val dao = db.local()
        val tree = Uri.parse(requireNotNull(vault.get("${a.storage}.backup-tree")) { "请先选择备份目录" }.decodeToString())
        val password = requireNotNull(vault.get("${a.storage}.backup-pass"))
        val content = db.withTransaction {
            buildJsonObject {
                put("format", "yichat-app-packets"); put("version", 1); put("uid", a.uid); put("mxid", a.mxid)
                put("packets", buildJsonArray { for (p in dao.packets()) add(buildJsonObject {
                    put("room", p.room); put("event", p.event); put("sha256", p.hash)
                    put("raw", Base64.encodeToString(vault.decrypt(a.storage, "packet:${p.room}:${p.event}", p.encrypted), Base64.NO_WRAP))
                }) })
                put("tombstones", buildJsonArray { for (t in dao.tombstones()) add(obj("room" to t.room, "event" to t.event)) })
            }.toString().toByteArray()
        }
        val packed = PortableBackup.encrypt(content, password); password.fill(0)
        val parent = DocumentsContract.buildDocumentUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree))
        val uri = requireNotNull(DocumentsContract.createDocument(context.contentResolver, parent, "application/octet-stream", "YiChat-${java.time.Instant.now().toEpochMilli()}.ycb"))
        try { requireNotNull(context.contentResolver.openOutputStream(uri, "wt")).use { it.write(packed); it.flush() } }
        catch (e: Exception) { runCatching { DocumentsContract.deleteDocument(context.contentResolver, uri) }; throw e }
        status.value = "外部加密备份成功 · ${java.time.Instant.now()}"; uri
    }
    suspend fun restore(uri: Uri, password: String): Int = withContext(Dispatchers.IO) {
        val a = requireNotNull(matrix.account); val db = requireNotNull(matrix.database); val dao = db.local()
        val packed = requireNotNull(context.contentResolver.openInputStream(uri)).use { input ->
            val out = java.io.ByteArrayOutputStream(); val buffer = ByteArray(32768); var bytes = 0
            while (true) { val n = input.read(buffer); if (n < 0) break; bytes += n; require(bytes <= 100000000) { "此版本恢复文件上限为100MB" }; out.write(buffer, 0, n) }
            out.toByteArray()
        }
        require(packed.size <= 100000000) { "此版本恢复文件上限为100MB" }
        val plain = PortableBackup.decrypt(packed, password.toByteArray())
        val data = json.parseToJsonElement(plain.decodeToString()).jsonObject
        require(data.s("format") == "yichat-app-packets" && data["version"]?.jsonPrimitive?.int == 1)
        require(data.s("uid") == a.uid && data.s("mxid") == a.mxid) { "备份属于其他账户" }
        // Fetch current authoritative metadata first. A portable backup cannot
        // certify its own hashes or restore withdrawn bodies.
        history.catchUp(true)
        val verified = data.a("packets").mapNotNull { value ->
            val p = value.jsonObject; val r = dao.receipt(p.s("room"), p.s("event")) ?: return@mapNotNull null
            if (dao.tombstone(r.room, r.event) != null) return@mapNotNull null
            val raw = Base64.decode(p.s("raw"), Base64.NO_WRAP)
            HistoryRepository.verifyPacket(raw, r.hash, r.room, r.event)
            Packet(r.room, r.event, r.sequence, r.hash, vault.encrypt(a.storage, "packet:${r.room}:${r.event}", raw))
        }
        db.withTransaction { verified.forEach { if (dao.packet(it.room, it.event) == null && dao.tombstone(it.room, it.event) == null) dao.addPacket(it) } }
        plain.fill(0)
        status.value = "已恢复 ${verified.size} 份认证原消息包；Matrix 历史密钥需独立恢复"
        verified.size
    }
}

object PortableBackup {
    private val aad = "YiChat-App-Backup-v1;scrypt:N32768:r8:p1;AES256-GCM".toByteArray()
    fun encrypt(plain: ByteArray, password: ByteArray): ByteArray {
        val random = SecureRandom(); val salt = ByteArray(32).also(random::nextBytes); val iv = ByteArray(12).also(random::nextBytes)
        val key = SCrypt.generate(password, salt, 32768, 8, 1, 32)
        try {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding"); cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, iv)); cipher.updateAAD(aad)
            return "YCB1".toByteArray() + salt + iv + cipher.doFinal(plain)
        } finally { key.fill(0) }
    }
    fun decrypt(packed: ByteArray, password: ByteArray): ByteArray {
        require(packed.size >= 64 && packed.copyOfRange(0, 4).contentEquals("YCB1".toByteArray())) { "不支持的备份格式" }
        val key = SCrypt.generate(password, packed.copyOfRange(4, 36), 32768, 8, 1, 32)
        try {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding"); cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, packed.copyOfRange(36, 48))); cipher.updateAAD(aad)
            return cipher.doFinal(packed.copyOfRange(48, packed.size))
        } finally { key.fill(0); password.fill(0) }
    }
}
class BackupWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val graph = (applicationContext as YiChatApplication).graph
        val opened = graph.matrix.account == null
        return try {
            if (opened) {
                val saved = graph.auth.restore() ?: return Result.success() // Logout removes this credential.
                graph.gateway.call("/api/session"); graph.matrix.open(saved)
            }
            graph.history.catchUp(); graph.backup.export(); Result.success()
        }
        catch (e: Exception) { graph.backup.status.value = "备份未完成，请打开 App 查看目录授权、网络和登录状态"; Result.retry() }
        finally { if (opened) graph.matrix.stopSync() }
    }
}
