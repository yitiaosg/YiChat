package org.yitiao.yichat

import android.app.Application
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.work.WorkManager
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.Constraints
import androidx.work.NetworkType
import androidx.work.ExistingWorkPolicy
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.serialization.json.*
import org.matrix.rustcomponents.sdk.*
import java.util.UUID

class YiChatApplication : Application() { val graph by lazy { Graph(this) } }
class Graph(val context: Application) {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    val vault = SecureStore(context)
    val gateway = Gateway()
    val auth = AuthRepository(vault, gateway)
    val matrix = MatrixRepository(context, vault, gateway, scope)
    val business = BusinessRepository(matrix, gateway, vault)
    val history = HistoryRepository(matrix, gateway, vault)
    val backup = BackupRepository(context, matrix, history, vault)
    val media = MediaRepository(context, matrix, gateway)
    suspend fun completePendingLogout() {
        val pending = vault.get("pending-logout") ?: return
        val client = Gateway(gateway.base).apply { token = pending.decodeToString() }
        try { client.call("/api/logout",obj()) }
        catch(e: GatewayError) { if(e.status!=401) throw e }
        vault.delete("pending-logout")
    }
}
data class AppState(val account: Account? = null, val room: String? = null, val busy: Boolean = false, val error: String? = null,
    val order: JsonObject? = null, val wallet: JsonObject? = null, val profiles: JsonArray = JsonArray(emptyList()), val roomInfo: JsonObject? = null,
    val keyResult: String? = null, val sas: String? = null, val verificationStage: String = "", val dark: Boolean? = null, val power: JsonObject? = null,
    val cards: JsonObject? = null, val card: JsonObject? = null, val gift: JsonObject? = null, val newDeviceNotice: Boolean = false)

class AppModel(application: Application) : AndroidViewModel(application) {
    val graph = (application as YiChatApplication).graph
    private fun savedTheme()=graph.vault.get("theme")?.decodeToString()?.toBooleanStrictOrNull()
    val state = MutableStateFlow(AppState(dark=savedTheme()))
    fun theme(dark: Boolean?) { if(dark==null) graph.vault.delete("theme") else graph.vault.put("theme",dark.toString().toByteArray());state.update { it.copy(dark=dark) } }
    val rooms = graph.matrix.conversations
    val messages = graph.matrix.messages
    val sync = graph.matrix.syncStatus
    val mediaPreview = MutableStateFlow<Pair<Attachment, MediaFileHandle>?>(null)
    val recording = MutableStateFlow(false)
    val voice = MutableStateFlow<Pair<java.io.File, String>?>(null)
    fun openAttachment(a: Attachment) = act { mediaPreview.value?.second?.close(); mediaPreview.value = a to graph.media.download(a) }
    fun closeAttachment() { mediaPreview.value?.second?.close(); mediaPreview.value = null }
    private var poll: Job? = null
    private var foreground = false
    private var pendingOrderReference: String? = null
    private var verificationController: SessionVerificationController? = null
    init {
        graph.matrix.onInvalidSession = { viewModelScope.launch { lock() } }
        act {
            graph.completePendingLogout()
            graph.auth.restore()?.let { a -> graph.gateway.call("/api/session"); graph.matrix.open(a); state.update { it.copy(account = a,newDeviceNotice=a.newDevice) }; refreshAuthority();background() }
        }
    }
    fun act(block: suspend () -> Unit) {
        viewModelScope.launch {
            val credential=graph.gateway.token
            state.update { it.copy(busy = true, error = null) }
            try { block() }
            catch (e: CancellationException) { throw e }
            catch (e: Exception) {
                // SDK error strings can contain request data. Display a safe
                // generic error; structured business errors contain no tokens.
                if (credential!=null && credential!=graph.gateway.token) return@launch
                if (e is GatewayError && e.status == 401) lock()
                state.update { it.copy(error = if (e is GatewayError) e.reason else if (e is IllegalArgumentException || e is IllegalStateException) e.message ?: "操作未完成" else "操作未完成，请检查网络或服务器状态；未知结果请先刷新原记录") }
            } finally { state.update { it.copy(busy = false) } }
        }
    }
    private fun background() {
        poll?.cancel()
        if(!foreground) return
        poll = viewModelScope.launch {
            while (isActive && state.value.account != null) {
                runCatching { graph.gateway.call("/api/session") }.onFailure { if (it is GatewayError && it.status == 401) lock() }
                runCatching { graph.history.catchUp(); graph.history.exchange() }
                    .onFailure { graph.history.status.value = "原包增量备份未完成；水位保留，稍后重试" }
                delay(60000)
            }
        }
    }
    fun callback(uri: Uri) = act {
        val a = graph.auth.callback(uri)
        if(uri.path=="/app/return" && uri.getQueryParameter("order")!=null) {
            require(uri.queryParameterNames==setOf("order") && uri.getQueryParameters("order").size==1)
            val ref=uri.getQueryParameter("order").orEmpty()
            require(ref.matches(Regex("[A-Za-z0-9_-]{32}"))) { "订单回跳引用无效" }
            pendingOrderReference=ref
        }
        if (a != null) { graph.matrix.open(a); state.update { it.copy(account = a,newDeviceNotice=a.newDevice) }; background() }
        refreshAuthority()
    }
    suspend fun refreshAuthority() {
        if (state.value.account == null) return
        graph.gateway.call("/api/session")
        val wallet=graph.gateway.call("/api/wallet").jsonObject
        state.update { it.copy(wallet=wallet) }
        pendingOrderReference?.let { reference ->
            val order=graph.business.order(reference)
            state.update { it.copy(order=order) };pendingOrderReference=null
        }
        state.value.order?.let { old -> val fresh = graph.business.order(old.s("id")); state.update { if(it.order?.s("id")==old.s("id")) it.copy(order = fresh) else it } }
    }
    fun resume() { foreground=true; graph.matrix.startSync(); if (state.value.account != null) { background();act { refreshAuthority() } } }
    fun pause() { foreground=false; poll?.cancel();poll=null;graph.matrix.stopSync() }
    fun login(resume: Boolean, open: (Uri) -> Unit) = act { graph.completePendingLogout(); open(graph.auth.begin(resume)) }
    fun cancelLogin() { graph.auth.cancel(); state.update { it.copy(error = "已取消本次登录，可重新开始") } }
    fun acknowledgeDevice() { val a=graph.auth.acknowledgeDevice(); state.update { it.copy(account=a,newDeviceNotice=false) } }
    fun openRoom(id: String) = act {
        graph.history.catchUp()
        graph.matrix.select(id)
        val info = runCatching { graph.gateway.call("/api/rooms/${Gateway.segment(id)}").jsonObject }.getOrNull()
        state.update { it.copy(room = id, roomInfo = info) }
    }
    fun closeRoom() { state.update { it.copy(room = null, roomInfo = null) } }
    fun send(text: String, reply: ChatMessage?, edit: ChatMessage?, queued: () -> Unit) = act { if (edit != null) graph.matrix.edit(edit, text) else graph.matrix.send(text, reply); queued() }
    fun history() = act { graph.matrix.history() }
    fun read() = act { graph.matrix.read() }
    fun join(id: String) = act { graph.matrix.joined(id); delay(1000); graph.matrix.refreshRooms() }
    fun leave() = act { graph.matrix.leave(requireNotNull(state.value.room)); closeRoom() }
    fun search(q: String) = act { val found = graph.gateway.call("/api/users?q=${Gateway.segment(q)}").jsonArray; state.update { it.copy(profiles = found) } }
    fun createRoom(users: List<String>, group: Boolean, name: String) = act {
        val key = UUID.randomUUID().toString(); val data = buildJsonObject { put("kind", if (group) "group" else "direct"); put("users", JsonArray(users.map(::JsonPrimitive))); put("name", name); put("key", key) }
        graph.business.persist(key, "room", "pending", data)
        val id = try { graph.gateway.call("/api/rooms", data).jsonObject.s("room_id") }
        catch (e: Exception) { graph.gateway.call("/api/room-creations/$key").jsonObject.s("room_id").ifBlank { throw e } }
        graph.business.persist(key, "room", "created", obj("room" to id)); delay(500); graph.matrix.refreshRooms(); openRoom(id)
    }
    fun invite(username: String) = act { graph.gateway.call("/api/rooms/${Gateway.segment(requireNotNull(state.value.room))}/invite", obj("username" to username)) }
    fun roomDetails() = act {
        val room = requireNotNull(state.value.room)
        val members = graph.gateway.call("/_matrix/client/v3/rooms/${Gateway.segment(room)}/members").jsonObject
        val ids = members.a("chunk").mapNotNull { x -> x.jsonObject.s("state_key").takeIf { x.jsonObject.o("content").s("membership") == "join" } }
        val profiles = graph.gateway.call("/api/profiles", buildJsonObject { put("mxids", JsonArray(ids.take(100).map(::JsonPrimitive))) }).jsonArray
        val power = graph.gateway.call("/_matrix/client/v3/rooms/${Gateway.segment(room)}/state/m.room.power_levels").jsonObject
        state.update { it.copy(profiles = profiles, power = power) }
    }
    fun moderator(mxid: String, enabled: Boolean) = act {
        val room = requireNotNull(state.value.room)
        val power = graph.gateway.call("/_matrix/client/v3/rooms/${Gateway.segment(room)}/state/m.room.power_levels").jsonObject
        val users = JsonObject(power.o("users") + (mxid to JsonPrimitive(if (enabled) 50 else 0)))
        graph.gateway.call("/_matrix/client/v3/rooms/${Gateway.segment(room)}/state/m.room.power_levels", JsonObject(power + ("users" to users)), "PUT")
        roomDetails()
    }
    fun wallet() = act { val wallet = graph.gateway.call("/api/wallet").jsonObject; state.update { it.copy(wallet = wallet) } }
    fun cards(page: Int) = act { val cards = graph.gateway.call("/api/cards/mine?page=$page").jsonObject; state.update { it.copy(cards = cards, card = null, gift = null) } }
    fun selectCard(id: String) = act { val card = graph.gateway.call("/api/cards/${Gateway.segment(id)}/render").jsonObject; state.update { it.copy(card = card) } }
    fun giftCard(recipient: String) = act {
        val selected = requireNotNull(state.value.card); val room = requireNotNull(state.value.room)
        val gift = graph.business.gift(room, selected.s("id"), recipient, UUID.randomUUID().toString())
        state.update { it.copy(gift = gift) }
    }
    fun announceGift() = act {
        val gift = requireNotNull(state.value.gift); val room = requireNotNull(state.value.room); val key = gift.s("id") + ".gift_message"
        val existing=graph.matrix.database!!.local().operation(key)
        require(existing == null || existing.state=="retryable") { "此赠卡消息已入原出站箱，请重试原消息" }
        graph.business.persist(key,"gift_message","queued",gift)
        try { graph.matrix.send("赠送了一张 YCard", extra = buildJsonObject {
            put("org.yitiao.business",obj("kind" to "gift","id" to gift.s("id")))
            put("org.yitiao.share",obj("type" to "gift","id" to gift.s("id")))
        }) } catch(e: Exception) {
            val bound=graph.matrix.database!!.local().allOperations().any { op -> op.kind=="sdk_metadata" && graph.business.decode(op).o("metadata").o("business").s("id")==gift.s("id") && op.state!="not_enqueued" }
            if(!bound) graph.business.persist(key,"gift_message","retryable",gift)
            throw e
        }
    }
    fun createOrder(amount: String, count: Int, title: String) = act {
        val order = graph.business.createOrder(requireNotNull(state.value.room), amount, count, title)
        state.update { it.copy(order = order) }
    }
    fun showOrder(id: String) = act { val o = graph.business.order(id); state.update { it.copy(order = o) } }
    fun pollOrder(id: String) = act { val o=graph.business.order(id);state.update { if(it.order?.s("id")==id) it.copy(order=o) else it } }
    fun recoverOrder(op: Operation) = act {
        val data=graph.business.decode(op)
        val known=data.o("order").s("id")
        val order=if(known.isNotBlank()) graph.business.order(known) else graph.business.createOrder(data.s("room"),data.s("amount"),data["count"]!!.jsonPrimitive.int,data.s("title"),op.key)
        state.update { it.copy(order=order) }
    }
    fun recoverGift(op: Operation, retry: Boolean = false) = act {
        val d=graph.business.decode(op)
        val gift=if(retry) graph.business.gift(d.s("room"),d.s("instance"),d.s("recipient"),op.key)
            else graph.gateway.call("/api/cards/gifts/by-key/${Gateway.segment(op.key)}").jsonObject
        graph.business.persist(op.key,"gift","transferred",JsonObject(d+("gift" to gift)))
        state.update { it.copy(gift=gift,error="服务器确认原赠送已完成；关联消息可独立发送，无需再次转移资产") }
    }
    fun announceRecoveredGift(op: Operation) = act {
        val d=graph.business.decode(op)
        val gift=graph.gateway.call("/api/cards/gifts/by-key/${Gateway.segment(op.key)}").jsonObject
        val room=d.s("room"); graph.history.catchUp(); graph.matrix.select(room)
        state.update { it.copy(room=room,gift=gift) }
        announceGift()
    }
    fun dismissOrder() { state.update { it.copy(order = null) } }
    fun orderAction(action: String, expectedId: String) = act {
        val original = requireNotNull(state.value.order); require(original.s("id")==expectedId) { "订单已切换，请重新确认" }; val result = graph.business.action(original.s("id"), action, original.s("room"))
        state.update { if(it.order?.s("id")==expectedId) it.copy(order = result) else it }
        if(action=="fund" && result.s("deposit").isNotBlank()) announceOrder(result,false)
        if(action=="receive" && result.a("claims").any { it.jsonObject.s("user_id")==state.value.account?.uid }) announceOrder(result,true)
    }
    fun sendOrder(receipt: Boolean = false) = act { announceOrder(requireNotNull(state.value.order),receipt) }
    private suspend fun announceOrder(o: JsonObject, receipt: Boolean) {
        require(o.s("deposit").isNotBlank())
        if(state.value.room!=o.s("room")) {
            graph.history.catchUp(); graph.matrix.select(o.s("room"))
            val info=graph.gateway.call("/api/rooms/${Gateway.segment(o.s("room"))}").jsonObject
            state.update { it.copy(room=o.s("room"),roomInfo=info) }
        }
        val id = o.s("id"); val key = "$id.${if (receipt) "receipt" else "bubble"}"
        val previous = graph.matrix.database!!.local().operation(key)
        require(previous == null || previous.state=="retryable") { "此消息已入出站箱；请在聊天中重试原消息，避免重复发送" }
        graph.business.persist(key, "business_message", "queued", o)
        try { graph.matrix.send(o.s("url"), extra = buildJsonObject {
            put("org.yitiao.business", obj("kind" to "order", "id" to id))
            put("org.yitiao.share", obj("type" to if (receipt) "receipt" else "order", "id" to id, "url" to o.s("url")))
        }) } catch(e: Exception) {
            val bound = graph.matrix.database!!.local().allOperations().any { op -> op.kind=="sdk_metadata" && graph.business.decode(op).o("metadata").o("business").s("id")==id && op.state!="not_enqueued" }
            if(!bound) graph.business.persist(key,"business_message","retryable",o)
            throw e
        }
    }
    fun setupVerification() = act {
        val c = requireNotNull(graph.matrix.client).getSessionVerificationController(); verificationController = c
        c.setDelegate(object : SessionVerificationControllerDelegate {
            override fun didReceiveVerificationRequest(details: SessionVerificationRequestDetails) { state.update { it.copy(verificationStage = "收到验证请求，请确认后接受") } }
            override fun didAcceptVerificationRequest() { state.update { it.copy(verificationStage = "请求已接受，可开始 SAS") } }
            override fun didStartSasVerification() { state.update { it.copy(verificationStage = "等待双方安全码") } }
            override fun didReceiveVerificationData(data: SessionVerificationData) { state.update { it.copy(sas = when(data) { is SessionVerificationData.Decimals -> data.values.joinToString(" · "); is SessionVerificationData.Emojis -> data.emojis.joinToString(" ") { it.symbol() } }, verificationStage = "请在两台设备核对，完全一致才批准") } }
            override fun didFail() { state.update { it.copy(verificationStage = "验证失败", sas = null) } }
            override fun didCancel() { state.update { it.copy(verificationStage = "验证已取消", sas = null) } }
            override fun didFinish() { state.update { it.copy(verificationStage = "设备验证完成", sas = null) } }
        })
        state.update { it.copy(verificationStage = "验证控制器已就绪") }
    }
    fun verify(action: String) = act {
        val c = requireNotNull(verificationController)
        when(action) { "request" -> c.requestDeviceVerification(); "accept" -> c.acceptVerificationRequest(); "start" -> c.startSasVerification(); "approve" -> c.approveVerification(); "cancel" -> c.cancelVerification() }
    }
    fun enableKeyBackup() = act {
        val encryption=requireNotNull(graph.matrix.client).encryption()
        encryption.waitForE2eeInitializationTasks()
        check(encryption.recoveryState()==RecoveryState.DISABLED) { "已有密钥恢复设置或状态尚不完整；请先使用原恢复密钥，不覆盖已有备份" }
        val key = encryption.enableRecovery(true, null, object : EnableRecoveryProgressListener { override fun onUpdate(status: EnableRecoveryProgress) {} })
        state.update { it.copy(keyResult = key,error=if(encryption.recoveryState()==RecoveryState.ENABLED) null else "恢复设置尚不完整，请保留刚生成的恢复密钥并检查交叉签名与备份状态") }
    }
    fun recoverKeys(key: String) = act { val encryption=requireNotNull(graph.matrix.client).encryption();encryption.recover(key);check(encryption.recoveryState()==RecoveryState.ENABLED) { "恢复状态尚不完整，请检查交叉签名与历史密钥备份；不能确认已恢复" };state.update { it.copy(error = "Matrix 密钥恢复完成；缺失消息包仍需独立补齐") } }
    fun clearKey() { state.update { it.copy(keyResult = null) } }
    fun logout() = act {
        state.value.account?.token?.let { graph.vault.put("pending-logout",it.toByteArray()) }
        graph.matrix.stopSync()
        try { graph.completePendingLogout() } catch(e: Exception) { /* The encrypted revocation record and UI below retain this failure. */ }
        finally {
            lock()
            if(graph.vault.get("pending-logout")!=null) {
                WorkManager.getInstance(getApplication()).enqueueUniqueWork("yichat-session-revoke",ExistingWorkPolicy.KEEP,
                    OneTimeWorkRequestBuilder<RevokeWorker>().setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()).build())
                state.update { it.copy(error="本地数据已锁定；服务端撤销尚未确认，系统将在联网后重试。重新登录前会先完成撤销。") }
            }
        }
    }
    private suspend fun lock() {
        val self=currentCoroutineContext()[Job]
        poll?.let { if(it!==self) it.cancel() };poll=null
        viewModelScope.coroutineContext[Job]?.children?.filter { it!==self }?.forEach { it.cancel() }
        withContext(NonCancellable) {
            WorkManager.getInstance(getApplication()).cancelUniqueWork("yichat-backup")
            closeAttachment(); voice.value?.first?.delete();voice.value=null; graph.matrix.close(); graph.auth.lock(); graph.business.clear(); state.value = AppState(dark=savedTheme())
        }
    }
}
class RevokeWorker(context: android.content.Context, params: androidx.work.WorkerParameters) : androidx.work.CoroutineWorker(context,params) {
    override suspend fun doWork(): Result = try { (applicationContext as YiChatApplication).graph.completePendingLogout(); Result.success() } catch(e: Exception) { Result.retry() }
}
