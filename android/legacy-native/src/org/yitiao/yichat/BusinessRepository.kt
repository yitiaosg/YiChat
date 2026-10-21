package org.yitiao.yichat

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.*
import java.math.BigDecimal
import java.util.UUID

/** Business keys never double as Matrix message txnIds. No action on URL return. */
class BusinessRepository(private val matrix: MatrixRepository, private val gateway: Gateway, private val vault: SecureStore) {
    private val locks = mutableMapOf<String, Mutex>()
    private val previews = mutableMapOf<String, Pair<Long, JsonElement>>()
    private val previewMutex=Mutex()
    suspend fun persist(key: String, kind: String, state: String, payload: JsonObject) {
        val a = requireNotNull(matrix.account)
        matrix.database!!.local().saveOperation(Operation(key, kind, state, vault.encrypt(a.storage, key, payload.toString().toByteArray()), System.currentTimeMillis()))
    }
    fun decode(op: Operation): JsonObject = json.parseToJsonElement(vault.decrypt(matrix.account!!.storage, op.key, op.encrypted).decodeToString()).jsonObject
    suspend fun createOrder(room: String, amount: String, count: Int, title: String, key: String = UUID.randomUUID().toString()): JsonObject {
        require(amount.matches(Regex("(?:0|[1-9][0-9]*)(?:\\.[0-9]{1,16})?"))) { "金额须为最多16位小数的十进制数" }
        require(count in 1..1000 && BigDecimal(amount) >= BigDecimal("0.01").multiply(BigDecimal(count))) { "每份至少 0.01 YIDT" }
        val unresolved = matrix.database!!.local().allOperations().firstOrNull { it.kind=="order" && it.state in listOf("creating","unknown_create") && decode(it).s("room")==room }
        val original = unresolved?.let(::decode)
        require(original == null || (original.s("amount")==amount && original["count"]?.jsonPrimitive?.intOrNull==count && original.s("title")==title)) { "此会话有创建结果未核实的原订单；请在设置的业务记录中查询原请求，不能换新键重建" }
        val stableKey = unresolved?.key ?: key
        val payload = buildJsonObject { put("room", room); put("amount", amount); put("count", count); put("title", title); put("key", stableKey) }
        persist(stableKey, "order", "creating", payload)
        return try {
            val order = gateway.call("/api/orders", payload).jsonObject
            persist(stableKey, "order", order.s("state"), JsonObject(payload + ("order" to order)))
            order
        } catch (e: Exception) { persist(stableKey, "order", "unknown_create", payload); throw e }
    }
    private suspend fun fetchOrder(id: String) = gateway.call("/api/orders/${Gateway.segment(id)}").jsonObject
    suspend fun order(id: String): JsonObject = synchronized(locks) { locks.getOrPut(id) { Mutex() } }.withLock { fetchOrder(id) }
    suspend fun action(id: String, action: String, room: String): JsonObject {
        require(action in listOf("cancel", "fund", "receive", "claim"))
        val mutex = synchronized(locks) { locks.getOrPut(id) { Mutex() } }
        return mutex.withLock {
            val before = fetchOrder(id)
            require(before.o("actions").b(action)) { "服务器当前不允许此操作，请刷新状态" }
            persist("$id.$action", "order_action", "pending", obj("id" to id, "action" to action, "room" to room))
            val result = try { gateway.call("/api/orders/${Gateway.segment(id)}/$action", if (action in listOf("claim", "receive")) obj("room" to room) else obj()).jsonObject }
            catch (e: Exception) {
                // Unknown write result: read the original ID, never create/fund
                // another order. UI asks for another explicit action if needed.
                val found = runCatching { fetchOrder(id) }.getOrNull()
                persist("$id.$action", "order_action", "unknown", obj("id" to id, "action" to action))
                if (found == null) throw e
                if (action == "cancel" && found.s("state") != "cancelled") throw e
                if (action == "fund" && found.s("deposit").isBlank()) throw e
                found
            }
            persist("$id.$action", "order_action", result.s("state"), result)
            result
        }
    }
    suspend fun gift(room: String, instance: String, recipient: String, key: String): JsonObject {
        val unresolved = matrix.database!!.local().allOperations().firstOrNull { it.kind=="gift" && it.state=="pending" }
        val old = unresolved?.let(::decode)
        require(old == null || (old.s("room")==room && old.s("instance")==instance && old.s("recipient")==recipient)) { "有赠卡结果尚未核实，请先在业务记录中查询原赠送，不能重复转移" }
        val stableKey=unresolved?.key ?: key
        val payload = obj("room" to room, "instance" to instance, "recipient" to recipient, "key" to stableKey)
        persist(stableKey, "gift", "pending", payload)
        val result = try { gateway.call("/api/cards/gifts", payload).jsonObject }
        catch (e: Exception) { runCatching { gateway.call("/api/cards/gifts/by-key/${Gateway.segment(stableKey)}").jsonObject }.getOrElse { throw e } }
        persist(stableKey, "gift", "transferred", JsonObject(payload + ("gift" to result)))
        return result
    }
    suspend fun preview(url: String, room: String): JsonElement {
        // The caller supplies only URLs extracted after local decryption.
        require(ShareLinks.allowed(url))
        val key = "$room|$url"
        return previewMutex.withLock {
            val cached = synchronized(previews) { previews[key] }
            if (cached != null && System.currentTimeMillis() - cached.first < 20000) return@withLock cached.second
            gateway.call("/api/preview", obj("url" to url, "room" to room)).also { synchronized(previews) { if(previews.size>=128 && key !in previews) previews.remove(previews.keys.first());previews[key] = System.currentTimeMillis() to it } }
        }
    }
    fun clear() { synchronized(previews) { previews.clear() };synchronized(locks) { locks.clear() } }
}

object ShareLinks {
    private val rules = listOf(
        Regex("https://yidt\\.yitiao\\.org/explorer/tx/[a-f0-9]{64}"),
        Regex("https://yitiao\\.org/ycards/card/[a-f0-9]{32}/[A-Za-z0-9_-]+/"),
        Regex("https://yitiao\\.org/games/parachute/share/[a-f0-9]{32}/"),
        Regex("https://chat\\.yitiao\\.org/orders/[A-Za-z0-9_-]{32}")
    )
    fun allowed(s: String) = s.length <= 2048 && rules.any { it.matches(s) }
    fun extract(body: String): List<String> = Regex("https://[^\\s<>\\\"]+").findAll(body).map { it.value.trimEnd('.', ',', '，', '。') }.filter(::allowed).distinct().toList()
}
