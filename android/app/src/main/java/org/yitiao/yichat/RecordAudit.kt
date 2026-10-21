package org.yitiao.yichat

import android.util.Base64
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*
import java.security.MessageDigest
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.temporal.ChronoUnit
import javax.crypto.spec.SecretKeySpec

/** Hashes actual saved ciphertext locally. No room key or message is uploaded. */
class RecordAudit(private val store: PacketStore,private val vault: SecureStore) {
    companion object { const val FORMAT="yichat-record-digests-v1" }
    private fun canonical(value:JsonElement):ByteArray=(value.toString()+"\n").toByteArray(Charsets.UTF_8)
    private fun hex(bytes:ByteArray)=bytes.joinToString(""){ "%02x".format(it) }
    private fun range(start:String,end:String):List<LocalDate>{
        val first=LocalDate.parse(start);val last=LocalDate.parse(end);val count=ChronoUnit.DAYS.between(first,last)+1
        require(count in 1..5 && first.toString()==start && last.toString()==end){"请选择连续1至5个UTC日期"}
        return (0 until count.toInt()).map{first.plusDays(it.toLong())}
    }
    suspend fun rooms():JsonArray=withContext(Dispatchers.IO){
        val a=requireNotNull(store.account){"请先登录"};val dao=requireNotNull(store.database).local()
        val rooms=dao.recordRooms();check(store.account?.token==a.token)
        JsonArray(rooms.map(::JsonPrimitive))
    }
    suspend fun days(room:String,start:String,end:String):JsonObject=withContext(Dispatchers.IO){
        require(room.startsWith("!") && room.length<=255)
        val dates=range(start,end);val a=requireNotNull(store.account){"请先登录"};val dao=requireNotNull(store.database).local()
        val secret=vault.accountKey(a.storage,"data");val key=SecretKeySpec(secret,"AES")
        val digests=dates.associateWith{day->MessageDigest.getInstance("SHA-256").apply{update(canonical(buildJsonObject{put("day",day.toString());put("format",FORMAT);put("room_id",room);put("version",1)}))}}
        val counts=dates.associateWith{0}.toMutableMap();val missing=dates.associateWith{0}.toMutableMap()
        val low=dates.first().atStartOfDay().toInstant(ZoneOffset.UTC).toEpochMilli();val high=dates.last().plusDays(1).atStartOfDay().toInstant(ZoneOffset.UTC).toEpochMilli()
        try {
            var after=0L
            while(true){
                val batch=dao.recordReceipts(room,after,500);if(batch.isEmpty())break
                for(receipt in batch){
                    after=receipt.sequence
                    val metadata=json.parseToJsonElement(SecureStore.open(key,"receipt:$room:${receipt.event}",receipt.encrypted).decodeToString()).jsonObject
                    val at=metadata["received_ms"]!!.jsonPrimitive.long;if(at<low || at>=high)continue
                    val day=java.time.Instant.ofEpochMilli(at).atOffset(ZoneOffset.UTC).toLocalDate();counts[day]=counts.getValue(day)+1
                    val packet=dao.packet(room,receipt.event)
                    if(packet==null){missing[day]=missing.getValue(day)+1;continue}
                    var raw:ByteArray?=null
                    try {
                        raw=SecureStore.open(key,"packet:$room:${receipt.event}",packet.encrypted)
                        HistoryRepository.verifyPacket(raw,metadata.s("sha256"),room,receipt.event)
                        val event=json.parseToJsonElement(raw.decodeToString()).jsonObject
                        require(receipt.hash==metadata.s("sha256") && event.s("type")==metadata.s("type") && event.s("sender")==metadata.s("sender"))
                        val redacts=(event.o("content").s("redacts").ifBlank{event.s("redacts")}).takeIf{event.s("type")=="m.room.redaction" && it.isNotBlank()}
                        digests.getValue(day).update(canonical(buildJsonObject{
                            put("event_id",receipt.event);put("received_ms",at);put("redacts",redacts?.let(::JsonPrimitive)?:JsonNull);put("room_id",room);put("sender",event.s("sender"));put("sequence",receipt.sequence);put("sha256",hex(MessageDigest.getInstance("SHA-256").digest(raw)));put("type",event.s("type"))
                        }))
                    }catch(_:Exception){missing[day]=missing.getValue(day)+1}finally{raw?.fill(0)}
                }
            }
            check(store.account?.token==a.token)
            buildJsonObject {
                put("format",FORMAT);put("room",room);put("start",start);put("end",end)
                put("days",buildJsonArray {
                    for(day in dates) add(buildJsonObject {
                        put("day",day.toString());put("count",counts.getValue(day))
                        put("complete",missing.getValue(day)==0);put("missing",missing.getValue(day))
                        put("sha256",if(missing.getValue(day)==0) JsonPrimitive(hex(digests.getValue(day).digest())) else JsonNull)
                    })
                })
            }
        }finally{secret.fill(0)}
    }
    suspend fun page(room:String,day:String,afterText:String):JsonObject=withContext(Dispatchers.IO){
        val date=range(day,day).single();require(room.startsWith("!") && room.length<=255)
        val from=afterText.toLongOrNull()?:0L;require(from>=0)
        val a=requireNotNull(store.account){"请先登录"};val dao=requireNotNull(store.database).local()
        val secret=vault.accountKey(a.storage,"data");val key=SecretKeySpec(secret,"AES")
        val low=date.atStartOfDay().toInstant(ZoneOffset.UTC).toEpochMilli();val high=low+86400000L
        val rows=mutableListOf<JsonElement>();var size=0;var cursor=from;var more=false
        try {
            outer@ while(true){
                val batch=dao.recordReceipts(room,cursor,200);if(batch.isEmpty())break
                for(receipt in batch){
                    val metadata=json.parseToJsonElement(SecureStore.open(key,"receipt:$room:${receipt.event}",receipt.encrypted).decodeToString()).jsonObject
                    val at=metadata["received_ms"]!!.jsonPrimitive.long
                    if(at<low || at>=high){cursor=receipt.sequence;continue}
                    if(rows.size>=30){more=true;break@outer}
                    val packet=requireNotNull(dao.packet(room,receipt.event)){"原包缺失，请重新核验"}
                    val raw=SecureStore.open(key,"packet:$room:${receipt.event}",packet.encrypted)
                    val row=try{HistoryRepository.verifyPacket(raw,metadata.s("sha256"),room,receipt.event);buildJsonObject{put("event_id",receipt.event);put("received_ms",at);put("sequence",receipt.sequence.toString());put("sha256",metadata.s("sha256"));put("withdrawn",dao.tombstone(room,receipt.event)!=null);put("raw",Base64.encodeToString(raw,Base64.NO_WRAP))}}finally{raw.fill(0)}
                    val bytes=row.toString().length
                    if(rows.isNotEmpty() && size+bytes>260000){more=true;break@outer}
                    rows.add(row);size+=bytes;cursor=receipt.sequence
                }
            }
            check(store.account?.token==a.token)
            buildJsonObject{put("rows",JsonArray(rows));put("next_after",if(more)JsonPrimitive(cursor.toString()) else JsonNull)}
        }finally{secret.fill(0)}
    }
}
