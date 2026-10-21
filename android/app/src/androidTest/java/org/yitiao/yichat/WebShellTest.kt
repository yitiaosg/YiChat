package org.yitiao.yichat

import android.app.Activity
import android.app.Instrumentation
import android.content.Intent
import android.content.IntentFilter
import android.webkit.WebView
import android.net.Uri
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import org.junit.Assume.assumeTrue
import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

@RunWith(AndroidJUnit4::class)
class WebShellTest {
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private fun eval(web: WebView, code: String): String {
        val done=CountDownLatch(1);var answer=""
        instrumentation.runOnMainSync { web.evaluateJavascript(code) { answer=it;done.countDown() } }
        check(done.await(20,TimeUnit.SECONDS));return answer
    }
    private fun waitJs(web:WebView,code:String) {
        val end=System.currentTimeMillis()+60000
        while(System.currentTimeMillis()<end) { if(eval(web,code)=="true")return;Thread.sleep(300) }
        fail("WebView condition timed out: $code")
    }
    private fun modernEngine() = (WebViewCompat.getCurrentWebViewPackage(context)?.versionName?.substringBefore('.')?.toIntOrNull() ?: 0)>=100 && WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)
    @Test fun bundledOfflineFrameworkBridgeStorageAndExternalBrowser() {
        assumeTrue("Modern WebView required for JS engine test",modernEngine())
        val activity=instrumentation.startActivitySync(Intent(context,MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as MainActivity
        try {
            val field=MainActivity::class.java.getDeclaredField("web").apply { isAccessible=true }
            val web=field.get(activity) as WebView
            val cacheField=MainActivity::class.java.getDeclaredField("cache").apply { isAccessible=true }
            (cacheField.get(activity) as WebShellCache).bundledOnly=true
            instrumentation.runOnMainSync { web.settings.blockNetworkLoads=true;web.reload() }
            waitJs(web,"document.documentElement.dataset.app==='android' && !!document.querySelector('a[href=\"/auth/login\"]')")
            assertEquals("true",eval(web,"window.isSecureContext && !!navigator.locks && !!indexedDB"))
            assertEquals("true",eval(web,"getComputedStyle(document.querySelector('.welcome-features')).userSelect==='none'"))
            eval(web,"window.__rpcResult=null;YiChatApp.onmessage=e=>{window.__rpcResult=JSON.parse(e.data)};YiChatApp.postMessage(JSON.stringify({id:'test-session',method:'session'}))")
            waitJs(web,"window.__rpcResult?.id==='test-session'")
            assertEquals("true",eval(web,"__rpcResult.ok && __rpcResult.value.session===null && !JSON.stringify(__rpcResult).includes('proof')"))
            eval(web,"window.__dbDone=false;let r=indexedDB.open('wrapper-test',1);r.onupgradeneeded=()=>r.result.createObjectStore('state');r.onsuccess=()=>{let db=r.result,t=db.transaction('state','readwrite');t.objectStore('state').put('retained','marker');t.oncomplete=()=>{db.close();window.__dbDone=true}}")
            waitJs(web,"window.__dbDone===true")
            instrumentation.runOnMainSync { web.reload() }
            waitJs(web,"document.documentElement.dataset.app==='android' && !!document.querySelector('.welcome-features')")
            eval(web,"window.__retained=false;let r=indexedDB.open('wrapper-test');r.onsuccess=()=>{let db=r.result,q=db.transaction('state').objectStore('state').get('marker');q.onsuccess=()=>{window.__retained=q.result==='retained';db.close()}}")
            waitJs(web,"window.__retained===true")
            val filter=IntentFilter(Intent.ACTION_VIEW).apply { addDataScheme("https");addCategory(Intent.CATEGORY_BROWSABLE) }
            val monitor=instrumentation.addMonitor(filter,Instrumentation.ActivityResult(Activity.RESULT_CANCELED,null),true)
            try {
                eval(web,"location.href='https://yidt.yitiao.org/services/yichat/'")
                val end=System.currentTimeMillis()+8000
                while(monitor.hits==0 && System.currentTimeMillis()<end)Thread.sleep(200)
                assertTrue("YIDT is delegated to a system-browser intent",monitor.hits>0)
                assertEquals("true",eval(web,"location.origin==='https://chat.yitiao.org'"))
            } finally { instrumentation.removeMonitor(monitor) }
            val screenshot=File(context.filesDir,"web-shell-offline.png")
            androidx.test.uiautomator.UiDevice.getInstance(instrumentation).takeScreenshot(screenshot)
        } finally { instrumentation.runOnMainSync { activity.finish() } }
    }
    @Test fun completeFrameworkIsBundledWithoutNetwork() {
        val cache=WebShellCache(context){false}
        val html=requireNotNull(cache.intercept(Uri.parse(WebRoutes.ORIGIN+"/"))).data.use { it.readBytes().decodeToString() }
        assertTrue(html.contains("yichat-app-bridge"))
        val files=context.assets.list("app-shell/assets")!!.toList()
        assertTrue(files.any { it.endsWith(".wasm") })
        for(name in files) {
            val response=requireNotNull(cache.intercept(Uri.parse(WebRoutes.ORIGIN+"/assets/"+name)))
            assertTrue(response.data.use { it.readBytes() }.isNotEmpty())
        }
        assertNull(cache.intercept(Uri.parse(WebRoutes.ORIGIN+"/api/session")))
    }
    @Test fun oldWebViewShowsActionableUpdateScreen() {
        assumeTrue("Only checks the legacy engine branch",!modernEngine())
        val activity=instrumentation.startActivitySync(Intent(context,MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as MainActivity
        try {
            val device=androidx.test.uiautomator.UiDevice.getInstance(instrumentation)
            assertTrue(device.wait(androidx.test.uiautomator.Until.hasObject(androidx.test.uiautomator.By.textContains("请更新 Android System WebView")),15000))
            assertTrue(device.hasObject(androidx.test.uiautomator.By.text("打开系统浏览器更新")))
            device.takeScreenshot(File(context.filesDir,"old-engine-update.png"))
            val monitor=instrumentation.addMonitor(IntentFilter(Intent.ACTION_VIEW).apply { addDataScheme("https");addCategory(Intent.CATEGORY_BROWSABLE) },Instrumentation.ActivityResult(Activity.RESULT_CANCELED,null),true)
            try {
                device.findObject(androidx.test.uiautomator.By.text("打开系统浏览器更新")).click()
                val end=System.currentTimeMillis()+8000
                while(monitor.hits==0 && System.currentTimeMillis()<end)Thread.sleep(200)
                assertTrue("Update action delegates to system browser",monitor.hits>0)
            } finally { instrumentation.removeMonitor(monitor) }
        } finally { instrumentation.runOnMainSync { activity.finish() } }
    }
    @Test fun automaticInternalBackupEncryptsPacketsAndPreservesLegacyNamespace()=runBlocking {
        val vault=SecureStore(context)
        vault.put("legacy-test-sentinel","keep original data".toByteArray())
        val a=Account("0".repeat(32),"isolated-backup","@isolated:chat.yitiao.org","TEST-WEB-DEVICE","never-network-token","never-export-proof")
        val db=Room.inMemoryDatabaseBuilder(context,YiChatDatabase::class.java).build()
        val store=object:PacketStore { override val account=a;override val database=db;override suspend fun refreshTombstones()=Unit }
        try {
            val raw="{\"event_id\":\"test-event\",\"room_id\":\"test-room\",\"type\":\"m.room.encrypted\",\"content\":{\"ciphertext\":\"isolated encrypted packet\"}}".toByteArray()
            db.local().addPacket(Packet("test-room","test-event",1,SecureStore.hash(raw.decodeToString()),vault.encrypt(a.storage,"packet:test-room:test-event",raw)))
            val history=HistoryRepository(store,Gateway(),vault)
            BackupRepository(context,store,history,vault).saveInternal(true)
            val file=File(context.noBackupFilesDir,"backups/${a.storage}/packets.enc")
            assertTrue(file.isFile)
            assertFalse(file.readBytes().decodeToString().contains("isolated encrypted packet"))
            val content=vault.decrypt(a.storage,"backup:packets",file.readBytes()).decodeToString()
            assertTrue(content.contains("yichat-app-packets"));assertFalse(content.contains(a.token));assertFalse(content.contains(a.proof))
            assertEquals("keep original data",vault.get("legacy-test-sentinel")?.decodeToString())
            assertNull(AuthRepository(vault,Gateway(),"web.").restore())
        } finally { db.close() }
    }
}
