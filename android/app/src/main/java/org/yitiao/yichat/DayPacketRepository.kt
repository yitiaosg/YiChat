package org.yitiao.yichat

import androidx.room.withTransaction
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*
import java.time.LocalDate
import java.util.Base64
import java.util.UUID

/** Full-day trust is independent of the display cache and includes empty days. */
class DayPacketRepository(private val store:PacketStore,private val gateway:Gateway,private val vault:SecureStore,private val cache:ProtocolStore,private val materials:MessageMaterials,private val files:RecordFiles) {
 private val mutex=Mutex();private val imports=mutableMapOf<String,JsonObject>();private val importPlans=mutableMapOf<String,JsonObject>();private val importOwners=mutableMapOf<String,String>()
 fun clearImports(){imports.clear();importPlans.clear();importOwners.clear()}
 private fun dayKey(room:String,day:String)="day|$room|$day"
 private fun proof(room:String,day:String)=cache.get("proof|$room|$day") as? JsonObject
 private fun checkedAccount(a:Account){check(store.account?.token==a.token){"本地账户已锁定"}}
 private fun dates(start:String,end:String,max:Int=3650):List<String> {val a=LocalDate.parse(start);val b=LocalDate.parse(end);val count=java.time.temporal.ChronoUnit.DAYS.between(a,b)+1;require(count in 1..max.toLong()&&a.toString()==start&&b.toString()==end){"请选择连续1至${max}个 GMT 日期"};return (0 until count.toInt()).map{a.plusDays(it.toLong()).toString()}}
 suspend fun collect(room:String,day:String,includeData:Boolean=true):JsonObject=withContext(Dispatchers.IO) {
  val selected=cache.get(dayKey(room,day)) as? JsonObject
  if(selected?.b("manual")==true)return@withContext selected
  val a=requireNotNull(store.account);val dao=requireNotNull(store.database).local();val low=PacketProtocol.start(day);val entries=mutableListOf<JsonElement>();var missing=false
  for(receipt in dao.receipts().filter{it.room==room}.sortedBy{it.sequence}) {
   val metadata=runCatching{json.parseToJsonElement(vault.decrypt(a.storage,"receipt:$room:${receipt.event}",receipt.encrypted).decodeToString()).jsonObject}.getOrNull()
   if(metadata==null){missing=true;continue};val at=metadata["received_ms"]?.jsonPrimitive?.longOrNull?:continue;if(at<low||at>=low+86400000)continue
   val packet=dao.packet(room,receipt.event);if(packet==null){missing=true;continue}
   var bytes:ByteArray?=null
   try {
    bytes=files.decryptPacket(a,packet);HistoryRepository.verifyPacket(bytes,receipt.hash,room,receipt.event);val raw=bytes.decodeToString();val event=json.parseToJsonElement(raw).jsonObject
    val content=event.o("content");val material=if(content[PacketProtocol.MATERIAL]!=null)materials.read(receipt.event) else null
    if(content[PacketProtocol.MATERIAL]!=null&&material==null){missing=true;continue}
    val legacyIds=content.a("org.yitiao.attachments").map{it.jsonPrimitive.content}.toMutableSet()
    Regex("mxc://chat\\.yitiao\\.org/([a-f0-9]{48})").matchEntire(content.s("url"))?.groupValues?.get(1)?.let{legacyIds.add(it)}
    val fileRows=if(material!=null)material.o("meta").a("files") else JsonArray(legacyIds.sorted().mapNotNull{id->runCatching{val data=files.bytes(id);buildJsonObject{put("id",id);put("bytes",data.size.toString());put("sha256",PacketProtocol.hash(data))}}.getOrElse{missing=true;null}})
    val projectedFiles=buildJsonArray {for(row in fileRows){val f=row.jsonObject;val value=runCatching{files.bytes(f.s("id"))}.getOrNull();if(value==null||PacketProtocol.hash(value)!=f.s("sha256")){missing=true;add(f);continue};add(if(includeData)JsonObject(f+("data" to JsonPrimitive(Base64.getEncoder().encodeToString(value)))) else f)}}
    entries+=buildJsonObject {
     put("event_id",receipt.event);put("room_id",room);put("sender",event.s("sender"));put("type",event.s("type"));put("sequence",receipt.sequence.toString());put("received_ms",at.toString());put("raw",raw);put("files",projectedFiles);put("redacted",dao.tombstone(room,receipt.event)!=null)
     if(material!=null){val meta=material.o("meta");put("material",JsonObject(meta+("canonical" to JsonPrimitive(material.s("canonical")))));if(!meta.b("finalized"))missing=true}
    }
   }catch(_:Exception){missing=true}finally{bytes?.fill(0)}
  }
  val expected=proof(room,day);val old=cache.get(dayKey(room,day)) as? JsonObject
  val covered=dao.watermark("*")?.untilMs?.let{it>=PacketProtocol.immutable(day)}==true || (old?.b("complete")==true)
  val complete=!missing&&covered&&(expected==null||expected["count"]?.jsonPrimitive?.intOrNull==entries.size)
  checkedAccount(a);val value=JsonObject(PacketProtocol.header(room,day)+mapOf("entries" to JsonArray(entries),"complete" to JsonPrimitive(complete),"covered" to JsonPrimitive(covered),"count" to JsonPrimitive(entries.size)))
  val actual=runCatching{PacketProtocol.dayHash(value,includeData)}.getOrNull()
  JsonObject(value+mapOf("sha256" to (actual?.let(::JsonPrimitive)?:JsonNull),"expected_sha256" to (expected?.get("sha256")?:JsonNull),"state" to JsonPrimitive(old?.s("state")?:"unverified")))
 }
 private suspend fun validated(packet:JsonObject,download:Boolean):JsonObject {
  val entries=buildJsonArray{for(v in packet.a("entries")){
   val e=v.jsonObject;val f=buildJsonArray {for(value in e.a("files")){val row=value.jsonObject;val bytes=if(row["data"] is JsonPrimitive)Base64.getDecoder().decode(row.s("data")) else {if(download)files.ensure(row.s("id"),row.s("sha256"));files.bytes(row.s("id"))};require(PacketProtocol.hash(bytes)==row.s("sha256")&&bytes.size.toLong()==row.s("bytes").toLong()){ "附件校验失败" };add(JsonObject(row+("data" to JsonPrimitive(Base64.getEncoder().encodeToString(bytes)))))}}
   add(JsonObject(e+("files" to f)))
  }}
  val complete=JsonObject(packet+mapOf("entries" to entries,"complete" to JsonPrimitive(true)));val actual=PacketProtocol.dayHash(complete)
  require(actual==packet.s("sha256")){"完整日期数据与服务器摘要不匹配"};return complete
 }
 suspend fun install(packet:JsonObject,verified:Boolean,download:Boolean=false) {
  val a=requireNotNull(store.account);val db=requireNotNull(store.database);val dao=db.local();val room=packet.s("room_id");val day=packet.s("day");val low=PacketProtocol.start(day)
  val ready=if(verified)validated(packet,download) else packet;checkedAccount(a)
  if(verified)for(value in ready.a("entries")) {
   val e=value.jsonObject
   for(v in e.a("files")){val f=v.jsonObject;if(f["data"] is JsonPrimitive)files.save(f.s("id"),Base64.getDecoder().decode(f.s("data")),f.s("sha256"),a)}
   if(e["material"] is JsonObject){val m=e.o("material");materials.save(buildJsonObject{put("meta",JsonObject(m-"canonical"));put("canonical",m.s("canonical"))},a)}
  }
  db.withTransaction {
   for(r in dao.receipts().filter{it.room==room}){
    val m=runCatching{json.parseToJsonElement(vault.decrypt(a.storage,"receipt:$room:${r.event}",r.encrypted).decodeToString()).jsonObject}.getOrNull();val at=m?.get("received_ms")?.jsonPrimitive?.longOrNull
    if(at!=null&&at>=low&&at<low+86400000){dao.deletePacket(room,r.event);dao.deleteReceipt(room,r.event);dao.deleteTombstone(room,r.event)}
   }
   for(value in ready.a("entries")){
    val e=value.jsonObject;val eid=e.s("event_id");val raw=e.s("raw").toByteArray();val event=json.parseToJsonElement(e.s("raw")).jsonObject;val sequence=e.s("sequence").toLong();val sha=PacketProtocol.hash(raw);val target=event.o("content").s("redacts").ifBlank{event.s("redacts")}
    val receipt=buildJsonObject{put("event_id",eid);put("room",room);put("sender",event.s("sender"));put("type",event.s("type"));put("sequence",sequence);put("received_ms",e.s("received_ms").toLong());put("origin_server_ts",event["origin_server_ts"]?:JsonPrimitive(e.s("received_ms").toLong()));put("sha256",sha);put("redacts",target.takeIf{it.isNotBlank()}?.let(::JsonPrimitive)?:JsonNull);put("business",event.o("content")["org.yitiao.business"]?:JsonNull)}
    dao.receipt(TrustedReceipt(room,eid,sha,sequence,vault.encrypt(a.storage,"receipt:$room:$eid",receipt.toString().toByteArray())))
    dao.addPacket(Packet(room,eid,sequence,sha,vault.encrypt(a.storage,"packet:$room:$eid",raw)));raw.fill(0)
    if(e.b("redacted"))dao.tombstone(Tombstone(room,eid,sequence))
    if(event.s("type")=="m.room.redaction"&&target.isNotBlank())dao.tombstone(Tombstone(room,target,sequence))
   }
  }
  checkedAccount(a)
  val stripped=if(!verified)JsonObject(ready+("manual" to JsonPrimitive(true))) else JsonObject(ready+("entries" to JsonArray(ready.a("entries").map{v->val e=v.jsonObject;JsonObject(e+("files" to JsonArray(e.a("files").map{JsonObject(it.jsonObject-"data")})))})))
  cache.put(dayKey(room,day),JsonObject(stripped+("state" to JsonPrimitive(if(verified)"ok" else "untrusted"))),a)
  if(verified)cache.put("proof|$room|$day",buildJsonObject{put("room",room);put("day",day);put("sha256",ready.s("sha256"));put("count",ready.a("entries").size);put("sealed_ms",PacketProtocol.immutable(day))},a)
 }
 suspend fun checkDay(room:String,day:String):JsonObject=mutex.withLock {withContext(Dispatchers.IO) {
  val owner=requireNotNull(store.account)
  require(System.currentTimeMillis()>=PacketProtocol.immutable(day)){"此 GMT 日期尚未在00:05封存"}
  val response=gateway.call("/api/packets/days?room="+Gateway.segment(room)+"&start=$day&end=$day").jsonObject.a("days").single().jsonObject
  checkedAccount(owner)
  if(response.s("state")!="sealed")return@withContext response
  cache.put("proof|$room|$day",response,owner);var local=collect(room,day,true);val actual=runCatching{PacketProtocol.dayHash(local)}.getOrNull()
  if(local.b("complete")&&actual==response.s("sha256")) {
   val good=JsonObject(local+mapOf("state" to JsonPrimitive("ok"),"sha256" to JsonPrimitive(actual)));cache.put(dayKey(room,day),JsonObject(good+("entries" to JsonArray(good.a("entries").map{v->val e=v.jsonObject;JsonObject(e+("files" to JsonArray(e.a("files").map{JsonObject(it.jsonObject-"data")})))}))))
   return@withContext obj("room" to room,"day" to day,"state" to "ok")
  }
  cache.put(dayKey(room,day),JsonObject(local+("state" to JsonPrimitive(if(local.b("covered"))"mismatch" else "incomplete"))),owner)
  val repair=gateway.call("/api/packets/day?room="+Gateway.segment(room)+"&day=$day").jsonObject
  if(repair.s("state")=="available") {
   checkedAccount(owner);val incoming=repair.o("packet");install(incoming,true,true);local=collect(room,day,true);val fixed=PacketProtocol.dayHash(local)
   require(fixed==response.s("sha256")){"数据覆盖后重新校验失败"}
   val checked=gateway.call("/api/packets/check",buildJsonObject{put("room",room);put("start",day);put("end",day);put("days",buildJsonArray{add(buildJsonObject{put("day",day);put("complete",true);put("sha256",fixed);put("count",local.a("entries").size)})})}).jsonObject.a("days").single().jsonObject
   require(checked.s("state")=="ok"){"服务器未通过修复后的日期摘要"};return@withContext obj("room" to room,"day" to day,"state" to "repaired")
  }
  cache.put("peer|$room|$day",JsonObject(repair+("requested_ms" to JsonPrimitive(System.currentTimeMillis()))),owner);obj("room" to room,"day" to day,"state" to "peer_required")
 }}
 suspend fun exchange() {
  val owner=requireNotNull(store.account)
  val wants=mutableListOf<String>()
  for(key in cache.keys().filter{it.startsWith("peer|")}) {
   if(wants.size>=7)break
   var pending=cache.get(key)?.jsonObject?:continue;if(pending.s("request").isBlank())continue
   if(System.currentTimeMillis()-(pending["requested_ms"]?.jsonPrimitive?.longOrNull?:0)>150000){
    val parts=key.split('|');val room=parts[1];val day=parts[2]
    val fresh=gateway.call("/api/packets/day?room="+Gateway.segment(room)+"&day=$day").jsonObject;checkedAccount(owner)
    if(fresh.s("state")=="available"){install(fresh.o("packet"),true,true);require(serverValid(collect(room,day,true)));cache.put(key,JsonObject(emptyMap()),owner);continue}
    pending=JsonObject(fresh+("requested_ms" to JsonPrimitive(System.currentTimeMillis())));cache.put(key,pending,owner)
   }
   pending.s("request").takeIf{it.isNotBlank()}?.let(wants::add)
  }
  val reply=gateway.call("/api/packets/exchange",buildJsonObject{put("requests",JsonArray(wants.map(::JsonPrimitive)))}).jsonObject
  checkedAccount(owner)
  for(value in reply.a("fulfilled")){val packet=value.jsonObject.o("packet");install(packet,true);val local=collect(packet.s("room_id"),packet.s("day"),true);require(serverValid(local)){"服务器未通过对端修复后的整日数据"};cache.put("peer|${packet.s("room_id")}|${packet.s("day")}",JsonObject(emptyMap()),owner)}
  val offers=buildJsonArray{for(value in reply.a("asked")){
   val ask=value.jsonObject;val packet=collect(ask.s("room"),ask.s("day"),true);val sha=runCatching{PacketProtocol.dayHash(packet)}.getOrNull()
   if(packet.b("complete")&&sha==ask.s("sha256"))add(buildJsonObject{put("request",ask.s("request"));put("packet",JsonObject(packet+("sha256" to JsonPrimitive(sha))))})
  }}
  if(offers.isNotEmpty())gateway.call("/api/packets/exchange",buildJsonObject{put("offers",offers)})
 }
 suspend fun exportKnown():JsonObject=withContext(Dispatchers.IO) {
  val a=requireNotNull(store.account);val dao=requireNotNull(store.database).local();val keys=linkedSetOf<Pair<String,String>>()
  for(key in cache.keys().filter{it.startsWith("day|")}){val row=cache.get(key)?.jsonObject?:continue;keys.add(row.s("room_id") to row.s("day"))}
  for(receipt in dao.receipts())runCatching {val raw=vault.decrypt(a.storage,"receipt:${receipt.room}:${receipt.event}",receipt.encrypted);try{val m=json.parseToJsonElement(raw.decodeToString()).jsonObject;keys.add(receipt.room to PacketProtocol.day(m["received_ms"]!!.jsonPrimitive.long))}finally{raw.fill(0)}}
  require(keys.size<=10000){"日期超过10000项，请按会话分批导出"}
  val result=mutableListOf<JsonObject>();for((room,day) in keys.sortedBy{it.first+it.second})if(System.currentTimeMillis()>=PacketProtocol.immutable(day))result.add(collect(room,day,true))
  checkedAccount(a);PacketProtocol.pack(result)
 }
 suspend fun export(rooms:List<String>,start:String,end:String):JsonObject=withContext(Dispatchers.IO) {
  require(rooms.isNotEmpty()&&rooms.size<=100);val dates=dates(start,end);val days=mutableListOf<JsonObject>()
  for(room in rooms)for(day in dates){require(System.currentTimeMillis()>=PacketProtocol.immutable(day)){"只能导出已经不可撤回的 GMT 日期"};days+=collect(room,day,true)}
  PacketProtocol.pack(days)
 }
 private suspend fun serverValid(packet:JsonObject):Boolean {
  val room=packet.s("room_id");val day=packet.s("day")
  val response=gateway.call("/api/packets/verify",buildJsonObject{put("room",room);put("start",day);put("end",day);put("days",JsonArray(listOf(packet)))}).jsonObject.a("days").single().jsonObject
  return response.s("state")=="ok"
 }
 suspend fun inspect(bundle:JsonObject):JsonObject=withContext(Dispatchers.IO) {
  val a=requireNotNull(store.account);val key=UUID.randomUUID().toString();val rows=buildJsonArray {
   for(value in bundle.a("days")){
    val source=value.jsonObject;val room=source.s("room_id");val day=source.s("day");require(System.currentTimeMillis()>=PacketProtocol.immutable(day)){"导入包含尚可撤回的日期"};val incoming=PacketProtocol.hydrate(bundle,source);val local=collect(room,day,true)
    val left=runCatching{PacketProtocol.dayHash(local)}.getOrNull();val right=runCatching{PacketProtocol.dayHash(incoming)}.getOrNull()
    var state="same";var reason=""
    if(left==null||right==null||left!=right||!local.b("complete")||!incoming.b("complete")) {
     try {
      val incomingOk=serverValid(incoming);checkedAccount(a);val localOk=serverValid(local);checkedAccount(a)
      state=when{incomingOk->{install(JsonObject(incoming+("sha256" to JsonPrimitive(requireNotNull(right)))),true);"imported"};localOk->"kept_local";else->"conflict"}
     }catch(e:Exception){state="pending";reason=if(e is GatewayError)e.reason else "需要联网完成服务器校验"}
    }
    add(buildJsonObject{put("room_id",room);put("day",day);put("state",state);put("local_sha256",left?.let(::JsonPrimitive)?:JsonNull);put("incoming_sha256",right?.let(::JsonPrimitive)?:JsonNull);put("error",reason)})
   }
  };checkedAccount(a);val plan=buildJsonObject{put("import",key);put("days",rows)};imports[key]=bundle;importPlans[key]=plan;importOwners[key]=a.storage;plan
 }
 suspend fun resolve(key:String,room:String,day:String,choice:String,confirmed:Boolean):JsonObject=withContext(Dispatchers.IO) {
  require(confirmed&&choice in listOf("local","incoming")){"需要二次确认保留哪一方的当日记录"}
  require(importOwners[key]==store.account?.storage){"导入任务属于其他本地账户"}
  val plan=requireNotNull(importPlans[key]);require(plan.a("days").any{it.jsonObject.s("room_id")==room&&it.jsonObject.s("day")==day&&it.jsonObject.s("state")=="conflict"}){"只有双方都不匹配的日期可人工覆盖"}
  val bundle=requireNotNull(imports[key]);val selected=if(choice=="local")collect(room,day,true) else PacketProtocol.hydrate(bundle,bundle.a("days").first{it.jsonObject.s("room_id")==room&&it.jsonObject.s("day")==day}.jsonObject)
  install(selected,false);obj("room" to room,"day" to day,"state" to "untrusted","choice" to choice)
 }
 suspend fun localPreview(room:String,day:String)=collect(room,day,true)
 fun displayEntries(ids:Set<String>):JsonArray=buildJsonArray {
  for(key in cache.keys().filter{it.startsWith("day|")}) {
   val day=cache.get(key) as? JsonObject?:continue;if(!day.b("manual"))continue
   for(value in day.a("entries")) {val e=value.jsonObject;if(e.s("event_id") !in ids)continue
    val display=JsonArray(e.a("files").map{v->val f=v.jsonObject;runCatching{val bytes=Base64.getDecoder().decode(f.s("data"));JsonObject(f+mapOf("bytes" to JsonPrimitive(bytes.size.toString()),"sha256" to JsonPrimitive(PacketProtocol.hash(bytes))))}.getOrDefault(f)})
    add(JsonObject(e+("files" to display)))
   }
  }
 }
 fun states():JsonArray=JsonArray(cache.keys().filter{it.startsWith("day|")}.mapNotNull{key->(cache.get(key) as? JsonObject)?.let{obj("room_id" to it.s("room_id"),"day" to it.s("day"),"state" to it.s("state"))}})
}
