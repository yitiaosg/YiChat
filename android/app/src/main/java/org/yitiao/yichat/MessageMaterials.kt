package org.yitiao.yichat

import android.content.Context
import android.util.AtomicFile
import androidx.room.withTransaction
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*
import java.io.File

/** Independent encrypted documents survive WebView cache eviction and offline startup. */
class ProtocolStore(private val context:Context,private val packets:PacketStore,private val vault:SecureStore) {
 var onDayChanged:((JsonObject)->Unit)?=null
 private fun account()=requireNotNull(packets.account){"本地账户已锁定"}
 private fun directory(a:Account)=File(context.noBackupFilesDir,"message-protocol/${a.storage}").apply{mkdirs()}
 private fun read(a:Account,key:String):JsonElement? {
  val file=AtomicFile(File(directory(a),SecureStore.hash(key)));if(!file.baseFile.exists()&&!File(file.baseFile.path+".bak").exists())return null
  val bytes=vault.decrypt(a.storage,"protocol:$key",file.readFully())
  return try{json.parseToJsonElement(bytes.decodeToString()).also{check(packets.account?.token==a.token){"本地账户已锁定"}}}finally{bytes.fill(0)}
 }
 @Synchronized fun get(key:String):JsonElement?=read(account(),key)
 @Synchronized fun keys():List<String> {val a=account();return (read(a,"index") as? JsonArray)?.map{it.jsonPrimitive.content}.orEmpty()}
 private fun write(a:Account,key:String,value:JsonElement){
  val bytes=value.toString().toByteArray();val file=AtomicFile(File(directory(a),SecureStore.hash(key)));val out=file.startWrite()
  try{out.write(vault.encrypt(a.storage,"protocol:$key",bytes));check(packets.account?.token==a.token){"本地账户已锁定"};file.finishWrite(out)}catch(e:Exception){file.failWrite(out);throw e}finally{bytes.fill(0)}
 }
 @Synchronized fun put(key:String,value:JsonElement,expected:Account?=null){
  val a=account();require(expected==null||expected.token==a.token){"本地账户已改变"};require(key.length<=1024&&key!="index");write(a,key,value)
  if(key.startsWith("day|")&&value is JsonObject)onDayChanged?.invoke(buildJsonObject{put("room_id",value.s("room_id"));put("day",value.s("day"));put("state",value.s("state"))})
  val keys=(read(a,"index") as? JsonArray)?.map{it.jsonPrimitive.content}?.toMutableSet()?:mutableSetOf();if(keys.add(key))write(a,"index",JsonArray(keys.sorted().map(::JsonPrimitive)))
 }
}
class MessageMaterials(private val packets:PacketStore,private val gateway:Gateway,private val cache:ProtocolStore,private val files:RecordFiles) {
 private val mutex=Mutex();private val recordsMutex=Mutex()
 fun read(event:String):JsonObject? {
  require(event.startsWith("$")&&event.length<=255)
  val owner=requireNotNull(packets.account)
  val value=cache.get("material|$event") as? JsonObject?:return null
  require(value.o("meta").s("event_id")==event){"原始消息缓存身份不匹配"}
  val good=PacketProtocol.validateMaterial(value);val m=good.o("meta")
  if(!m.b("finalized")&&System.currentTimeMillis()>=m.s("shared_at").toLong()+300000){
   val finalMeta=JsonObject(m+mapOf("finalized" to JsonPrimitive(true),"sha256" to JsonPrimitive(PacketProtocol.materialHash(m,m.s("payload_sha256"),m.a("files"))),"server_confirmed" to JsonPrimitive(false)))
   val local=JsonObject(good+("meta" to finalMeta));cache.put("material|$event",local,owner);return local
  }
  return good
 }
 fun save(value:JsonObject,expected:Account?=null):JsonObject {
  val good=PacketProtocol.validateMaterial(value);cache.put("material|"+good.o("meta").s("event_id"),good,expected)
  for(row in good.o("meta").a("files"))files.remember(row.jsonObject,expected)
  val doc=json.parseToJsonElement(good.s("canonical")).jsonObject
  if(doc.s("kind")=="record"){val record=doc.o("record");cache.put("public-record|"+record.s("hash"),record,expected)}
  return good
 }
 suspend fun ensure(event:String)=mutex.withLock {withContext(Dispatchers.IO){
  val a=requireNotNull(packets.account);val local=runCatching{read(event)}.getOrNull();val meta=local?.o("meta")
  val query=buildJsonObject{put("messages",buildJsonArray{add(buildJsonObject{put("event_id",event);put("payload_sha256",meta?.get("payload_sha256")?:JsonNull);put("sha256",meta?.get("sha256")?:JsonNull)})})}
  var response:JsonObject?=null
  for(attempt in 0..3)try{response=gateway.call("/api/materials/check",query).jsonObject.a("messages").first().jsonObject;break}catch(e:GatewayError){if(e.status!=503||attempt==3)throw e;kotlinx.coroutines.delay(500L*(attempt+1))}
  val state=requireNotNull(response)
  if(state.s("state")=="expired"){
   val existing=requireNotNull(local){"服务器已清理原始消息，请通过完整日期数据包恢复"};return@withContext save(JsonObject(existing+("meta" to JsonObject(existing.o("meta")+("single_expired" to JsonPrimitive(true))))),a)
  }
  val canonical=if(state.s("state")=="ok"&&local!=null)local.s("canonical") else gateway.document(state.s("download"))
  val value=buildJsonObject{put("meta",state);put("canonical",canonical)};PacketProtocol.validateMaterial(value)
  val confirmed=gateway.call("/api/materials/"+Gateway.segment(event)+"/confirm",buildJsonObject{put("payload_sha256",state.s("payload_sha256"));put("sha256",state["sha256"]?:JsonNull)}).jsonObject
  require(confirmed.s("state")=="ok"){"服务器未通过客户端重新计算的原始消息摘要"}
  val doc=json.parseToJsonElement(canonical).jsonObject;val photos=FileDownloadPlan.ids(doc,true)
  for(file in state.a("files")){val f=file.jsonObject;files.remember(f,a);if(f.s("id") in photos)runCatching{files.ensure(f.s("id"),f.s("sha256"))}}
  check(packets.account?.token==a.token){"本地账户已锁定"}
  save(buildJsonObject{put("meta",confirmed);put("canonical",canonical)},a)
 }}
 suspend fun saveRecord(value:JsonObject):JsonObject {
  val a=requireNotNull(packets.account);val h=value.s("hash");val raw=value.s("canonical");require(h.matches(Regex("[a-f0-9]{64}"))&&PacketProtocol.hash(raw.toByteArray())==h){"记录原始哈希不匹配"}
  val payload=json.parseToJsonElement(raw).jsonObject;val good=JsonObject(value+("payload" to payload));cache.put("public-record|$h",good,a)
  val photos=FileDownloadPlan.ids(payload,true)
  for(v in value.a("files")){val f=v.jsonObject;files.remember(f,a);if(f.s("id") in photos)runCatching{files.ensure(f.s("id"),f.s("sha256"),h)}}
  return good
 }
 suspend fun exchangeRecords()=recordsMutex.withLock {
  val a=packets.account?:return@withLock;val reply=gateway.call("/api/records/exchange",obj()).jsonObject
  var offeredBytes=0L
  val offers=buildJsonArray {for(v in reply.a("asked")) {
   val ask=v.jsonObject;val h=ask.s("hash");var record=(cache.get("public-record|$h") as? JsonObject)?:files.previews.read("","record:$h") as? JsonObject
   if(record==null)runCatching {
    val recipe=ask.o("recipe");val messages=buildJsonArray {for(m in recipe.a("messages")){val row=m.jsonObject;val packet=requireNotNull(packets.database?.local()?.packet(ask.s("room"),row.s("id")));val bytes=files.decryptPacket(a,packet)
     try {val event=json.parseToJsonElement(bytes.decodeToString()).jsonObject;var content=event.o("content");var previews=JsonArray(emptyList())
      if(content[PacketProtocol.MATERIAL]!=null){val material=requireNotNull(read(row.s("id")));val doc=json.parseToJsonElement(material.s("canonical")).jsonObject;content=doc.o("content");previews=doc.a("previews")}
      else if(content["org.yitiao.forward_snapshot"] is JsonObject){val frozen=content.o("org.yitiao.forward_snapshot");content=frozen.o("content");previews=frozen.a("previews")}
      add(JsonObject(row+mapOf("content" to content,"previews" to previews)))
     }finally{bytes.fill(0)}
    }}
    val raw=PacketProtocol.canonical(JsonObject(recipe.o("header")+("messages" to messages)));require(PacketProtocol.hash(raw.toByteArray())==h);record=obj("hash" to h,"canonical" to raw);cache.put("public-record|$h",requireNotNull(record),a)
   }
   val raw=record?.s("canonical")?:continue;if(PacketProtocol.hash(raw.toByteArray())!=h)continue
   offeredBytes+=raw.toByteArray().size*2L+1024
   val bodies=buildJsonArray{for(id in ask.a("files").map{it.jsonPrimitive.content}){val data=runCatching{files.bytes(id)}.getOrNull()?:continue;val encodedBytes=((data.size+2L)/3L)*4+1024;if(offeredBytes+encodedBytes>128L*1024*1024-1024)continue;offeredBytes+=encodedBytes;add(obj("id" to id,"data" to java.util.Base64.getEncoder().encodeToString(data)))}}
   add(buildJsonObject{put("hash",h);put("canonical",raw);put("files",bodies)})
  }}
  if(offers.isNotEmpty())gateway.call("/api/records/exchange",buildJsonObject{put("offers",offers)})
  var attempted=0
  for(key in cache.keys().filter{it.startsWith("public-record|")}) {
   val record=cache.get(key) as? JsonObject?:continue;val raw=record.s("canonical");if(raw.isBlank())continue
   val photos=FileDownloadPlan.ids(json.parseToJsonElement(raw),true)
   for(v in record.a("files")){val f=v.jsonObject;if(f.s("id") !in photos||files.available(f.s("id")))continue;if(attempted++>=7)return@withLock;runCatching{files.ensure(f.s("id"),f.s("sha256"),key.removePrefix("public-record|"))}}
  }
 }
 suspend fun removeLegacy() {
  val a=packets.account?:return;val db=packets.database?:return;val dao=db.local();var after=0L
  do {
   val page=gateway.call("/api/materials/removed?after=$after").jsonObject
   db.withTransaction {for(value in page.a("records")){val r=value.jsonObject;check(packets.account?.token==a.token);dao.deletePacket(r.s("room"),r.s("event_id"));dao.deleteReceipt(r.s("room"),r.s("event_id"));dao.tombstone(Tombstone(r.s("room"),r.s("event_id"),r["sequence"]!!.jsonPrimitive.long))}}
   after=page["next_after"]?.jsonPrimitive?.longOrNull?:0L
  }while(after!=0L)
 }
 suspend fun syncNew() {
  val owner=packets.account?:return
  removeLegacy()
  val dao=packets.database?.local()?:return;val current=cache.get("material-cursor")?.jsonPrimitive?.longOrNull?:0L;var cursor=current;val pending=(cache.get("material-pending") as? JsonArray)?.map{it.jsonPrimitive.content}?.toMutableSet()?:mutableSetOf()
  for(receipt in dao.receipts().sortedBy{it.sequence}) {
   if(receipt.sequence<=current)continue
   val packet=dao.packet(receipt.room,receipt.event)?:continue
   val a=packets.account?:return
   val bytes=runCatching{files.decryptPacket(a,packet)}.getOrNull()
   if(bytes==null){cursor=maxOf(cursor,receipt.sequence);continue}
   try{val event=json.parseToJsonElement(bytes.decodeToString()).jsonObject;if(event.o("content")[PacketProtocol.MATERIAL]!=null){try{ensure(receipt.event);pending.remove(receipt.event)}catch(e:Exception){if(e is kotlinx.coroutines.CancellationException)throw e;pending.add(receipt.event)}};cursor=maxOf(cursor,receipt.sequence)}catch(e:Exception){if(e is kotlinx.coroutines.CancellationException)throw e;cursor=maxOf(cursor,receipt.sequence)}finally{bytes.fill(0)}
  }
  cache.put("material-cursor",JsonPrimitive(cursor),owner)
  for(event in pending.toList())try{ensure(event);pending.remove(event)}catch(e:Exception){if(e is kotlinx.coroutines.CancellationException)throw e}
  cache.put("material-pending",JsonArray(pending.map(::JsonPrimitive)),owner)
  // Previously mutable hashes become final after the recall window; avoid
  // downloading payloads that already match the server's original bytes.
  for(key in cache.keys().filter{it.startsWith("material|")}) {
   val value=runCatching{cache.get(key)?.jsonObject}.getOrNull()?:continue;val m=value.o("meta")
   runCatching{val doc=json.parseToJsonElement(value.s("canonical"));val photos=FileDownloadPlan.ids(doc,true);for(f in m.a("files").map{it.jsonObject}.filter{it.s("id") in photos&&!files.available(it.s("id"))})files.ensure(f.s("id"),f.s("sha256"))}
   if(!m.b("single_expired")&&(!m.b("finalized")||m["server_confirmed"]==JsonPrimitive(false))&&System.currentTimeMillis()>=m.s("shared_at").toLong()+300000)runCatching{ensure(m.s("event_id"))}
  }
 }
}
