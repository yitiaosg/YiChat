package org.yitiao.yichat

import android.content.Context
import android.webkit.*
import androidx.webkit.WebViewCompat
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.*
import java.util.UUID

/** Shares the UI's existing JS SDK. A headless WebView uses the identical origin,
 * IndexedDB, Rust crypto module and yc:active-client Web Lock when no UI exists.
 * Never creates a native Matrix device or a second native /sync consumer. */
class NotificationContent(private val context:Context,private val graph:Graph) {
    private val serial=Mutex()
    private val pending=mutableMapOf<String,CompletableDeferred<List<NoticeMessage>>>()
    private var host:((String,PushHint)->Unit)?=null
    private var preempt:(()->Unit)?=null
    fun attach(value:(String,PushHint)->Unit){preempt?.invoke();host=value}
    fun detach(){host=null}
    fun complete(id:String,data:JsonObject) {
        val result=pending[id]?:return
        if(!data.b("ok")){result.completeExceptionally(IllegalStateException("Notification unavailable"));return}
        val messages=data.a("messages").takeLast(20).map{it.jsonObject}.map {
            require(it.s("id").startsWith("$")&&it.s("id").length<=255)
            NoticeMessage(it.s("id"),it.s("sender").take(80),it.s("body").take(2000),it["time"]?.jsonPrimitive?.longOrNull?:0)
        }
        require(messages.isNotEmpty())
        result.complete(messages)
    }
    suspend fun fetch(hint:PushHint):List<NoticeMessage> = serial.withLock {
        withContext(Dispatchers.Main.immediate) {
            val id=UUID.randomUUID().toString();val result=CompletableDeferred<List<NoticeMessage>>()
            pending[id]=result
            var headless:WebView?=null
            try {
                val ui=host
                if(ui!=null)ui(id,hint)
                else {
                    preempt={result.completeExceptionally(IllegalStateException("UI resumed"))}
                    val cache=WebShellCache(context){false}.apply{bundledOnly=true}
                    headless=WebView(context).apply {
                        settings.javaScriptEnabled=true;settings.domStorageEnabled=true;settings.databaseEnabled=true
                        settings.allowFileAccess=false;settings.allowContentAccess=false
                        settings.mixedContentMode=WebSettings.MIXED_CONTENT_NEVER_ALLOW
                        WebViewCompat.addWebMessageListener(this,"YiChatApp",setOf(WebRoutes.ORIGIN)){_,message,origin,mainFrame,reply->
                            if(!mainFrame||origin.toString().trimEnd('/')!=WebRoutes.ORIGIN)return@addWebMessageListener
                            try {
                                val raw=message.data?:return@addWebMessageListener
                                require(raw.length<=100_000)
                                val data=json.parseToJsonElement(raw).jsonObject
                                val value=when(data.s("method")) {
                                    "session"->buildJsonObject {
                                        put("online",true);put("headless",true)
                                        put("session",graph.auth.account?.let{a->buildJsonObject{put("uid",a.uid);put("user",a.user);put("mxid",a.mxid);put("device_id",a.device);put("token",a.token)}}?:JsonNull)
                                    }
                                    "lock"->{graph.scope.launch{graph.lock()};JsonNull}
                                    "notificationRequest"->buildJsonObject{put("id",id);put("room",hint.room);put("kind",hint.kind);put("event",hint.event)}
                                    "notificationResult"->{complete(data.o("args").s("id"),data.o("args"));JsonNull}
                                    else->throw IllegalArgumentException("Unsupported background bridge method")
                                }
                                reply.postMessage(buildJsonObject{put("id",data.s("id"));put("ok",true);put("value",value)}.toString())
                            }catch(_:Exception){result.completeExceptionally(IllegalStateException("Background bridge failed"))}
                        }
                        webViewClient=object:WebViewClient(){
                            override fun shouldInterceptRequest(view:WebView,request:WebResourceRequest)=if(request.method=="GET")cache.intercept(request.url) else null
                            override fun shouldOverrideUrlLoading(view:WebView,request:WebResourceRequest)=true
                            override fun onReceivedSslError(view:WebView,handler:SslErrorHandler,error:android.net.http.SslError){handler.cancel();result.completeExceptionally(IllegalStateException("TLS rejected"))}
                        }
                        loadUrl(WebRoutes.ORIGIN+"/notification.html")
                    }
                }
                result.await()
            }finally{
                preempt=null
                pending.remove(id)
                headless?.let { web->withContext(NonCancellable){
                    // Wait for JS to close its Rust OlmMachine and release the
                    // Web Lock before destroying the document, including timeout.
                    withTimeoutOrNull(2_000){suspendCancellableCoroutine<Unit>{continuation->
                        web.evaluateJavascript("window.dispatchEvent(new Event('yc:notification-close'))"){
                            if(continuation.isActive)continuation.resumeWith(kotlin.Result.success(Unit))
                        }
                    }}
                    web.loadUrl("about:blank");web.destroy()
                }}
            }
        }
    }
}
