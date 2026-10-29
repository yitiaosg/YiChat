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

const val MESSAGE_EPOCH="plain-20261029-v1"

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

class YiChatApplication : Application() {
    val push by lazy { PushNotifications(this) }
    val graph by lazy { Graph(this) }
    override fun onCreate(){
        super.onCreate()
        // USER_UNLOCKED is a runtime-only broadcast. No CE access before it.
        if(!push.unlocked){
            val receiver=NotificationUnlockReceiver()
            androidx.core.content.ContextCompat.registerReceiver(this,receiver,
                android.content.IntentFilter(android.content.Intent.ACTION_USER_UNLOCKED),androidx.core.content.ContextCompat.RECEIVER_NOT_EXPORTED)
        }
    }
}
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
    val notificationContent = NotificationContent(context,this)
    private val startup = Mutex()
    private var restored = false
    suspend fun restore() = startup.withLock {
        if (!restored) {
            WorkManager.getInstance(context).cancelUniqueWork("yichat-backup")
            WorkManager.getInstance(context).cancelUniqueWork("yichat-export")
            auth.restore()?.let { store.open(it); resetChatEpoch(it); backup.activate(); notifications.resume() }
            restored = true
        }
        auth.account
    }
    suspend fun activate(a: Account) {
        store.open(a); resetChatEpoch(a); backup.activate(); notifications.resume()
        scope.launch { runCatching { history.catchUp(true); backup.saveInternal() } }
    }
    // Explicit server-wide chat reset. Preserve credentials, wallet/business
    // operations, backup settings and at-rest protection. Do not import old chats.
    internal suspend fun resetChatEpoch(a: Account) {
        val db=requireNotNull(store.database)
        val dao=db.local()
        if(dao.watermark("@message-epoch")?.cursor==MESSAGE_EPOCH)return
        db.withTransaction {
            dao.clearPackets();dao.clearReceipts();dao.clearTombstones();dao.clearWatermarks();dao.clearMissingOperations()
            dao.watermark(Watermark("@message-epoch",0,MESSAGE_EPOCH))
        }
        context.getSystemService(android.app.NotificationManager::class.java).cancelAll()
        File(context.noBackupFilesDir,"backups/${a.storage}").deleteRecursively()
        vault.delete("${a.storage}.local-backup-last")
        // Original native-only client stored its previous packet DB separately.
        vault.get("session")?.let { bytes ->
            runCatching { json.decodeFromString<Account>(bytes.decodeToString()) }.getOrNull()?.let { old ->
                if(old.uid==a.uid&&old.mxid==a.mxid){
                    File(context.noBackupFilesDir,"accounts/${old.storage}").deleteRecursively()
                    File(context.noBackupFilesDir,"backups/${old.storage}").deleteRecursively()
                }
            }
        }
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
