package org.yitiao.yichat
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

@RunWith(AndroidJUnit4::class)
class LiveMessagesTest {
 @Test fun newlyVisibleMessageSurvivesImmediateCloseOfflineThenPromotesOnlyWithValidReceipt()=runBlocking {
  val context=InstrumentationRegistry.getInstrumentation().targetContext;val graph=Graph(context.applicationContext as android.app.Application)
  val a=Account("live-persistence-test","isolated","@isolated:chat.yitiao.org","INSTANT","not-network-token","not-proof");val room="!instant";val id="\$instant";val at=System.currentTimeMillis()
  graph.store.open(a)
  try {
   val event=buildJsonObject{put("event_id",id);put("room_id",room);put("type","m.room.message");put("sender",a.mxid);put("origin_server_ts",at);put("content",obj("msgtype" to "m.text","body" to "刚收到就关闭，断网也能看见"))}
   assertEquals("saved",graph.liveMessages.remember(obj("room" to room,"raw" to event.toString())).s("state"))
   assertTrue(requireNotNull(graph.store.database).local().receipts().isEmpty())
   graph.store.close();graph.store.open(a)
   val pending=graph.protocol.bootstrap().a("rooms").single().jsonObject.a("rows").single().jsonObject
   assertTrue(pending.b("provisional"));assertTrue(String(android.util.Base64.decode(pending.s("raw"),android.util.Base64.NO_WRAP)).contains("刚收到就关闭"))
   val canonical=PacketProtocol.canonical(event);val hash=PacketProtocol.hash(canonical.toByteArray())
   val receipt=buildJsonObject{put("event_id",id);put("room",room);put("sender",a.mxid);put("sequence",100);put("received_ms",at);put("origin_server_ts",at);put("type","m.room.message");put("sha256",hash)}
   fun response(raw:String)=buildJsonArray{add(buildJsonObject{put("receipt",receipt);put("packet",obj("event_id" to id,"sha256" to hash,"raw" to android.util.Base64.encodeToString(raw.toByteArray(),android.util.Base64.NO_WRAP)))})}
   assertTrue(runCatching{graph.liveMessages.acceptRecent(a,response(canonical.replace("刚收到","被篡改")))}.isFailure)
   assertTrue(requireNotNull(graph.store.database).local().receipts().isEmpty());assertEquals(1,graph.liveMessages.snapshot().size)
   graph.liveMessages.acceptRecent(a,response(canonical));assertTrue(graph.liveMessages.snapshot().isEmpty())
   graph.store.close();graph.store.open(a)
   val verified=graph.protocol.bootstrap().a("rooms").single().jsonObject.a("rows").single().jsonObject
   assertFalse(verified.b("provisional"));assertEquals(hash,verified.s("sha256"));assertEquals("100",verified.s("sequence"))
  }finally{graph.store.close();listOf("web-accounts","message-protocol","special-previews","record-files").forEach{File(context.noBackupFilesDir,"$it/${a.storage}").deleteRecursively()}}
 }
}
