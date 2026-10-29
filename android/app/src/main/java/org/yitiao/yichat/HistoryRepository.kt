package org.yitiao.yichat

import android.util.Base64
import androidx.room.withTransaction
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.*
import java.security.MessageDigest

class HistoryRepository(private val matrix: PacketStore, private val gateway: Gateway, private val vault: SecureStore) {
    private val mutex = Mutex()
    val status = MutableStateFlow("尚未备份原消息包")
    suspend fun catchUp(relogin: Boolean = false) = mutex.withLock {
        val db = matrix.database ?: return@withLock
        val a = matrix.account ?: return@withLock
        val dao = db.local()
        val previous = dao.watermark("*")?.untilMs ?: 0
        val since = (previous - if (relogin) 600000 else 300000).coerceAtLeast(0)
        var after = 0L; var until = 0L
        do {
            val page = gateway.call("/api/history/updates?since=$since&after=$after&until=$until").jsonObject
            until = page["until"]!!.jsonPrimitive.long
            val records = page.a("records").map { it.jsonObject }
            db.withTransaction {
                // Apply all authenticated withdrawals before saving old bodies.
                for (r in records) {
                    val room = r.s("room"); val eid = r.s("event_id"); val seq = r["sequence"]!!.jsonPrimitive.long
                    val target = r.s("redacts").ifBlank { if (r["tombstone"] is JsonObject) eid else "" }
                    if (target.isNotEmpty()) { dao.tombstone(Tombstone(room, target, seq)) }
                    dao.receipt(TrustedReceipt(room, eid, r.s("sha256"), seq, vault.encrypt(a.storage, "receipt:$room:$eid", r.toString().toByteArray())))
                }
            }
            for (batch in records.filter { it["tombstone"] !is JsonObject }.chunked(20)) {
                val result = gateway.call("/api/history/packets", buildJsonObject { put("ids", JsonArray(batch.map { JsonPrimitive(it.s("event_id")) })) }).jsonObject
                for (p in result.a("packets").map { it.jsonObject }) {
                    val r = batch.first { it.s("event_id") == p.s("event_id") }
                    accept(p, r.s("room"))
                }
            }
            after = page["next_after"]?.jsonPrimitive?.longOrNull ?: 0L
            // Advancing only after packet commits: a crash repeats this window.
            if (after == 0L && dao.allOperations().none { it.kind == "missing_packet" && it.state == "pending" }) dao.watermark(Watermark("*", until, null))
        } while (after != 0L)
        matrix.refreshTombstones()
        val missing=dao.allOperations().count { it.kind=="missing_packet" && it.state=="pending" }
        status.value = if(missing==0) "原消息包已可靠保存；UTC ${java.time.Instant.ofEpochMilli(until)}" else "有 $missing 份已清理原包等待在线 App 成员补齐；无人持有原包时无法恢复，成功水位保留"
    }
    private suspend fun accept(p: JsonObject, room: String) {
        val a = matrix.account ?: return; val db = matrix.database ?: return; val dao = db.local(); val eid = p.s("event_id")
        if (p["tombstone"] is JsonObject) {
            db.withTransaction { dao.tombstone(Tombstone(room, eid, 0))
                dao.operation("missing.${SecureStore.hash(eid)}")?.let { dao.saveOperation(it.copy(state="withdrawn")) }
            }; return
        }
        if (p.s("raw").isBlank()) {
            // Persist a missing ID, not an ephemeral peer request ID; reissue it
            // after restart so server absence and permission are checked again.
            val key = "missing.${SecureStore.hash(eid)}"
            dao.saveOperation(Operation(key, "missing_packet", "pending", vault.encrypt(a.storage, key, obj("room" to room, "id" to eid).toString().toByteArray()), System.currentTimeMillis()))
            return
        }
        val receipt = requireNotNull(dao.receipt(room, eid)) { "缺少认证摘要" }
        val raw = Base64.decode(p.s("raw"), Base64.NO_WRAP)
        verifyPacket(raw, receipt.hash, room, eid)
        val r = json.parseToJsonElement(vault.decrypt(a.storage, "receipt:$room:$eid", receipt.encrypted).decodeToString()).jsonObject
        require(json.parseToJsonElement(raw.decodeToString()).jsonObject.s("type") == r.s("type"))
        db.withTransaction {
            if (dao.packet(room, eid) == null)
                dao.addPacket(Packet(room, eid, receipt.sequence, receipt.hash, vault.encrypt(a.storage, "packet:$room:$eid", raw)))
            dao.operation("missing.${SecureStore.hash(eid)}")?.let { dao.saveOperation(it.copy(state="restored")) }
        }
    }
    suspend fun exchange() = mutex.withLock {
        val a = matrix.account ?: return@withLock; val dao = matrix.database?.local() ?: return@withLock
        val missing = dao.allOperations().filter { it.kind == "missing_packet" && it.state == "pending" }.take(20)
        val wants = mutableMapOf<String, String>()
        for (op in missing) {
            val d = json.parseToJsonElement(vault.decrypt(a.storage, op.key, op.encrypted).decodeToString()).jsonObject
            val packets = gateway.call("/api/history/packets", buildJsonObject { put("ids", JsonArray(listOf(JsonPrimitive(d.s("id"))))) }).jsonObject.a("packets")
            for (p in packets.map { it.jsonObject }) {
                if (p.s("request").isNotEmpty()) wants[p.s("request")] = d.s("room")
                else accept(p, d.s("room"))
            }
        }
        val r = gateway.call("/api/history/exchange", buildJsonObject { put("requests", JsonArray(wants.keys.map(::JsonPrimitive))) }).jsonObject
        for (p in r.a("fulfilled").map { it.jsonObject }) accept(p, wants[p.s("request")] ?: continue)
        for (ask in r.a("asked").map { it.jsonObject }) {
            val local = dao.packet(ask.s("room"), ask.s("event_id")) ?: continue
            if (dao.tombstone(local.room, local.event) != null) continue
            // Never trust a relay's self-reported hash over our authenticated receipt.
            val receipt = dao.receipt(local.room, local.event) ?: continue
            if (receipt.hash != ask.s("sha256")) continue
            val raw = vault.decrypt(a.storage, "packet:${local.room}:${local.event}", local.encrypted)
            verifyPacket(raw, receipt.hash, local.room, local.event)
            gateway.call("/api/history/exchange", buildJsonObject { put("offers", buildJsonArray { add(buildJsonObject { put("request", ask.s("request")); put("raw", Base64.encodeToString(raw, Base64.NO_WRAP)) }) }) })
        }
    }
    companion object {
        fun verifyPacket(raw: ByteArray, expectedHash: String, room: String, event: String) {
            require(raw.size in 2..160000 && raw.last() == 10.toByte()) { "canonical 包缺少末尾 LF" }
            val actual = MessageDigest.getInstance("SHA-256").digest(raw).joinToString("") { "%02x".format(it) }
            require(MessageDigest.isEqual(actual.toByteArray(), expectedHash.toByteArray())) { "原消息包哈希不匹配" }
            val e = json.parseToJsonElement(raw.decodeToString()).jsonObject
            require(e.s("room_id") == room && e.s("event_id") == event) { "原消息包身份不匹配" }
        }
    }
}
