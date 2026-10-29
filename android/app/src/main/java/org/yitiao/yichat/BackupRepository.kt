package org.yitiao.yichat

import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract
import android.util.Base64
import android.util.AtomicFile
import androidx.room.withTransaction
import androidx.work.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.withContext
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.*
import org.bouncycastle.crypto.generators.SCrypt
import java.security.SecureRandom
import java.util.concurrent.TimeUnit
import java.io.File
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/** Versioned, portable scrypt + AES-GCM backup; never exports Keystore wraps. */
class BackupRepository(private val context: Context, private val matrix: PacketStore, private val history: HistoryRepository, private val vault: SecureStore) {
    private val mutex = Mutex()
    val status = MutableStateFlow("自动备份保存在应用内部")
    val exportStatus = MutableStateFlow("未启用外部导出")
    val exportDays = MutableStateFlow<Int?>(null)
    fun activate() {
        val a = requireNotNull(matrix.account)
        val work = WorkManager.getInstance(context)
        val data = workDataOf("account" to a.storage)
        // Replace the old external six-hour backup with an internal-only task.
        work.enqueueUniquePeriodicWork("yichat-web-backup", ExistingPeriodicWorkPolicy.UPDATE,
            PeriodicWorkRequestBuilder<BackupWorker>(6, TimeUnit.HOURS).setInputData(data)
                .setInitialDelay(6, TimeUnit.HOURS)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).setRequiresBatteryNotLow(true).build()).build())
        val days = vault.get("${a.storage}.export-days")?.decodeToString()?.toIntOrNull()?.takeIf { it in 1..3650 }
        exportDays.value = days
        if (days != null) scheduleExport(a, days) else work.cancelUniqueWork("yichat-web-export")
        val last = vault.get("${a.storage}.local-backup-last")?.decodeToString()?.toLongOrNull()
        status.value = last?.let { "内部加密备份 · ${localTime(it)}" } ?: "自动备份已开启，保存在应用内部"
        exportStatus.value = if (days != null) "每 $days 天导出一次，由系统择机执行" else "未启用外部导出"
    }
    fun configure(tree: Uri, password: String, days: Int) {
        require(password.length >= 12) { "备份口令至少12个字符，请另行妥善保存" }
        require(days in 1..3650) { "导出间隔须为1至3650个整天" }
        val a = requireNotNull(matrix.account)
        vault.put("${a.storage}.backup-tree", tree.toString().toByteArray())
        vault.put("${a.storage}.backup-pass", password.toByteArray())
        vault.put("${a.storage}.export-days", days.toString().toByteArray())
        exportDays.value = days
        scheduleExport(a, days)
        exportStatus.value = "已设置每 $days 天导出一次，由系统择机执行"
    }
    private fun scheduleExport(a: Account, days: Int) {
        WorkManager.getInstance(context).enqueueUniquePeriodicWork("yichat-web-export", ExistingPeriodicWorkPolicy.UPDATE,
            PeriodicWorkRequestBuilder<ExportWorker>(days.toLong(), TimeUnit.DAYS)
                .setInitialDelay(days.toLong(), TimeUnit.DAYS).setInputData(workDataOf("account" to a.storage))
                .setConstraints(Constraints.Builder().setRequiresBatteryNotLow(true).build()).build())
    }
    fun stopExport() {
        val a = requireNotNull(matrix.account)
        WorkManager.getInstance(context).cancelUniqueWork("yichat-web-export")
        listOf("export-days", "backup-tree", "backup-pass").forEach { vault.delete("${a.storage}.$it") }
        exportDays.value = null
        exportStatus.value = "已停用外部导出；已有导出文件保留"
    }
    suspend fun saveInternal(force: Boolean = false) = mutex.withLock { withContext(Dispatchers.IO) {
        val a = requireNotNull(matrix.account)
        val last = vault.get("${a.storage}.local-backup-last")?.decodeToString()?.toLongOrNull() ?: 0
        if (!force && System.currentTimeMillis() - last in 0 until TimeUnit.HOURS.toMillis(6)) return@withContext
        val plain = packetContent(a)
        try {
            val packed = vault.encrypt(a.storage, "backup:packets", plain)
            val dir = File(context.noBackupFilesDir, "backups/${a.storage}").apply { check(mkdirs() || isDirectory) }
            val file = AtomicFile(File(dir, "packets.enc"))
            val stream = file.startWrite()
            try { stream.write(packed); file.finishWrite(stream) }
            catch (e: Exception) { file.failWrite(stream); throw e }
            val now = System.currentTimeMillis()
            vault.put("${a.storage}.local-backup-last", now.toString().toByteArray())
            status.value = "内部加密备份成功 · ${localTime(now)}"
        } finally { plain.fill(0) }
    } }
    private suspend fun packetContent(a: Account): ByteArray {
        check(matrix.account?.storage == a.storage) { "备份账号已改变" }
        val db = requireNotNull(matrix.database); val dao = db.local()
        val content = db.withTransaction {
            buildJsonObject {
                put("format", "yichat-app-packets"); put("version", 1); put("uid", a.uid); put("mxid", a.mxid)
                put("packets", buildJsonArray { for (p in dao.packets()) add(buildJsonObject {
                    put("room", p.room); put("event", p.event); put("sha256", p.hash)
                    val raw = vault.decrypt(a.storage, "packet:${p.room}:${p.event}", p.encrypted)
                    try { put("raw", Base64.encodeToString(raw, Base64.NO_WRAP)) } finally { raw.fill(0) }
                }) })
                put("tombstones", buildJsonArray { for (t in dao.tombstones()) add(obj("room" to t.room, "event" to t.event)) })
            }.toString().toByteArray()
        }
        if (matrix.database !== db || matrix.account?.storage != a.storage) {
            content.fill(0)
            error("备份期间账号已锁定或改变")
        }
        return content
    }
    suspend fun export(): Uri = mutex.withLock { withContext(Dispatchers.IO) {
        val a = requireNotNull(matrix.account)
        val tree = Uri.parse(requireNotNull(vault.get("${a.storage}.backup-tree")) { "请先选择备份目录" }.decodeToString())
        val password = requireNotNull(vault.get("${a.storage}.backup-pass"))
        val content = packetContent(a)
        val packed = try { PortableBackup.encrypt(content, password) } finally { password.fill(0); content.fill(0) }
        val parent = DocumentsContract.buildDocumentUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree))
        val uri = requireNotNull(DocumentsContract.createDocument(context.contentResolver, parent, "application/octet-stream", "YiChat-${java.time.Instant.now().toEpochMilli()}.ycb"))
        try { requireNotNull(context.contentResolver.openOutputStream(uri, "wt")).use { it.write(packed); it.flush() } }
        catch (e: Exception) { runCatching { DocumentsContract.deleteDocument(context.contentResolver, uri) }; throw e }
        exportStatus.value = "外部加密导出成功 · ${localTime(System.currentTimeMillis())}"; uri
    } }
    private fun localTime(ms: Long) = java.time.Instant.ofEpochMilli(ms).atZone(java.time.ZoneId.systemDefault())
        .format(java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm"))
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
        // certify its own hashes. Withdrawn bytes remain audit-only; tombstones still hide/deny relay.
        history.catchUp(true)
        val verified = data.a("packets").mapNotNull { value ->
            val p = value.jsonObject; val r = dao.receipt(p.s("room"), p.s("event")) ?: return@mapNotNull null
            val raw = Base64.decode(p.s("raw"), Base64.NO_WRAP)
            HistoryRepository.verifyPacket(raw, r.hash, r.room, r.event)
            Packet(r.room, r.event, r.sequence, r.hash, vault.encrypt(a.storage, "packet:${r.room}:${r.event}", raw))
        }
        db.withTransaction { verified.forEach { if (dao.packet(it.room, it.event) == null) dao.addPacket(it) } }
        plain.fill(0)
        status.value = "已恢复 ${verified.size} 份认证原消息包；无需会话密钥"
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
        return try {
            if (!prepareBackup(graph, inputData.getString("account"))) return Result.success()
            graph.history.catchUp(); graph.backup.saveInternal(force = true); Result.success()
        }
        catch (e: CancellationException) { throw e }
        catch (e: GatewayError) { if(e.status==401) { graph.lock();return Result.success() };graph.backup.status.value="内部备份未完成，将重试";Result.retry() }
        catch (e: Exception) { graph.backup.status.value = "内部备份未完成，将重试；已保存数据保留"; Result.retry() }
    }
}
class ExportWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val graph = (applicationContext as YiChatApplication).graph
        return try {
            if (!prepareBackup(graph, inputData.getString("account"))) return Result.success()
            val account = requireNotNull(graph.store.account)
            if (graph.vault.get("${account.storage}.export-days") == null) return Result.success()
            graph.backup.export(); Result.success()
        }
        catch (e: CancellationException) { throw e }
        catch (e: GatewayError) { if(e.status==401) { graph.lock();return Result.success() };graph.backup.exportStatus.value="外部导出未完成，将重试";Result.retry() }
        catch (e: Exception) { graph.backup.exportStatus.value = "外部导出未完成，请检查目录授权和存储空间；内部数据保留"; Result.retry() }
    }
}
private suspend fun prepareBackup(graph: Graph, expected: String?): Boolean {
    val active = graph.store.account
    if (active != null) return expected == active.storage
    val saved = graph.auth.restore() ?: return false // Logout removes this credential.
    if (expected != saved.storage) return false // An old account's worker cannot open another account.
    graph.gateway.call("/api/session")
    fun stillLoggedIn() = graph.vault.get("web.session")?.decodeToString()?.let {
        json.decodeFromString<Account>(it).token == saved.token
    } == true
    if (!stillLoggedIn()) return false
    // Do not start/stop foreground sync from a worker. open() reuses an already
    // opened account if the user returns to the App during this check.
    graph.store.open(saved)
    if (!stillLoggedIn()) {
        graph.store.close(expectedToken = saved.token)
        return false
    }
    return graph.store.account?.storage == expected
}
