package org.yitiao.yichat

import android.content.Context
import androidx.room.Room as RoomDatabaseBuilder
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.*
import org.matrix.rustcomponents.sdk.*
import uniffi.matrix_sdk_common.BackgroundTaskFailureReason
import java.io.File
import java.util.UUID

data class Conversation(val id: String, val name: String, val invited: Boolean, val encrypted: Boolean, val unread: Long = 0)
data class ChatMessage(val key: String, val id: EventOrTransactionId, val sender: String, val body: String, val timestamp: Long,
    val own: Boolean, val editable: Boolean, val redacted: Boolean, val failed: Boolean, val pending: Boolean, val encryptedFailure: Boolean, val attachment: Attachment? = null,
    val giftId: String? = null, val receipt: Boolean = false)

class MatrixRepository(private val context: Context, private val vault: SecureStore, private val gateway: Gateway, private val scope: CoroutineScope) {
    var client: Client? = null; private set
    var database: YiChatDatabase? = null; private set
    @Volatile var account: Account? = null; private set
    private var lockFile: java.nio.channels.FileLock? = null
    private var lockChannel: java.nio.channels.FileChannel? = null
    private var syncHandle: TaskHandle? = null
    private var timelineHandle: TaskHandle? = null
    private var delegateHandle: TaskHandle? = null
    private var timeline: Timeline? = null
    private var currentRoom: String? = null
    private val sessionMutex = Mutex()
    private val sendMutex = Mutex()
    private val sessionGeneration = java.util.concurrent.atomic.AtomicLong()
    private val timelineGeneration = java.util.concurrent.atomic.AtomicLong()
    private val items = mutableListOf<TimelineItem>()
    private val withdrawn = java.util.concurrent.ConcurrentHashMap.newKeySet<String>()
    val conversations = MutableStateFlow<List<Conversation>>(emptyList())
    val messages = MutableStateFlow<List<ChatMessage>>(emptyList())
    val syncStatus = MutableStateFlow("未登录")
    val verification = MutableStateFlow("")
    var onInvalidSession: (() -> Unit)? = null

    suspend fun open(a: Account) = sessionMutex.withLock {
        client?.enableAllSendQueues(false)
        closeInternal()
        val root = File(context.noBackupFilesDir, "accounts/${a.storage}").apply { mkdirs() }
        lockChannel = java.io.RandomAccessFile(File(root, "writer.lock"), "rw").channel
        lockFile = requireNotNull(lockChannel!!.tryLock()) { "此账号设备库已由其他进程打开" }
        try {
            database = RoomDatabaseBuilder.databaseBuilder(context, YiChatDatabase::class.java, File(root, "app.db").absolutePath).build()
            val c = ClientBuilder().homeserverUrl(gateway.base).disableWellKnownLookup(true)
                .sqliteStore(SqliteStoreBuilder(File(root, "matrix").absolutePath, File(context.cacheDir, "matrix/${a.storage}").apply { mkdirs() }.absolutePath).key(vault.accountKey(a.storage, "matrix")))
                .slidingSyncVersionBuilder(SlidingSyncVersionBuilder.NONE)
                .autoEnableCrossSigning(true)
                .requestConfig(RequestConfig(2uL, 40000uL, 4uL, 60000uL))
                .userAgent("YiChat-Android/0.1.0").build()
            client = c; account = a
            val generation=sessionGeneration.get()
            delegateHandle = c.setDelegate(object : ClientDelegate {
                override fun didReceiveAuthError(isSoftLogout: Boolean) { if(client===c && sessionGeneration.get()==generation) { syncStatus.value = "会话已失效"; onInvalidSession?.invoke() } }
                override fun onBackgroundTaskErrorReport(taskName: String, error: BackgroundTaskFailureReason) { if(client===c && sessionGeneration.get()==generation) syncStatus.value = "后台同步暂时失败，请检查网络" }
            })
            c.enableAllSendQueues(false)
            c.restoreSession(Session(a.token, null, a.mxid, a.device, gateway.base, null, SlidingSyncVersion.NONE))
            restoreMetadata()
            c.enableAllSendQueues(true)
            verification.value = c.encryption().verificationState().name
            startSync()
        } catch (e: Exception) { closeInternal(); throw e }
    }
    fun startSync() {
        val c = client ?: return
        if (syncHandle != null) return
        syncStatus.value = "正在同步"
        val generation=sessionGeneration.get()
        syncHandle = c.syncV2(SyncSettingsV2(30000uL, true), object : SyncListenerV2 {
            override fun onUpdate(response: SyncResponseV2) {
                if(client!==c || sessionGeneration.get()!=generation) return
                syncStatus.value = "已同步"
                scope.launch { runCatching { refreshRooms() }.onFailure { if(client===c) syncStatus.value="会话元数据暂不可用，等待下一次同步" } }
            }
        })
    }
    fun stopSync() { syncHandle?.cancel(); syncHandle?.close(); syncHandle = null }
    suspend fun refreshRooms() {
        val c = client ?: return
        val rooms=c.rooms().filter { it.membership() in listOf(Membership.JOINED,Membership.INVITED) }.map { room ->
            val invited=room.membership()==Membership.INVITED
            Conversation(room.id(), room.displayName().orEmpty().ifBlank { room.id() }, invited, if(invited) false else room.isEncrypted(), if(invited) 0 else room.roomInfo().numUnreadMessages.toLong())
        }.sortedBy { it.name }
        if(client===c) conversations.value=rooms
    }
    suspend fun select(room: String) {
        val generation=timelineGeneration.incrementAndGet()
        refreshTombstones()
        timelineHandle?.cancel(); timelineHandle?.close(); timelineHandle = null
        timeline?.close(); timeline = null; synchronized(items) { items.clear() }; messages.value = emptyList()
        currentRoom = room
        val r = requireNotNull(client?.getRoom(room)) { "会话尚未同步" }
        if (r.membership() != Membership.JOINED) return
        val t = r.timeline(); timeline = t
        timelineHandle = t.addListener(object : TimelineListener {
            override fun onUpdate(diff: List<TimelineDiff>) {
                synchronized(items) {
                    if(timelineGeneration.get()!=generation || currentRoom!=room) return
                    for (d in diff) when (d) {
                        is TimelineDiff.Append -> items.addAll(d.values)
                        TimelineDiff.Clear -> items.clear()
                        is TimelineDiff.PushFront -> items.add(0, d.value)
                        is TimelineDiff.PushBack -> items.add(d.value)
                        TimelineDiff.PopFront -> if (items.isNotEmpty()) items.removeAt(0)
                        TimelineDiff.PopBack -> if (items.isNotEmpty()) items.removeAt(items.lastIndex)
                        is TimelineDiff.Insert -> items.add(d.index.toInt(), d.value)
                        is TimelineDiff.Set -> items[d.index.toInt()] = d.value
                        is TimelineDiff.Remove -> items.removeAt(d.index.toInt())
                        is TimelineDiff.Truncate -> while (items.size > d.length.toInt()) items.removeAt(items.lastIndex)
                        is TimelineDiff.Reset -> { items.clear(); items.addAll(d.values) }
                    }
                    messages.value = items.mapNotNull { item ->
                        val e = item.asEvent() ?: return@mapNotNull null
                        val kind = (e.content as? TimelineItemContent.MsgLike)?.content?.kind ?: return@mapNotNull null
                        val withdrawnId = (e.eventOrTransactionId as? EventOrTransactionId.EventId)?.eventId
                        val hidden = kind == MsgLikeKind.Redacted || "$room|$withdrawnId" in withdrawn
                        val body = if(hidden) "消息已撤回" else (kind as? MsgLikeKind.Message)?.content?.body ?: when (kind) {
                            MsgLikeKind.Redacted -> "消息已撤回"
                            is MsgLikeKind.UnableToDecrypt -> "无法解密：请验证设备或恢复 Matrix 密钥"
                            else -> return@mapNotNull null
                        }
                        val media = when(val type = (kind as? MsgLikeKind.Message)?.content?.msgType) {
                            is MessageType.File -> Attachment(type.content.filename, type.content.info?.mimetype ?: "application/octet-stream", type.content.source.toJson())
                            is MessageType.Image -> Attachment(type.content.filename, type.content.info?.mimetype ?: "image/jpeg", type.content.source.toJson())
                            is MessageType.Audio -> Attachment(type.content.filename, type.content.info?.mimetype ?: "audio/mp4", type.content.source.toJson())
                            is MessageType.Video -> Attachment(type.content.filename, type.content.info?.mimetype ?: "video/mp4", type.content.source.toJson())
                            else -> null
                        }
                        val extra = runCatching { e.lazyProvider.latestJson()?.let { json.parseToJsonElement(it).jsonObject.o("content") } }.getOrNull()
                        val reference = extra?.o("org.yitiao.business")
                        val gift = reference?.s("id")?.takeIf { reference.s("kind") == "gift" }
                        ChatMessage(item.uniqueId().id, e.eventOrTransactionId, e.sender, body, e.timestamp.toLong(), e.isOwn,
                            e.isEditable && !hidden, hidden, e.localSendState is EventSendState.SendingFailed,
                            e.localSendState is EventSendState.NotSentYet, kind is MsgLikeKind.UnableToDecrypt, media, gift, extra?.o("org.yitiao.share")?.s("type") == "receipt")
                    }
                }
            }
        })
        // Classic sync does not hydrate a new timeline from an old sync token.
        // Fetch an initial page rather than showing an empty reopened room.
        t.paginateBackwards(30u)
    }
    suspend fun refreshTombstones() {
        val rows=database?.local()?.tombstones() ?: return
        withdrawn.addAll(rows.map { "${it.room}|${it.event}" })
        val room=currentRoom ?: return
        messages.value=messages.value.map { m ->
            val id=(m.id as? EventOrTransactionId.EventId)?.eventId
            if("$room|$id" in withdrawn) m.copy(body="消息已撤回",redacted=true,editable=false,attachment=null,giftId=null) else m
        }
    }
    suspend fun joined(room: String) { requireNotNull(client?.getRoom(room)).join() }
    suspend fun leave(room: String) { requireNotNull(client?.getRoom(room)).leave(); refreshRooms() }
    suspend fun history() = requireNotNull(timeline).paginateBackwards(30u)
    suspend fun read() { timeline?.markAsRead(ReceiptType.READ) }

    private suspend fun prepare(room: String) {
        val r = requireNotNull(client?.getRoom(room))
        val meta = gateway.call("/api/rooms/${Gateway.segment(room)}").jsonObject
        val encrypted = meta.s("encryption") != "plain"
        check(r.isEncrypted() == encrypted) { "会话加密状态未一致，暂停发送" }
        if (encrypted) {
            // Fetch full current state through the SDK, then ensure membership
            // and device tracking before its native encryption/send queue runs.
            client!!.syncOnceV2(SyncSettingsV2(0uL, true))
            r.members().close()
            val ids = r.activeHumanMemberIds()
            if (meta.s("kind") == "direct") check(ids.containsAll(listOf(meta.s("owner"), meta.s("peer")))) { "双方成员尚未加入，暂不能加密发送" }
            ids.forEach { client!!.encryption().userIdentity(it, true)?.close() }
            client!!.encryption().waitForE2eeInitializationTasks()
        }
    }
    suspend fun send(text: String, reply: ChatMessage? = null, extra: JsonObject? = null) = sendMutex.withLock {
        require(text.codePointCount(0, text.length) in 1..2000) { "消息须为 1–2000 个 Unicode 码点" }
        val room = requireNotNull(currentRoom); prepare(room)
        val t = requireNotNull(timeline)
        val content = requireNotNull(t.createMessageContent(MessageType.Text(TextMessageContent(text, null))))
        // Matrix's durable SDK send queue owns the stable txnId. We never
        // enqueue again to retry a failed item; retrySend reuses that txnId.
        if (reply != null) t.sendReply(content, (reply.id as EventOrTransactionId.EventId).eventId)
        else if (extra == null) t.send(content)
        else sendBound(t, room, content, extra)
    }
    private suspend fun sendBound(t: Timeline, room: String, content: RoomMessageEventContentWithoutRelation, extra: JsonObject) {
        val r = requireNotNull(client?.getRoom(room)); val a = requireNotNull(account); val dao = requireNotNull(database).local()
        val registered = CompletableDeferred<String>()
        r.enableSendQueue(false)
        val known = localEchoes(room)
        val metadata = buildJsonObject {
            extra["org.yitiao.business"]?.let { put("business", it) }
            extra["org.yitiao.attachments"]?.let { put("attachments", it) }
        }
        val op = "metadata.${UUID.randomUUID()}"
        val binding = buildJsonObject { put("room",room); put("metadata",metadata); put("known",JsonArray(known.map(::JsonPrimitive))) }
        dao.saveOperation(Operation(op,"sdk_metadata","binding",vault.encrypt(a.storage,op,binding.toString().toByteArray()),System.currentTimeMillis()))
        val listener = r.subscribeToSendQueueUpdates(object : SendQueueListener {
            override fun onUpdate(update: RoomSendQueueUpdate) { if (update is RoomSendQueueUpdate.NewLocalEvent && update.transactionId !in known) registered.complete(update.transactionId) }
        })
        try {
            t.sendWithExtraContent(content, extra.toString())
            val txn = withTimeout(15000) { registered.await() }
            val record = buildJsonObject { put("room", room); put("txn", txn); put("metadata", metadata) }
            dao.saveOperation(Operation(op, "sdk_metadata", "pending", vault.encrypt(a.storage, op, record.toString().toByteArray()), System.currentTimeMillis()))
            gateway.call("/api/native/rooms/${Gateway.segment(room)}/transactions/${Gateway.segment(txn)}", metadata)
            dao.saveOperation(Operation(op, "sdk_metadata", "registered", vault.encrypt(a.storage, op, record.toString().toByteArray()), System.currentTimeMillis()))
            r.enableSendQueue(true)
        } finally { listener.cancel(); listener.close() }
        // On failure the room stays paused; never send without outer metadata.
    }
    private suspend fun localEchoes(room: String): Set<String> {
        val ids = java.util.Collections.synchronizedSet(mutableSetOf<String>())
        val h = requireNotNull(client).subscribeToSendQueueUpdates(object : SendQueueRoomUpdateListener {
            override fun onUpdate(roomId: String, update: RoomSendQueueUpdate) { if(roomId == room && update is RoomSendQueueUpdate.NewLocalEvent) ids.add(update.transactionId) }
        })
        h.cancel();h.close();return ids.toSet()
    }
    suspend fun sendMedia(room: String, type: MessageType, extra: JsonObject) = sendMutex.withLock {
        check(currentRoom == room) { "发送前会话已改变，请返回原会话重试" }
        prepare(room)
        val t = requireNotNull(timeline)
        sendBound(t, room, requireNotNull(t.createMessageContent(type)), extra)
    }
    private suspend fun restoreMetadata() {
        val a = account ?: return; val dao = database?.local() ?: return
        for (op in dao.allOperations().filter { it.kind == "sdk_metadata" && it.state in listOf("pending","binding") }) {
            var d = json.parseToJsonElement(vault.decrypt(a.storage, op.key, op.encrypted).decodeToString()).jsonObject
            if(op.state == "binding") {
                val known = d.a("known").map { it.jsonPrimitive.content }.toSet()
                val candidates = localEchoes(d.s("room")) - known
                if(candidates.isEmpty()) { dao.saveOperation(op.copy(state="not_enqueued"));continue }
                check(candidates.size==1) { "上次消息与外层引用关联未完成，存在多个候选事务；队列保持暂停，不能盲目重发" }
                d = JsonObject(d + ("txn" to JsonPrimitive(candidates.single())))
                dao.saveOperation(op.copy(state="pending",encrypted=vault.encrypt(a.storage,op.key,d.toString().toByteArray())))
            }
            gateway.call("/api/native/rooms/${Gateway.segment(d.s("room"))}/transactions/${Gateway.segment(d.s("txn"))}", d.o("metadata"))
            dao.saveOperation(op.copy(state = "registered", updated = System.currentTimeMillis()))
        }
    }
    suspend fun retry(message: ChatMessage) { restoreMetadata(); timeline?.retrySend(message.id, SendTarget.Event); client?.getRoom(requireNotNull(currentRoom))?.enableSendQueue(true) }
    suspend fun edit(message: ChatMessage, text: String) {
        require(text.codePointCount(0, text.length) in 1..2000)
        val content = requireNotNull(timeline?.createMessageContent(MessageType.Text(TextMessageContent(text, null))))
        requireNotNull(timeline).edit(message.id, EditedContent.RoomMessage(content))
    }
    suspend fun redact(message: ChatMessage) { requireNotNull(timeline).redactEvent(message.id, null) }
    suspend fun close() = sessionMutex.withLock { stopSync(); client?.enableAllSendQueues(false); closeInternal() }
    private fun closeInternal() {
        sessionGeneration.incrementAndGet();timelineGeneration.incrementAndGet()
        val storage = account?.storage
        stopSync(); timelineHandle?.cancel(); timelineHandle?.close(); timelineHandle = null
        delegateHandle?.cancel(); delegateHandle?.close(); delegateHandle = null
        timeline?.close(); timeline = null; client?.close(); client = null
        database?.close(); database = null; account = null
        lockFile?.release(); lockFile = null; lockChannel?.close(); lockChannel = null
        messages.value = emptyList(); conversations.value = emptyList(); syncStatus.value = "本地数据已锁定"
        withdrawn.clear()
        synchronized(items) { items.forEach { it.close() }; items.clear() }
        if(storage!=null) File(context.cacheDir,"matrix/$storage").deleteRecursively()
        File(context.cacheDir, "previews").deleteRecursively(); File(context.cacheDir, "temporary").deleteRecursively()
    }
}
