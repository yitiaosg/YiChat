package org.yitiao.yichat

import android.util.Base64
import androidx.room.withTransaction
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*

/** Durable display inbox first; authenticated receipts promote it into the archive. */
class LiveMessages(private val store:PacketStore,private val gateway:Gateway,private val vault:SecureStore) {
 private val mutex=Mutex()
 val changes=MutableSharedFlow<String>(extraBufferCapacity=1,onBufferOverflow=BufferOverflow.DROP_OLDEST)
 private fun key(room:String,event:String)="live."+SecureStore.hash(room+"\u0000"+event)
 private fun current(a:Account){check(store.account?.token==a.token){"本地账户已锁定"}}
 fun changed(){store.account?.let{changes.tryEmit(it.token)}}
 suspend fun remember(args:JsonObject,owner:Account?=null):JsonObject=withContext(Dispatchers.IO) {
  val a=requireNotNull(store.account);require(owner==null||owner.token==a.token){"本地账户已改变"};val db=requireNotNull(store.database);val dao=db.local();val room=args.s("room");val raw=args.s("raw");require(raw.toByteArray().size in 2..160000)
  val event=json.parseToJsonElement(raw).jsonObject;val id=event.s("event_id");require(room.startsWith("!")&&room.length<=255&&id.startsWith("$")&&id.length<=255&&event.s("room_id")==room&&event.s("type") in listOf("m.room.message","m.room.encrypted","m.room.redaction")){"消息身份无效"}
  val canonical=event.toString()+"\n";current(a)
  val existing=dao.packet(room,id)
  if(existing!=null&&runCatching{val bytes=vault.decrypt(a.storage,"packet:$room:$id",existing.encrypted);try{HistoryRepository.verifyPacket(bytes,existing.hash,room,id)}finally{bytes.fill(0)}}.isSuccess)return@withContext obj("state" to "verified")
  val k=key(room,id);val payload=buildJsonObject{put("room",room);put("event",id);put("raw",canonical)}.toString().toByteArray()
  try{db.withTransaction{current(a);dao.saveOperation(Operation(k,"live_message","pending",vault.encrypt(a.storage,k,payload),System.currentTimeMillis()))}}finally{payload.fill(0)}
  changed();obj("state" to "saved")
 }
 suspend fun snapshot():List<JsonObject> =withContext(Dispatchers.IO){
  val a=store.account?:return@withContext emptyList();val dao=store.database?.local()?:return@withContext emptyList();val rows=mutableListOf<JsonObject>();val sizes=mutableMapOf<String,Int>();val counts=mutableMapOf<String,Int>()
  for(op in dao.allOperations().filter{it.kind=="live_message"&&it.state=="pending"}.sortedByDescending{it.updated})runCatching{
   val bytes=vault.decrypt(a.storage,op.key,op.encrypted)
   try{val data=json.parseToJsonElement(bytes.decodeToString()).jsonObject;val room=data.s("room");val raw=data.s("raw");val event=json.parseToJsonElement(raw).jsonObject
    if((counts[room]?:0)>=40||(sizes[room]?:0)+raw.length>LocalTimeline.BYTES)return@runCatching
    rows+=buildJsonObject{put("room",room);put("event_id",data.s("event"));put("raw",Base64.encodeToString(raw.toByteArray(),Base64.NO_WRAP));put("sha256",PacketProtocol.hash(raw.toByteArray()));put("received_ms",event["origin_server_ts"]?:JsonPrimitive(op.updated));put("provisional",true)};sizes[room]=(sizes[room]?:0)+raw.length;counts[room]=(counts[room]?:0)+1
   }finally{bytes.fill(0)}
  };current(a);rows
 }
 internal suspend fun acceptRecent(a:Account,records:JsonArray):List<String> {
  val db=requireNotNull(store.database);val dao=db.local();val materials=mutableListOf<String>()
  for(value in records){val r=value.jsonObject.o("receipt");val p=value.jsonObject.o("packet");val room=r.s("room");val id=r.s("event_id");require(p.s("event_id")==id)
   var raw:ByteArray?=null
   try{
    if(p["tombstone"] !is JsonObject){raw=Base64.decode(p.s("raw"),Base64.NO_WRAP);HistoryRepository.verifyPacket(raw,r.s("sha256"),room,id);val event=json.parseToJsonElement(raw.decodeToString()).jsonObject;require(event.s("type")==r.s("type"));if(event.o("content")[PacketProtocol.MATERIAL]!=null)materials.add(id)}
    db.withTransaction {
     current(a);val sequence=r["sequence"]!!.jsonPrimitive.long;dao.receipt(TrustedReceipt(room,id,r.s("sha256"),sequence,vault.encrypt(a.storage,"receipt:$room:$id",r.toString().toByteArray())))
     val target=r.s("redacts");if(target.isNotBlank())dao.tombstone(Tombstone(room,target,sequence))
     if(p["tombstone"] is JsonObject)dao.tombstone(Tombstone(room,id,sequence))
     else {dao.deletePacket(room,id);dao.addPacket(Packet(room,id,sequence,r.s("sha256"),vault.encrypt(a.storage,"packet:$room:$id",requireNotNull(raw))))}
     dao.deleteOperation(key(room,id))
    }
   }finally{raw?.fill(0)}
  }
  changed();return materials
 }
 suspend fun recent(room:String,owner:Account?=null):List<String> =withContext(Dispatchers.IO){val a=requireNotNull(store.account);require(owner==null||owner.token==a.token){"本地账户已改变"};val result=gateway.call("/api/history/recent",buildJsonObject{put("room",room);put("limit",40)}).jsonObject;current(a);acceptRecent(a,result.a("records"))}
 suspend fun drain():List<String> =mutex.withLock {withContext(Dispatchers.IO){
  val a=store.account?:return@withContext emptyList();val dao=store.database?.local()?:return@withContext emptyList();val groups=linkedMapOf<String,MutableList<String>>();val materials=mutableListOf<String>()
  for(op in dao.allOperations().filter{it.kind=="live_message"&&it.state=="pending"}.sortedByDescending{it.updated}.take(200))runCatching{
   val bytes=vault.decrypt(a.storage,op.key,op.encrypted);try{val r=json.parseToJsonElement(bytes.decodeToString()).jsonObject;groups.getOrPut(r.s("room")){mutableListOf()}.add(r.s("event"))}finally{bytes.fill(0)}
  }.onFailure{if(it is kotlinx.coroutines.CancellationException)throw it}
  for((room,ids) in groups)for(batch in ids.chunked(20))runCatching{val result=gateway.call("/api/history/recent",buildJsonObject{put("room",room);put("ids",JsonArray(batch.map(::JsonPrimitive)))}).jsonObject;current(a);materials+=acceptRecent(a,result.a("records"))}.onFailure{if(it is kotlinx.coroutines.CancellationException)throw it}
  materials
 }}
}
