package org.yitiao.yichat

import android.content.Intent
import android.webkit.WebView
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.UiDevice
import androidx.webkit.WebViewCompat
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.*
import okhttp3.OkHttpClient
import okhttp3.Request
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

@RunWith(AndroidJUnit4::class)
class LaunchWebViewTest {
 private val inst get()=InstrumentationRegistry.getInstrumentation()
 private val context get()=inst.targetContext
 private fun eval(web:WebView,code:String):String { val done=CountDownLatch(1);var result="";inst.runOnMainSync { web.evaluateJavascript(code){result=it;done.countDown()} };check(done.await(30,TimeUnit.SECONDS));return result }
 private fun waitJs(web:WebView,code:String,timeout:Long=120000){val end=System.currentTimeMillis()+timeout;while(System.currentTimeMillis()<end){if(eval(web,code)=="true")return;Thread.sleep(500)};fail("WebView condition not met: "+code+" / "+eval(web,"document.body.innerText.slice(0,500)"))}
 private fun tap(web:WebView,expression:String){val point=json.parseToJsonElement(eval(web,"(()=>{const e=$expression;if(!e)return null;const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2,w:innerWidth}})()")).jsonObject;val origin=IntArray(2);var width=0;inst.runOnMainSync{web.getLocationOnScreen(origin);width=web.width};val scale=width.toDouble()/point["w"]!!.jsonPrimitive.double;UiDevice.getInstance(inst).click((origin[0]+point["x"]!!.jsonPrimitive.double*scale).toInt(),(origin[1]+point["y"]!!.jsonPrimitive.double*scale).toInt())}
 @Test fun realWebMessageBridgeCryptoSyncSafeAreaAndPhysicalGroupSend()=runBlocking {
  assumeTrue(BuildConfig.DEBUG && BuildConfig.GATEWAY=="http://127.0.0.1:18018")
  assumeTrue((WebViewCompat.getCurrentWebViewPackage(context)?.versionName?.substringBefore('.')?.toIntOrNull()?:0)>=100)
  val raw=OkHttpClient().newCall(Request.Builder().url(BuildConfig.GATEWAY+"/__fixture/new-device/alice").build()).execute().use { check(it.isSuccessful);json.parseToJsonElement(it.body!!.string()).jsonObject }
  val account=Account(raw.s("uid"),raw.s("user"),raw.s("mxid"),raw.s("device"),raw.s("token"),"isolated-unused-proof")
  val graph=(context.applicationContext as YiChatApplication).graph
  graph.vault.put("web.session",json.encodeToString(account).toByteArray());graph.auth.restore();graph.activate(account)
  val activity=inst.startActivitySync(Intent(context,MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as MainActivity
  try {
   val web=MainActivity::class.java.getDeclaredField("web").apply{isAccessible=true}.get(activity) as WebView
   waitJs(web,"!!document.querySelector('.sidebar')")
   waitJs(web,"document.documentElement.dataset.app==='android'")
   waitJs(web,"document.querySelector('.brand-image')?.naturalWidth===1024")
   val file=File(context.filesDir,"launch-sidebar.png");UiDevice.getInstance(inst).takeScreenshot(file)
   var safeTop=0;var actualY=0
   inst.runOnMainSync { safeTop=ViewCompat.getRootWindowInsets(web)!!.getInsetsIgnoringVisibility(WindowInsetsCompat.Type.statusBars()).top;val loc=IntArray(2);web.getLocationOnScreen(loc);actualY=loc[1] }
   assertTrue("WebView begins below system status bar",safeTop>0 && actualY>=safeTop)
   tap(web,"[...document.querySelectorAll('.room-row')].find(e=>e.innerText.includes('Android isolated group'))")
   waitJs(web,"!!document.querySelector('textarea[aria-label=消息]')")
   waitJs(web,"!document.querySelector('textarea[aria-label=消息]').disabled")
   tap(web,"document.querySelector('textarea[aria-label=消息]')")
   waitJs(web,"document.activeElement?.tagName==='TEXTAREA'")
   inst.sendStringSync("Android real WebView group check")
   waitJs(web,"document.querySelector('textarea[aria-label=消息]').value.includes('Android real WebView group check')")
   UiDevice.getInstance(inst).takeScreenshot(File(context.filesDir,"launch-keyboard.png"))
   tap(web,"document.querySelector('button[aria-label=发送消息]')")
   waitJs(web,"!document.querySelector('.outgoing') && [...document.querySelectorAll('.message-body')].some(e=>e.innerText==='Android real WebView group check')")
   UiDevice.getInstance(inst).takeScreenshot(File(context.filesDir,"launch-group-sent.png"))
   assertEquals("true",eval(web,"window.isSecureContext && !!navigator.locks && !!indexedDB"))
  } finally { inst.runOnMainSync{activity.finish()} }
 }
}
