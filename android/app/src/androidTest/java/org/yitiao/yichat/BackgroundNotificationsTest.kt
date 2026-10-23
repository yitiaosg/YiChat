package org.yitiao.yichat

import android.app.NotificationManager
import android.content.Intent
import android.os.Build
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.UiDevice
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.MediaType.Companion.toMediaType

@RunWith(AndroidJUnit4::class)
class BackgroundNotificationsTest {
 @Test fun taskRemovalKeepsNotificationListenerAndReceivesNewMessage()=runBlocking {
  assumeTrue(BuildConfig.DEBUG && BuildConfig.GATEWAY=="http://127.0.0.1:18018")
  val instrumentation=InstrumentationRegistry.getInstrumentation();val context=instrumentation.targetContext
  val http=OkHttpClient()
  fun get(path:String)=http.newCall(Request.Builder().url(BuildConfig.GATEWAY+path).build()).execute().use{check(it.isSuccessful);json.parseToJsonElement(it.body!!.string()).jsonObject}
  val alice=get("/__fixture/accounts/alice");val bob=get("/__fixture/accounts/bob");val ready=get("/__fixture/ready")
  val graph=(context.applicationContext as YiChatApplication).graph
  val account=Account(alice.s("uid"),alice.s("user"),alice.s("mxid"),alice.s("device"),alice.s("token"),alice.s("proof"))
  graph.vault.put("web.session",json.encodeToString(Account.serializer(),account).toByteArray());graph.auth.restore();graph.activate(account)
  if(Build.VERSION.SDK_INT>=33)instrumentation.uiAutomation.executeShellCommand("pm grant ${context.packageName} android.permission.POST_NOTIFICATIONS").close()
  val activity=instrumentation.startActivitySync(Intent(context,MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
  try {
   graph.notifications.foreground=true;graph.notifications.configure(true)
   val manager=context.getSystemService(NotificationManager::class.java)
   val started=System.currentTimeMillis()+20_000
   while(System.currentTimeMillis()<started&&!manager.activeNotifications.any{it.id==2})Thread.sleep(200)
   assertTrue("ongoing listener notification is visible",manager.activeNotifications.any{it.id==2})
   instrumentation.runOnMainSync{activity.finishAndRemoveTask()}
   UiDevice.getInstance(instrumentation).pressHome()
   val untilBackground=System.currentTimeMillis()+10_000
   while(graph.notifications.foreground&&System.currentTimeMillis()<untilBackground)Thread.sleep(100)
   assertFalse(graph.notifications.foreground)
   assertTrue("listener survives removal of the activity task",manager.activeNotifications.any{it.id==2})
   val room=ready.s("group");val encoded=java.net.URLEncoder.encode(room,"UTF-8")
   val body="{\"msgtype\":\"m.text\",\"body\":\"isolated background notification test\"}".toRequestBody("application/json".toMediaType())
   http.newCall(Request.Builder().url(BuildConfig.GATEWAY+"/_matrix/client/v3/rooms/$encoded/send/m.room.message/background-"+System.nanoTime()).header("Authorization","Bearer "+bob.s("token")).put(body).build()).execute().use{assertTrue(it.isSuccessful)}
   val deadline=System.currentTimeMillis()+60_000
   while(System.currentTimeMillis()<deadline&&!manager.activeNotifications.any{it.tag==room})Thread.sleep(300)
   assertTrue("a new accepted message is notified with the chat screen closed",manager.activeNotifications.any{it.tag==room})
  } finally {graph.notifications.configure(false);graph.lock();instrumentation.runOnMainSync{activity.finish()};http.dispatcher.executorService.shutdown()}
 }
}
