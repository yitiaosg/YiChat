package org.yitiao.yichat
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test
class PacketProtocolTest {
 private fun vectors()=json.parseToJsonElement(javaClass.getResource("/protocol-v2-vectors.json")!!.readText()).jsonObject
 @Test fun pythonBrowserAndNativeAgreeOnAllGoldenHashes() {
  val v=vectors();for((name,packet) in v.o("packets"))assertEquals(name,v.o("sha256").s(name),PacketProtocol.dayHash(packet.jsonObject))
 }
 @Test fun actualAttachmentBytesAndOriginalPayloadAreAuthenticated() {
  val v=vectors();val p=v.o("packets").o("media");val e=p.a("entries").first().jsonObject;val f=e.a("files").first().jsonObject;val bad=JsonObject(f+("data" to JsonPrimitive(java.util.Base64.getEncoder().encodeToString("bad image".toByteArray()))));val changed=JsonObject(p+("entries" to JsonArray(listOf(JsonObject(e+("files" to JsonArray(listOf(bad))))))))
  assertThrows(IllegalArgumentException::class.java){PacketProtocol.dayHash(changed)}
 }
 @Test fun browserEncryptedPackageIsReadableByNative() {
  val value=DataPackageCodec.decode(javaClass.getResource("/protocol-v2-browser.ycp")!!.readBytes(),"")
  assertEquals(vectors().o("sha256").s("special"),PacketProtocol.dayHash(PacketProtocol.hydrate(value,value.a("days").single().jsonObject)))
  assertTrue(value.a("days").isNotEmpty())
  System.getenv("YICHAT_CODEC_VECTOR_OUT")?.let{java.io.File(it).writeBytes(DataPackageCodec.encode(value,""))}
 }
 @Test fun portableGzipPackagesRejectTamperingAndWrongPasswords() {
  val p=PacketProtocol.pack(listOf(vectors().o("packets").o("special")));val bytes=DataPackageCodec.encode(p,"local");assertEquals(p,DataPackageCodec.decode(bytes,"local"));assertThrows(Exception::class.java){DataPackageCodec.decode(bytes,"wrong")};bytes[bytes.lastIndex]=(bytes.last().toInt() xor 1).toByte();assertThrows(Exception::class.java){DataPackageCodec.decode(bytes,"local")}
 }
 @Test fun undownloadedFileKeepsSameDayRootWithoutFetchingBytes() {
  val p=vectors().o("packets").o("media");val e=p.a("entries").single().jsonObject;val f=e.a("files").single().jsonObject
  val stripped=JsonObject(e+("files" to JsonArray(listOf(JsonObject(f-"data")))))
  val absent=JsonObject(p+("entries" to JsonArray(listOf(stripped))))
  assertEquals(PacketProtocol.dayHash(p),PacketProtocol.dayHash(absent));val bundle=PacketProtocol.pack(listOf(absent));assertEquals(PacketProtocol.dayHash(p),PacketProtocol.dayHash(PacketProtocol.hydrate(bundle,bundle.a("days").single().jsonObject)))
 }
 @Test fun photoPlanIncludesMergedImagesButExcludesOrdinaryFiles() {
  val photo="a".repeat(48);val file="b".repeat(48)
  val record=buildJsonObject{put("messages",buildJsonArray{add(obj("msgtype" to "m.image","url" to "mxc://chat.yitiao.org/$photo"));add(obj("msgtype" to "m.file","url" to "mxc://chat.yitiao.org/$file"))})}
  assertEquals(setOf(photo),FileDownloadPlan.ids(record,true));assertEquals(setOf(photo,file),FileDownloadPlan.ids(record))
 }
}
