package org.yitiao.yichat

import android.content.Context
import android.util.AtomicFile
import android.webkit.WebResourceResponse
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.*
import java.io.File
import java.io.InputStream
import java.io.SequenceInputStream
import java.security.MessageDigest
import java.util.Collections

/** Per-account encrypted chunks for media referenced inside merged records. */
class RecordFiles(private val context:Context,private val store:PacketStore,private val gateway:Gateway,private val vault:SecureStore) {
 private val receiving=Mutex()
 val previews=SpecialPreviews(context,store,gateway,vault)
 private fun valid(id:String)=id.matches(Regex("[a-f0-9]{48}"))
 private fun folder(a:Account,id:String)=File(context.noBackupFilesDir,"record-files/${a.storage}/$id")
 fun available(id:String):Boolean { val a=store.account?:return false;return valid(id)&&File(folder(a,id),"manifest").isFile }
 suspend fun backupMerged()=withContext(Dispatchers.IO) {
  previews.backup()
  val a=store.account?:return@withContext;val db=store.database?:return@withContext;val ids=mutableSetOf<String>();val photos=mutableSetOf<String>();val capabilities=mutableMapOf<String,String>()
  for(packet in db.local().packets()) {
   val bytes=runCatching{decryptPacket(a,packet)}.getOrNull()?:continue
   try {
    val content=json.parseToJsonElement(bytes.decodeToString()).jsonObject.o("content")
    ids.addAll(FileDownloadPlan.ids(content));photos.addAll(FileDownloadPlan.ids(content,true))
    for(plan in SpecialPreviewPlan.requests(packet.room,content).filter{it.resource.startsWith("record:")}) {
     val saved=previews.read("",plan.resource)?.jsonObject;val raw=saved?.s("canonical").orEmpty();if(raw.isBlank())continue
     require(PacketProtocol.hash(raw.toByteArray())==plan.resource.removePrefix("record:"));val record=json.parseToJsonElement(raw)
     ids.addAll(FileDownloadPlan.ids(record));photos.addAll(FileDownloadPlan.ids(record,true));for(f in saved?.a("files").orEmpty()){remember(f.jsonObject,a);capabilities[f.jsonObject.s("id")]=plan.resource.removePrefix("record:")}
    }
   } finally {bytes.fill(0)}
  }
  for(id in ids)runCatching {
   check(store.account?.token==a.token);val meta=metadata(id)?:gateway.call("/api/files/$id/meta").jsonObject.also{remember(it,a)}
   if(id in photos)ensure(id,meta.s("sha256"),capabilities[id])
  }
 }
 fun remember(row:JsonObject,owner:Account?=null) {val a=requireNotNull(store.account);require(owner==null||owner.token==a.token);val id=row.s("id");require(valid(id)&&row.s("sha256").matches(Regex("[a-f0-9]{64}")));previews.save(a,"","file-meta:$id",buildJsonObject{put("id",id);put("sha256",row.s("sha256"));put("bytes",row["bytes"]?:JsonPrimitive("0"))})}
 fun metadata(id:String):JsonObject?=previews.read("","file-meta:$id") as? JsonObject

 fun decryptPacket(a:Account,packet:Packet)=vault.decrypt(a.storage,"packet:${packet.room}:${packet.event}",packet.encrypted)
 fun bytes(id:String):ByteArray {
  val response=requireNotNull(open(id)){"本机附件缺失"}
  return response.data.use{it.readBytes()}.also{require(it.size<=100000000)}
 }
 @Synchronized fun save(id:String,data:ByteArray,expected:String,owner:Account?=null) {
  val a=requireNotNull(store.account);require(owner==null||owner.token==a.token){"附件账户已改变"};require(valid(id)&&PacketProtocol.hash(data)==expected&&data.size<=100000000){"附件校验失败"}
  val dir=folder(a,id);val parent=dir.parentFile!!.apply{mkdirs()};val temp=File(parent,"$id.replace");temp.deleteRecursively();temp.mkdirs()
  try {
   var count=0;for(offset in data.indices step 65536){val bytes=data.copyOfRange(offset,minOf(offset+65536,data.size));File(temp,"${count}").writeBytes(vault.encrypt(a.storage,"record-file:$id:${count++}",bytes));bytes.fill(0)}
   val manifest=buildJsonObject{put("sha256",expected);put("bytes",data.size);put("chunks",count)}.toString().toByteArray();File(temp,"manifest").writeBytes(vault.encrypt(a.storage,"record-file:$id:manifest",manifest));check(store.account?.token==a.token);val old=File(parent,"$id.old");old.deleteRecursively();if(dir.exists())check(dir.renameTo(old));if(!temp.renameTo(dir)){old.renameTo(dir);error("无法原子保存附件")};old.deleteRecursively()
  }finally{temp.deleteRecursively()}
 }
 private fun digest(input:InputStream):String {val hash=MessageDigest.getInstance("SHA-256");val buffer=ByteArray(65536);try{while(true){val n=input.read(buffer);if(n<0)break;if(n>0)hash.update(buffer,0,n)}}finally{buffer.fill(0)};return hash.digest().joinToString(""){"%02x".format(it)}}
 @Synchronized private fun commit(a:Account,id:String,temp:File) {
  check(store.account?.token==a.token){"附件账户已改变"};val dir=folder(a,id);val old=File(dir.parentFile,"$id.old");old.deleteRecursively();if(dir.exists())check(dir.renameTo(old));if(!temp.renameTo(dir)){old.renameTo(dir);error("无法原子保存附件")};old.deleteRecursively()
 }
 internal fun receive(a:Account,id:String,input:InputStream,expected:String) {
  require(store.account?.token==a.token&&valid(id)&&expected.matches(Regex("[a-f0-9]{64}")))
  val parent=folder(a,id).parentFile!!.apply{mkdirs()};val temp=File(parent,"$id.receive-${java.util.UUID.randomUUID()}").apply{mkdirs()};val buffer=ByteArray(65536)
  try {
   val hash=MessageDigest.getInstance("SHA-256");var total=0L;var count=0
   while(true){check(store.account?.token==a.token){"附件账户已改变"};val n=input.read(buffer);if(n<0)break;if(n==0)continue;total+=n;require(total<=100000000);hash.update(buffer,0,n);val chunk=buffer.copyOf(n);try{File(temp,"${count}").writeBytes(vault.encrypt(a.storage,"record-file:$id:${count++}",chunk))}finally{chunk.fill(0)}}
   val actual=hash.digest().joinToString(""){"%02x".format(it)};require(actual==expected){"原始附件校验失败"}
   val manifest=buildJsonObject{put("sha256",actual);put("bytes",total);put("chunks",count)}.toString().toByteArray();File(temp,"manifest").writeBytes(vault.encrypt(a.storage,"record-file:$id:manifest",manifest));commit(a,id,temp)
  }finally{buffer.fill(0);temp.deleteRecursively()}
 }
 suspend fun ensure(id:String,expected:String,record:String?=null,owner:Account?=null)=receiving.withLock {withContext(Dispatchers.IO) {
  val a=requireNotNull(store.account);require(owner==null||owner.token==a.token){"附件账户已改变"};require(valid(id)&&expected.matches(Regex("[a-f0-9]{64}")))
  if(available(id)&&runCatching{open(id)?.data?.use{digest(it)}==expected}.getOrDefault(false))return@withContext
  gateway.readFile(id,record){input->receive(a,id,input,expected)}
 }}
 fun open(id:String):WebResourceResponse? {
  val a=store.account?:return null;if(!available(id))return null
  val dir=folder(a,id);val raw=vault.decrypt(a.storage,"record-file:$id:manifest",File(dir,"manifest").readBytes())
  val meta=try{json.parseToJsonElement(raw.decodeToString()).jsonObject}finally{raw.fill(0)}
  val count=meta["chunks"]!!.jsonPrimitive.int;var index=0
  val chunks=object:java.util.Enumeration<InputStream> {
   override fun hasMoreElements()=index<count
   override fun nextElement():InputStream {check(store.account?.token==a.token){"账号已锁定"};val n=index++;return vault.decrypt(a.storage,"record-file:$id:$n",File(dir,"$n").readBytes()).inputStream()}
  }
  return WebResourceResponse("application/octet-stream",null,200,"OK",mapOf("Cache-Control" to "no-store","Content-Length" to meta["bytes"]!!.jsonPrimitive.content),SequenceInputStream(chunks))
 }
}
