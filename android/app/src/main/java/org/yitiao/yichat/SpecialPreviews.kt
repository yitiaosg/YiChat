package org.yitiao.yichat

import android.content.Context
import android.util.AtomicFile
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*
import java.io.File
import java.net.URLEncoder

/** Preview metadata is backed up proactively, even for messages never opened. */
data class PreviewRequest(val room:String,val resource:String,val path:String,val body:JsonObject?=null)
object SpecialPreviewPlan {
 fun requests(room:String,content:JsonObject):List<PreviewRequest> {
  val share=content.o("org.yitiao.share");val business=content.o("org.yitiao.business")
  val text=content.s("body")+" "+share.s("url")
  val result=mutableListOf<PreviewRequest>()
  val gift=Regex("https://chat\\.yitiao\\.org/gifts/([a-f0-9]{32})(?![a-zA-Z0-9])").find(text)?.groupValues?.get(1)
   ?: (if(business.s("kind")=="gift")business.s("id") else if(share.s("type")=="gift")share.s("id") else "").takeIf{it.matches(Regex("[a-f0-9]{32}"))}
  if(gift!=null)result+=PreviewRequest(room,"gift:$gift","/api/cards/gifts/$gift?room="+URLEncoder.encode(room,"UTF-8"))
  val urls=Regex("https://chat\\.yitiao\\.org/(orders/[a-f0-9]{32}|records/[a-f0-9]{64})(?![a-zA-Z0-9])").findAll(text).map{it.value}.toSet()
  for(url in urls) {
   val id=url.substringAfterLast('/')
   if(url.contains("/records/"))result+=PreviewRequest("","record:$id","/api/public-records/$id")
   else result+=PreviewRequest(room,url,"/api/preview",obj("room" to room,"url" to url))
  }
  val hash=content.s("org.yitiao.record")
  if(hash.matches(Regex("[a-f0-9]{64}"))&&result.none{it.resource=="record:$hash"})result+=PreviewRequest("","record:$hash","/api/public-records/$hash")
  return result.distinctBy{it.room to it.resource}
 }
}
class SpecialPreviews(private val context:Context,private val store:PacketStore,private val gateway:Gateway,private val vault:SecureStore) {
 private fun directory(a:Account)=File(context.noBackupFilesDir,"special-previews/${a.storage}")
 private fun key(room:String,resource:String)=SecureStore.hash("$room\u0000$resource")
 private fun check(a:Account){check(store.account?.token==a.token){"备份账号已锁定"}}
 private fun save(a:Account,room:String,resource:String,value:JsonElement) {
  check(a);val id=key(room,resource);val dir=directory(a).apply{mkdirs()}
  val bytes=buildJsonObject{put("room",room);put("resource",resource);put("saved_at",System.currentTimeMillis());put("value",value)}.toString().toByteArray()
  try { val file=AtomicFile(File(dir,id));val out=file.startWrite()
   try{out.write(vault.encrypt(a.storage,"special-preview:$id",bytes));check(a);file.finishWrite(out)}catch(e:Exception){file.failWrite(out);throw e}
  }finally{bytes.fill(0)}
 }
 private fun row(a:Account,id:String):JsonObject? {
  check(a);val file=AtomicFile(File(directory(a),id));if(!file.baseFile.exists()&&!File(file.baseFile.path+".bak").exists())return null
  val bytes=vault.decrypt(a.storage,"special-preview:$id",file.readFully())
  return try{json.parseToJsonElement(bytes.decodeToString()).jsonObject.also{check(a)}}finally{bytes.fill(0)}
 }
 fun read(room:String,resource:String):JsonElement? {
  val a=store.account?:return null;require(room.length<=255&&resource.length<=512)
  return row(a,key(room,resource))?.get("value")
 }
 suspend fun backup()=withContext(Dispatchers.IO) {
  val a=store.account?:return@withContext;val dao=store.database?.local()?:return@withContext
  val plans=linkedMapOf<Pair<String,String>,PreviewRequest>()
  for(packet in dao.packets()) {
   if(dao.tombstone(packet.room,packet.event)!=null)continue
   check(a);val bytes=vault.decrypt(a.storage,"packet:${packet.room}:${packet.event}",packet.encrypted)
   try {
    val content=json.parseToJsonElement(bytes.decodeToString()).jsonObject.o("content")
    for(plan in SpecialPreviewPlan.requests(packet.room,content))plans[plan.room to plan.resource]=plan
    val raw=content.s("org.yitiao.record_json");val hash=content.s("org.yitiao.record")
    if(raw.isNotBlank()) {
     require(SecureStore.hash(raw)==hash){"合并记录摘要不一致"}
     save(a,"","record:$hash",buildJsonObject{put("hash",hash);put("canonical",raw)})
     plans.remove("" to "record:$hash")
    }
   }finally{bytes.fill(0)}
  }
  for(plan in plans.values) {
   check(a)
   try {
    val value=gateway.call(plan.path,plan.body)
    if(plan.resource.startsWith("record:"))require(SecureStore.hash(value.jsonObject.s("canonical"))==plan.resource.removePrefix("record:")){"聊天记录摘要不一致"}
    save(a,plan.room,plan.resource,value)
    if(value.jsonObject.s("type")=="order") {
     val order=value.jsonObject.o("data");save(a,plan.room,"order:"+order.s("id"),order)
    }
   }catch(e:GatewayError){
    if(e.status !in listOf(403,404,410))throw e
    // Historical backups survive server expiry; live 4xx never use this fallback.
   }
  }
 }
 fun export(a:Account):JsonArray {
  check(a);return buildJsonArray{for(file in directory(a).listFiles().orEmpty())if(file.name.matches(Regex("[a-f0-9]{64}")))row(a,file.name)?.let{add(it)}}
 }
 suspend fun restore(a:Account,rows:JsonArray) {
  check(a);val dao=store.database?.local()?:return;val allowed=mutableSetOf<Pair<String,String>>()
  for(packet in dao.packets()) {
   val bytes=vault.decrypt(a.storage,"packet:${packet.room}:${packet.event}",packet.encrypted)
   try{for(plan in SpecialPreviewPlan.requests(packet.room,json.parseToJsonElement(bytes.decodeToString()).jsonObject.o("content"))) {
    allowed+=plan.room to plan.resource
    if(plan.resource.contains("/orders/"))allowed+=plan.room to ("order:"+plan.resource.substringAfterLast('/'))
   }}finally{bytes.fill(0)}
  }
  for(item in rows) {
   val row=item.jsonObject;val room=row.s("room");val resource=row.s("resource");val value=row["value"]?:continue
   if((room to resource) !in allowed)continue
   if(resource.startsWith("record:"))require(SecureStore.hash(value.jsonObject.s("canonical"))==resource.removePrefix("record:"))
   save(a,room,resource,value)
  }
 }
}
