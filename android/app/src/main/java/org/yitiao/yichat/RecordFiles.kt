package org.yitiao.yichat

import android.content.Context
import android.util.AtomicFile
import android.webkit.WebResourceResponse
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*
import java.io.File
import java.io.InputStream
import java.io.SequenceInputStream
import java.security.MessageDigest
import java.util.Collections

/** Per-account encrypted chunks for media referenced inside merged records. */
class RecordFiles(private val context:Context,private val store:PacketStore,private val gateway:Gateway,private val vault:SecureStore) {
 private fun valid(id:String)=id.matches(Regex("[a-f0-9]{48}"))
 private fun folder(a:Account,id:String)=File(context.noBackupFilesDir,"record-files/${a.storage}/$id")
 fun available(id:String):Boolean { val a=store.account?:return false;return valid(id)&&File(folder(a,id),"manifest").isFile }
 suspend fun backupMerged()=withContext(Dispatchers.IO) {
  val a=store.account?:return@withContext;val db=store.database?:return@withContext;val ids=mutableSetOf<String>()
  for(packet in db.local().packets()) {
   val bytes=vault.decrypt(a.storage,"packet:${packet.room}:${packet.event}",packet.encrypted)
   try {
    val content=json.parseToJsonElement(bytes.decodeToString()).jsonObject.o("content")
    val raw=content.s("org.yitiao.record_json");if(raw.isBlank())continue
    require(SecureStore.hash(raw)==content.s("org.yitiao.record")){"合并记录摘要不一致"}
    val record=json.parseToJsonElement(raw).jsonObject
    for(message in record.a("messages")) {
     val c=message.jsonObject.o("content")
     c.a("org.yitiao.attachments").mapNotNullTo(ids){it.jsonPrimitive.contentOrNull?.takeIf(::valid)}
     Regex("mxc://chat\\.yitiao\\.org/([a-f0-9]{48})").matchEntire(c.s("url"))?.groupValues?.get(1)?.let(ids::add)
    }
   } finally { bytes.fill(0) }
  }
  for(id in ids) {
   check(store.account?.token==a.token){"备份账号已锁定"};if(available(id))continue
   val meta=gateway.call("/api/files/$id/meta").jsonObject;val expected=meta.s("sha256");require(expected.matches(Regex("[a-f0-9]{64}")))
   val parent=folder(a,id).parentFile!!.apply{mkdirs()};val temp=File(parent,"$id.part");temp.deleteRecursively();temp.mkdirs()
   try {
    var count=0;var total=0L;val digest=MessageDigest.getInstance("SHA-256")
    gateway.readFile(id) { input ->
     val buffer=ByteArray(65536)
     while(true) {
      check(store.account?.token==a.token){"备份账号已锁定"};val n=input.read(buffer);if(n<0)break;if(n==0)continue
      total+=n;require(total<=100000000);digest.update(buffer,0,n)
      val data=vault.encrypt(a.storage,"record-file:$id:$count",buffer.copyOf(n));File(temp,"${count++}").writeBytes(data)
     }
     buffer.fill(0)
    }
    val actual=digest.digest().joinToString(""){"%02x".format(it)};require(actual==expected){"合并记录附件摘要不一致"}
    val manifest=buildJsonObject{put("sha256",actual);put("bytes",total);put("chunks",count)}.toString().toByteArray()
    File(temp,"manifest").writeBytes(vault.encrypt(a.storage,"record-file:$id:manifest",manifest))
    check(store.account?.token==a.token);check(temp.renameTo(folder(a,id))){"无法保存合并记录附件"}
   } finally { temp.deleteRecursively() }
  }
 }
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
