package org.yitiao.yichat
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
/** Real encrypted AtomicFile, Room packets, attachment chunks and cold native bootstrap. */
@RunWith(AndroidJUnit4::class)
class ProtocolStorageTest {
 @Test fun originalMaterialFilesEmptyDayAndExportSurviveColdStorageWithoutNetwork()=runBlocking {
  val context=InstrumentationRegistry.getInstrumentation().targetContext
  val graph=Graph(context.applicationContext as android.app.Application)
  val a=Account("protocol-storage-test","isolated","@isolated:chat.yitiao.org","COLD","not-a-network-token","not-a-proof");val room="!protocol-test";val day="2026-01-02";val at=PacketProtocol.start(day)+1000
  graph.store.open(a)
  try {
   val dao=requireNotNull(graph.store.database).local();val original="原始卡片 👋 即使源实例销毁仍保留";val raw=PacketProtocol.canonical(buildJsonObject{put("event_id","\$local");put("room_id",room);put("sender",a.mxid);put("type","m.room.message");put("origin_server_ts",at);put("content",buildJsonObject{put("msgtype","m.text");put("body",original)})});val sha=PacketProtocol.hash(raw.toByteArray())
   val receipt=buildJsonObject{put("received_ms",at);put("origin_server_ts",at);put("type","m.room.message")}
   dao.receipt(TrustedReceipt(room,"\$local",sha,1,graph.vault.encrypt(a.storage,"receipt:$room:\$local",receipt.toString().toByteArray())))
   dao.addPacket(Packet(room,"\$local",1,sha,graph.vault.encrypt(a.storage,"packet:$room:\$local",raw.toByteArray())))
   dao.watermark(Watermark("*",PacketProtocol.immutable(day),null))
   graph.protocol.saveUi(buildJsonObject{put("info",buildJsonArray{add(JsonArray(listOf(JsonPrimitive(room),buildJsonObject{put("room",room);put("kind","group");put("owner",a.mxid);put("encryption","plain")})))})})
   val materialRaw=PacketProtocol.canonical(buildJsonObject{put("version",2);put("kind","share");put("content",buildJsonObject{put("msgtype","m.text");put("body",original)});put("previews",JsonArray(emptyList()));put("files",JsonArray(emptyList()))})
   val material=buildJsonObject{put("canonical",materialRaw);put("meta",buildJsonObject{put("event_id","\$original_material");put("id","b".repeat(48));put("room_id",room);put("sender",a.mxid);put("shared_at",at.toString());put("payload_sha256",PacketProtocol.hash(materialRaw.toByteArray()));put("finalized",false);put("sha256",JsonNull);put("files",JsonArray(emptyList()));put("kind","share")})}
   graph.messageMaterials.save(material,a);assertEquals(materialRaw,graph.messageMaterials.read("\$original_material")!!.s("canonical"))
   val fid="a".repeat(48);val bytes="original bytes".toByteArray();graph.recordFiles.save(fid,bytes,PacketProtocol.hash(bytes));assertArrayEquals(bytes,graph.recordFiles.bytes(fid));graph.recordFiles.ensure(fid,PacketProtocol.hash(bytes))
   val streamed=ByteArray(131073){(it%251).toByte()};val streamId="c".repeat(48)
   streamed.inputStream().use{graph.recordFiles.receive(a,streamId,it,PacketProtocol.hash(streamed))};assertArrayEquals(streamed,graph.recordFiles.bytes(streamId))
   assertTrue(runCatching{"wrong bytes".byteInputStream().use{graph.recordFiles.receive(a,streamId,it,PacketProtocol.hash(streamed))}}.isFailure);assertArrayEquals(streamed,graph.recordFiles.bytes(streamId))
   val packet=graph.dayPackets.collect(room,day,true);assertTrue(packet.b("complete"));assertEquals(1,packet.a("entries").size)
   val empty=graph.dayPackets.collect(room,"2026-01-01",true);assertTrue(empty.b("complete"));assertTrue(empty.a("entries").isEmpty());assertNotEquals(PacketProtocol.dayHash(packet),PacketProtocol.dayHash(empty))
   val bundle=graph.dayPackets.export(listOf(room),"2026-01-01",day);val encoded=DataPackageCodec.encode(bundle);assertEquals(bundle,DataPackageCodec.decode(encoded))
   graph.store.close();graph.store.open(a)
   assertEquals(materialRaw,graph.messageMaterials.read("\$original_material")!!.s("canonical"));assertTrue(graph.messageMaterials.read("\$original_material")!!.o("meta").b("finalized"))
   val cold=graph.protocol.bootstrap();assertTrue(cold.a("rooms").any{it.jsonObject.s("room")==room});assertTrue(cold.a("rooms").first().jsonObject.a("rows").isNotEmpty());assertArrayEquals(bytes,graph.recordFiles.bytes(fid))
  }finally{graph.store.close();listOf("web-accounts","record-files","message-protocol").forEach{File(context.noBackupFilesDir,"$it/${a.storage}").deleteRecursively()}}
 }
}
