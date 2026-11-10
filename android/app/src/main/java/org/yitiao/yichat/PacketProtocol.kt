package org.yitiao.yichat

import kotlinx.serialization.json.*
import java.security.MessageDigest
import java.security.SecureRandom
import java.time.LocalDate
import java.time.ZoneOffset
import java.util.Base64
import java.util.zip.GZIPInputStream
import java.util.zip.GZIPOutputStream
import javax.crypto.Cipher
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.PBEKeySpec
import javax.crypto.spec.SecretKeySpec

/** Canonical projections contain decimal strings; original JSON bytes remain untouched. */
object PacketProtocol {
 const val FORMAT="yichat-day-packet-v2"
 const val MATERIAL="org.yitiao.material"
 fun hash(raw:ByteArray)=MessageDigest.getInstance("SHA-256").digest(raw).joinToString(""){"%02x".format(it)}
 fun canonical(value:JsonElement):String=sorted(value).toString()+"\n"
 private fun sorted(value:JsonElement):JsonElement=when(value){is JsonObject->JsonObject(value.toSortedMap().mapValues{sorted(it.value)});is JsonArray->JsonArray(value.map(::sorted));else->value}
 fun start(day:String):Long {val date=LocalDate.parse(day);require(date.toString()==day);return date.atStartOfDay().toInstant(ZoneOffset.UTC).toEpochMilli()}
 fun day(ms:Long)=java.time.Instant.ofEpochMilli(ms).atOffset(ZoneOffset.UTC).toLocalDate().toString()
 fun immutable(day:String)=start(day)+86400000L+300000L
 fun files(rows:JsonArray)=JsonArray(rows.map{v->val r=v.jsonObject;obj("id" to r.s("id"),"bytes" to r.s("bytes"),"sha256" to r.s("sha256"))}.sortedBy{it.s("id")})
 fun materialProjection(meta:JsonObject,payload:String,files:JsonArray)=buildJsonObject {
  put("event_id",meta.s("event_id"));put("format","yichat-material-v2");put("payload_sha256",payload);put("room_id",meta.s("room_id"));put("sender",meta.s("sender"));put("shared_at",meta.s("shared_at"));put("files",PacketProtocol.files(files));put("version","2")
 }
 fun materialHash(meta:JsonObject,payload:String,files:JsonArray)=hash(canonical(materialProjection(meta,payload,files)).toByteArray())
 fun validateMaterial(value:JsonObject):JsonObject {
  val raw=value.s("canonical");val meta=value.o("meta");require(raw.isNotBlank()&&meta.s("event_id").startsWith("$")){"原始消息缺失"}
  require(hash(raw.toByteArray())==meta.s("payload_sha256")){"原始消息内容损坏"}
  if(meta.b("finalized"))require(materialHash(meta,meta.s("payload_sha256"),meta.a("files"))==meta.s("sha256")){"原始消息时间摘要损坏"}
  return value
 }
 fun header(room:String,day:String)=buildJsonObject{put("day",day);put("format",FORMAT);put("immutable_at",immutable(day).toString());put("room_id",room);put("version","2")}
 fun entry(value:JsonObject,verifyFiles:Boolean=true):JsonObject {
  if(value["redacted"]!=null)require(value["redacted"] is JsonPrimitive && !value["redacted"]!!.jsonPrimitive.isString && value["redacted"]!!.jsonPrimitive.booleanOrNull!=null){"撤回状态无效"}
  val raw=value.s("raw");val event=json.parseToJsonElement(raw).jsonObject;require(raw.endsWith("\n")&&event.s("event_id")==value.s("event_id")&&event.s("room_id")==value.s("room_id")&&event.s("sender")==value.s("sender")&&event.s("type")==value.s("type")){"消息身份不匹配"}
  for(field in listOf("sequence","received_ms"))require(value.s(field).matches(Regex("0|[1-9][0-9]{0,18}")))
  val originals=value.a("files");val projected=files(originals);require(projected.map{it.jsonObject.s("id")}.distinct().size==projected.size)
  for(f in originals.map{it.jsonObject}) {
   require(f.s("id").matches(Regex("[a-f0-9]{48}"))&&f.s("sha256").matches(Regex("[a-f0-9]{64}")))
   if(verifyFiles&&f["data"]!=null&&f["data"]!=JsonNull){require(f["data"] is JsonPrimitive&&f["data"]!!.jsonPrimitive.isString){"附件实体必须为编码字符串"};val bytes=Base64.getDecoder().decode(f.s("data"));require(bytes.size.toLong()==f.s("bytes").toLong()&&hash(bytes)==f.s("sha256")){"附件内容损坏"}}
  }
  var material:JsonElement=JsonNull
  if(value["material"] is JsonObject) {
   val m=value.o("material");require(m.s("event_id")==value.s("event_id")&&m.s("room_id")==value.s("room_id")&&m.s("sender")==value.s("sender")&&m.s("shared_at")==value.s("received_ms")&&m.b("finalized")){"原始消息身份不匹配"};val canonical=m.s("canonical");val sha=hash(canonical.toByteArray());val doc=json.parseToJsonElement(canonical).jsonObject
   require(m.s("id")==event.o("content").o(MATERIAL).s("id")&&m.s("kind")==doc.s("kind")&&event.o("content").o(MATERIAL).s("kind")==doc.s("kind")&&files(m.a("files"))==projected&&sha==event.o("content").o(MATERIAL).s("sha256")&&sha==m.s("payload_sha256")&&files(doc.a("files"))==projected){"原始消息文件不匹配"}
   val meta=obj("event_id" to value.s("event_id"),"room_id" to value.s("room_id"),"sender" to value.s("sender"),"shared_at" to value.s("received_ms"))
   val actual=materialHash(meta,sha,projected);require(actual==m.s("sha256")){"原始消息分享时间不匹配"};material=JsonPrimitive(actual)
  } else require(event.o("content")[MATERIAL]==null){"原始分享数据缺失"}
  return buildJsonObject{put("event_id",value.s("event_id"));put("redacted",value.b("redacted"));put("files",projected);put("material_sha256",material);put("raw_sha256",hash(raw.toByteArray()));for(field in listOf("received_ms","room_id","sender","sequence","type"))put(field,value.s(field))}
 }
 fun dayHash(packet:JsonObject,verifyFiles:Boolean=true):String {
  val room=packet.s("room_id");val day=packet.s("day");val low=start(day);val entries=packet.a("entries");require(entries.size<=100000);val seen=mutableSetOf<String>();var last=-1L
  val projected=JsonArray(entries.map{v->val row=v.jsonObject;val at=row.s("received_ms").toLong();val sequence=row.s("sequence").toLong();require(row.s("room_id")==room&&at>=low&&at<low+86400000&&seen.add(row.s("event_id"))&&sequence>last){"会话、日期、消息顺序或ID不匹配"};last=sequence;entry(row,verifyFiles)})
  return hash(canonical(JsonObject(header(room,day)+("entries" to projected))).toByteArray())
 }
 fun hydrate(bundle:JsonObject,day:JsonObject)=JsonObject(day+("entries" to JsonArray(day.a("entries").map{v->val e=v.jsonObject;JsonObject(e+("files" to JsonArray(e.a("files").map{f->val row=f.jsonObject;val saved=bundle.o("files").o(row.s("id"));JsonObject(row+if(row["data"]!=null)emptyMap() else mapOf("data" to (saved["data"]?:JsonNull)))})))})))
 fun pack(days:List<JsonObject>):JsonObject {
  val all=linkedMapOf<String,JsonElement>();val stripped=days.map{day->JsonObject(day+("entries" to JsonArray(day.a("entries").map{v->val e=v.jsonObject;JsonObject(e+("files" to JsonArray(e.a("files").map{f->val row=f.jsonObject;if(row["data"]!=null){val prior=all[row.s("id")]?.jsonObject;require(prior==null||prior.s("sha256")==row.s("sha256"));all[row.s("id")]=row};JsonObject(row-"data")})))})))}
  return buildJsonObject{put("format","yichat-data-package");put("version",2);put("created_ms",System.currentTimeMillis());put("days",JsonArray(stripped));put("files",JsonObject(all))}
 }
}
object DataPackageCodec {
 private val magic="YICHAT2\u0000".toByteArray();private const val MAX=256*1024*1024
 private fun key(password:String,salt:ByteArray)=SecretKeySpec(SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(PBEKeySpec((if(password.isEmpty())"YiChat-data-package-v2" else password).toCharArray(),salt,10000,256)).encoded,"AES")
 fun encode(value:JsonObject,password:String=""):ByteArray {
  val raw=value.toString().toByteArray();require(raw.size<=MAX){"数据包超过256MB"};val packed=java.io.ByteArrayOutputStream().also{out->GZIPOutputStream(out).use{it.write(raw)}}.toByteArray();raw.fill(0)
  val random=SecureRandom();val salt=ByteArray(16).also(random::nextBytes);val iv=ByteArray(12).also(random::nextBytes);val cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,key(password,salt),GCMParameterSpec(128,iv));cipher.updateAAD(magic)
  return (magic+salt+iv+cipher.doFinal(packed)).also{require(it.size<=128*1024*1024){"数据包超过128MB，请分开导出日期"}}
 }
 fun decode(raw:ByteArray,password:String=""):JsonObject {
  require(raw.size in 52..128*1024*1024&&raw.copyOfRange(0,8).contentEquals(magic)){"数据包格式无效或超过128MB"}
  val cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.DECRYPT_MODE,key(password,raw.copyOfRange(8,24)),GCMParameterSpec(128,raw.copyOfRange(24,36)));cipher.updateAAD(magic);val opened=cipher.doFinal(raw.copyOfRange(36,raw.size))
  val bytes=GZIPInputStream(opened.inputStream()).use {input->val out=java.io.ByteArrayOutputStream();val buffer=ByteArray(65536);var total=0;while(true){val n=input.read(buffer);if(n<0)break;total+=n;require(total<=MAX){"解压后超过256MB"};out.write(buffer,0,n)};out.toByteArray()}
  return try{json.parseToJsonElement(bytes.decodeToString()).jsonObject.also{require(it.s("format")=="yichat-data-package"&&it["version"]?.jsonPrimitive?.int==2&&it["days"] is JsonArray&&it["files"] is JsonObject&&it.a("days").size<=10000){"数据包版本无效"};val seen=mutableSetOf<String>();for(value in it.a("days")){val d=value.jsonObject;val day=d.s("day");require(LocalDate.parse(day).toString()==day&&d.s("room_id").startsWith("!")&&d["entries"] is JsonArray&&d["complete"] is JsonPrimitive&&d["complete"]!!.jsonPrimitive.booleanOrNull!=null&&seen.add(d.s("room_id")+"|"+day)){"会话日期重复或无效"}}}}finally{bytes.fill(0);opened.fill(0)}
 }
}
