package org.yitiao.yichat

import android.util.Base64
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*
import javax.crypto.spec.SecretKeySpec

/**
 * Pages older messages out of the automatic packet backup (Room database), not
 * the WebView sync cache. Used when the server cannot be reached or no longer
 * holds older history. Every packet is re-verified against its authenticated
 * receipt; withdrawn messages and redactions are never returned.
 */
class LocalTimeline(private val store: PacketStore, private val vault: SecureStore) {
    companion object { const val PAGE = 40; const val BYTES = 260000 }

    /**
     * [cursor] is the receipt sequence returned by a previous page. Without one,
     * only packets sent at or before [beforeTs] are returned; the client skips
     * any it already shows.
     */
    suspend fun older(room: String, cursor: String, beforeTs: Long, limit: Int): JsonObject = withContext(Dispatchers.IO) {
        require(room.startsWith("!") && room.length <= 255) { "会话无效" }
        val want = limit.coerceIn(1, PAGE)
        var before = cursor.toLongOrNull()?.also { require(it >= 0) } ?: Long.MAX_VALUE
        val byTime = cursor.isBlank()
        val a = requireNotNull(store.account) { "请先登录" }; val dao = requireNotNull(store.database).local()
        val secret = vault.accountKey(a.storage, "data"); val key = SecretKeySpec(secret, "AES")
        val rows = mutableListOf<JsonElement>(); var size = 0; var more = false
        try {
            outer@ while (true) {
                val batch = dao.olderReceipts(room, before, 200); if (batch.isEmpty()) break
                for (receipt in batch) {
                    if (rows.size >= want) { more = true; break@outer }
                    val metadata = runCatching { json.parseToJsonElement(SecureStore.open(key, "receipt:$room:${receipt.event}", receipt.encrypted).decodeToString()).jsonObject }.getOrNull()
                    val ts = metadata?.get("origin_server_ts")?.jsonPrimitive?.longOrNull
                    if (metadata == null || (byTime && (ts == null || ts > beforeTs))) { before = receipt.sequence; continue }
                    if (metadata.s("redacts").isNotBlank() || metadata["tombstone"] is JsonObject || dao.tombstone(room, receipt.event) != null) { before = receipt.sequence; continue }
                    val packet = dao.packet(room, receipt.event)
                    if (packet == null) { before = receipt.sequence; continue }
                    var raw: ByteArray? = null
                    val row = try {
                        raw = SecureStore.open(key, "packet:$room:${receipt.event}", packet.encrypted)
                        HistoryRepository.verifyPacket(raw, receipt.hash, room, receipt.event)
                        val event = json.parseToJsonElement(raw.decodeToString()).jsonObject
                        if (event["state_key"] != null || event.s("type") == "m.room.redaction" || event.s("type") != metadata.s("type")) null
                        else buildJsonObject { put("event_id", receipt.event);put("sequence",receipt.sequence); put("sha256", receipt.hash); put("raw", Base64.encodeToString(raw, Base64.NO_WRAP));put("received_ms",metadata["received_ms"]?:JsonNull) }
                    } catch (_: Exception) { null } finally { raw?.fill(0) }
                    if (row != null) {
                        val bytes = row.toString().length
                        if (rows.isNotEmpty() && size + bytes > BYTES) { more = true; break@outer }
                        rows.add(row); size += bytes
                    }
                    before = receipt.sequence
                }
            }
            check(store.account?.token == a.token) { "会话已锁定" }
            buildJsonObject { put("rows", JsonArray(rows)); put("cursor", if (more || rows.isNotEmpty()) JsonPrimitive(before.toString()) else JsonNull); put("more", more) }
        } finally { secret.fill(0) }
    }
}
