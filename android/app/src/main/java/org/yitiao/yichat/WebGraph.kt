package org.yitiao.yichat

import android.app.Application
import android.content.Context
import androidx.room.Room
import androidx.room.withTransaction
import androidx.work.*
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.io.File
import kotlinx.serialization.json.*

interface PacketStore {
    val account: Account?
    val database: YiChatDatabase?
    suspend fun refreshTombstones()
}

/** Packet storage is independent of both the old native crypto DB and WebView IndexedDB. */
class WebPacketStore(private val context: Context) : PacketStore {
    @Volatile override var account: Account? = null; private set
    @Volatile override var database: YiChatDatabase? = null; private set
    private val mutex = Mutex()
    suspend fun open(a: Account) = mutex.withLock {
        if (account?.storage == a.storage && account?.token == a.token && database != null) return@withLock
        database?.close()
        val root = File(context.noBackupFilesDir, "web-accounts/${a.storage}").apply { mkdirs() }
        database = Room.databaseBuilder(context, YiChatDatabase::class.java, File(root,"packets.db").absolutePath).build()
        account = a
    }
    suspend fun close(expectedToken: String? = null) = mutex.withLock {
        if (expectedToken == null || account?.token == expectedToken) {
            database?.close(); database = null; account = null
        }
    }
    override suspend fun refreshTombstones() = Unit // The shared Web client applies server recalls itself.
}

class YiChatApplication : Application() { val graph by lazy { Graph(this) } }
class Graph(val context: Application) {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    val vault = SecureStore(context)
    val gateway = Gateway()
    // Never restore a Rust-native device into matrix-js-sdk's different crypto store.
    val auth = AuthRepository(vault, gateway, "web.")
    val store = WebPacketStore(context)
    val history = HistoryRepository(store, gateway, vault)
    val backup = BackupRepository(context, store, history, vault)
    val records = RecordAudit(store,vault)
    val notifications = Notifications(context,this)
    private val startup = Mutex()
    private var restored = false
    suspend fun restore() = startup.withLock {
        if (!restored) {
            WorkManager.getInstance(context).cancelUniqueWork("yichat-backup")
            WorkManager.getInstance(context).cancelUniqueWork("yichat-export")
            auth.restore()?.let { store.open(it); migrateLegacy(it); backup.activate(); notifications.resume() }
            restored = true
        }
        auth.account
    }
    suspend fun activate(a: Account) {
        store.open(a); migrateLegacy(a); backup.activate(); notifications.resume()
        scope.launch { runCatching { history.catchUp(true); backup.saveInternal() } }
    }
    private suspend fun migrateLegacy(a: Account) {
        try { preserveLegacyPackets(a) }
        catch(e: CancellationException) { throw e }
        catch(_: Exception) { backup.status.value="旧版消息包迁移未完成，原数据仍保留" }
    }
    private suspend fun preserveLegacyPackets(a: Account) {
        val old=vault.get("session")?.let { runCatching { json.decodeFromString<Account>(it.decodeToString()) }.getOrNull() } ?: return
        if(old.uid!=a.uid || old.mxid!=a.mxid || old.device==a.device) return
        val marker="${a.storage}.legacy-${old.storage}"
        if(vault.get(marker)!=null)return
        val path=File(context.noBackupFilesDir,"accounts/${old.storage}/app.db")
        if(!path.isFile)return
        val source=Room.databaseBuilder(context,YiChatDatabase::class.java,path.absolutePath).build()
        try {
            val from=source.local();val to=requireNotNull(store.database).local()
            val tombstones=from.tombstones();val receipts=from.receipts();val packets=from.packets()
            requireNotNull(store.database).withTransaction {
                tombstones.forEach { to.tombstone(it) }
                for(r in receipts) {
                    val raw=vault.decrypt(old.storage,"receipt:${r.room}:${r.event}",r.encrypted)
                    try { if(to.receipt(r.room,r.event)==null)to.receipt(r.copy(encrypted=vault.encrypt(a.storage,"receipt:${r.room}:${r.event}",raw))) } finally { raw.fill(0) }
                }
                for(p in packets) {
                    if(to.packet(p.room,p.event)!=null)continue
                    val r=to.receipt(p.room,p.event) ?: continue
                    val raw=vault.decrypt(old.storage,"packet:${p.room}:${p.event}",p.encrypted)
                    try { HistoryRepository.verifyPacket(raw,r.hash,p.room,p.event);to.addPacket(p.copy(encrypted=vault.encrypt(a.storage,"packet:${p.room}:${p.event}",raw))) } finally { raw.fill(0) }
                }
            }
            for(name in listOf("backup-tree","backup-pass","export-days")) {
                if(vault.get("${a.storage}.$name")==null) vault.get("${old.storage}.$name")?.let { vault.put("${a.storage}.$name",it);it.fill(0) }
            }
            vault.put(marker,byteArrayOf(1))
        } finally { source.close() }
    }
    suspend fun completePendingLogout() {
        for (key in listOf("pending-logout", "web.pending-logout")) {
            val token = vault.get(key)?.decodeToString() ?: continue
            try { Gateway().apply { this.token = token }.call("/api/logout", obj()) }
            catch (e: GatewayError) { if (e.status != 401) throw e }
            vault.delete(key)
        }
    }
    suspend fun lock() {
        WorkManager.getInstance(context).cancelUniqueWork("yichat-web-backup")
        WorkManager.getInstance(context).cancelUniqueWork("yichat-web-export")
        notifications.stop();auth.lock(); store.close()
    }
    suspend fun logout() {
        auth.account?.token?.let { vault.put("web.pending-logout", it.toByteArray()) }
        lock()
        WorkManager.getInstance(context).enqueueUniqueWork("yichat-session-revoke", ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<RevokeWorker>().setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()).build())
        runCatching { completePendingLogout() }
    }
}
class RevokeWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result = try {
        (applicationContext as YiChatApplication).graph.completePendingLogout(); Result.success()
    } catch (e: CancellationException) { throw e }
    catch (_: Exception) { Result.retry() }
}
