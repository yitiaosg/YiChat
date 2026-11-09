package org.yitiao.yichat

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*
import java.time.LocalDate

class MessageProtocol(private val store:PacketStore,private val gateway:Gateway,private val vault:SecureStore,private val cache:ProtocolStore,private val materials:MessageMaterials,private val days:DayPacketRepository,private val timeline:LocalTimeline) {
 private val mutex=Mutex();private var rootsAt=0L
 fun saveUi(value:JsonObject){cache.put("ui",value)}
 suspend fun bootstrap():JsonObject=withContext(Dispatchers.IO) {
  val a=requireNotNull(store.account);val ui=(cache.get("ui") as? JsonObject)?:JsonObject(emptyMap());val dao=requireNotNull(store.database).local()
  val rooms=(ui.a("info").mapNotNull{(it as? JsonArray)?.firstOrNull()?.jsonPrimitive?.contentOrNull}+dao.recordRooms()).distinct().take(100)
  val result=buildJsonArray {for(room in rooms){val page=timeline.older(room,"",Long.MAX_VALUE,40);add(buildJsonObject{put("room",room);put("rows",page.a("rows"));put("cursor",page["cursor"]?:JsonNull)})}}
  check(store.account?.token==a.token)
  buildJsonObject{put("ui",ui);put("rooms",result);put("states",days.states());put("untrusted",days.displayEntries(result.flatMap{it.jsonObject.a("rows")}.map{it.jsonObject.s("event_id")}.toSet()))}
 }
 suspend fun syncUi() {
  val a=requireNotNull(store.account);val info=gateway.call("/api/rooms").jsonArray;val prefs=gateway.call("/api/conversations/preferences").jsonArray
  val mxids=(listOf(a.mxid)+info.flatMap{r->val row=r.jsonObject;listOf(row.s("owner"),row.s("peer"))}).filter{it.startsWith("@")} .distinct().take(100)
  val profiles=gateway.call("/api/profiles",buildJsonObject{put("mxids",JsonArray(mxids.map(::JsonPrimitive)));put("usernames",JsonArray(emptyList()))}).jsonArray
  check(store.account?.token==a.token)
  cache.put("ui",buildJsonObject{put("info",JsonArray(info.map{JsonArray(listOf(JsonPrimitive(it.jsonObject.s("room")),it))}));put("preferences",JsonArray(prefs.map{JsonArray(listOf(JsonPrimitive(it.jsonObject.s("room")),it))}));put("room_names",cache.get("ui")?.jsonObject?.a("room_names")?:JsonArray(emptyList()));put("profiles",JsonArray(profiles.map{JsonArray(listOf(JsonPrimitive(it.jsonObject.s("mxid")),it))}))},a)
 }
 suspend fun sync()=mutex.withLock {
  withContext(Dispatchers.IO) {
   materials.syncNew()
   val now=System.currentTimeMillis()
   if(now-rootsAt>300000){syncUi();rootsAt=now
    val ui=cache.get("ui")?.jsonObject;val rooms=ui?.a("info")?.map{it.jsonArray.first().jsonPrimitive.content}.orEmpty();val end=LocalDate.parse(PacketProtocol.day(now)).minusDays(if(now>=PacketProtocol.immutable(PacketProtocol.day(now-86400000)))1 else 2)
    // Cover empty GMT days too. Older lazy-loaded dates are checked by the UI.
    for(room in rooms)for(offset in 0L..6L)runCatching{days.checkDay(room,end.minusDays(offset).toString())}
   }
   days.exchange()
  }
 }
}
