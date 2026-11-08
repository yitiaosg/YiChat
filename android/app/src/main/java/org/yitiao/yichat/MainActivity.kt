package org.yitiao.yichat

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.Bundle
import android.app.AlertDialog
import android.webkit.*
import android.widget.*
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import android.view.View
import android.view.ViewGroup
import android.content.res.Configuration
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.browser.auth.AuthTabIntent
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.webkit.*
import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import java.io.File
import java.util.UUID

class MainActivity : ComponentActivity() {
    private val graph get() = (application as YiChatApplication).graph
    private var safeRoot: FrameLayout? = null
    private lateinit var web: WebView
    private lateinit var cache: WebShellCache
    private var chooser: ValueCallback<Array<Uri>>? = null
    private var microphoneRequest: PermissionRequest? = null
    private var callbackDigest: String? = null
    private data class Reply(val id: String, val proxy: JavaScriptReplyProxy)
    private data class ExportConfig(val account: String, val password: String, val days: Int, val reply: Reply)
    private var exportConfig: ExportConfig? = null
    private data class Download(val file: File, val name: String, val mime: String, val expected: Long)
    private val downloads = mutableMapOf<String,Download>()
    private var saving: Pair<Download,Reply>? = null
    private val authWindow = AuthTabIntent.registerActivityResultLauncher(this) { result ->
        if(result.resultCode==AuthTabIntent.RESULT_OK) result.resultUri?.let(::consumeCallback)
        else if(result.resultCode in listOf(AuthTabIntent.RESULT_VERIFICATION_FAILED,AuthTabIntent.RESULT_VERIFICATION_TIMED_OUT))
            toast("浏览器未能验证登录回跳，请更新系统浏览器后重试")
    }
    private val chooseFile = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        chooser?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result.resultCode,result.data));chooser=null
    }
    private val microphone = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        microphoneRequest?.let { if(granted) it.grant(arrayOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE)) else it.deny() };microphoneRequest=null
    }
    private val directoryPicker = registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        val config=exportConfig;exportConfig=null
        if(config!=null) lifecycleScope.launch {
            try {
                if(uri==null) respond(config.reply,buildJsonObject { put("cancelled",true) })
                else {
                    require(graph.auth.account?.storage==config.account) { "账号已锁定或改变，请重新配置导出" }
                    contentResolver.takePersistableUriPermission(uri,Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
                    withContext(Dispatchers.IO) { graph.backup.configure(uri,config.password,config.days) }
                    respond(config.reply,backupStatus())
                }
            } catch(e:Exception) { reject(config.reply,e) }
        }
    }
    private val saveDocument = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val pending=saving;saving=null
        if(pending!=null) lifecycleScope.launch {
            val (download,reply)=pending
            try {
                val uri=result.data?.data?.takeIf { result.resultCode==Activity.RESULT_OK }
                if(uri==null) respond(reply,buildJsonObject { put("cancelled",true) })
                else {
                    withContext(Dispatchers.IO) { download.file.inputStream().use { input -> requireNotNull(contentResolver.openOutputStream(uri,"wt")).use { output -> input.copyTo(output) } } }
                    respond(reply,buildJsonObject { put("saved",true) })
                }
            } catch(e:Exception) { reject(reply,e) }
            finally { download.file.delete() }
        }
    }
    private var notificationRoom:String?=null
    private val notificationPermission=registerForActivityResult(ActivityResultContracts.RequestPermission()){ granted ->
        if(granted)graph.notifications.resume() else toast("通知权限未开启，可在系统设置中开启")
    }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        callbackDigest=savedInstanceState?.getString("callback-digest")
        // Old native databases/keys remain in no_backup. Only abandoned temporary downloads are removed.
        File(cacheDir,"web-downloads").deleteRecursively()
        val major=WebViewCompat.getCurrentWebViewPackage(this)?.versionName?.substringBefore('.')?.toIntOrNull() ?: 0
        if(!WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER) || major<100) {
            val box=LinearLayout(this).apply { orientation=LinearLayout.VERTICAL;setPadding(32,64,32,32) }
            box.addView(TextView(this).apply { text="请更新 Android System WebView\n聊天需要新版浏览器内核，以支持持久加密、Web Locks 和离线存储。";textSize=18f })
            box.addView(Button(this).apply { text="打开系统浏览器更新";setOnClickListener { external(Uri.parse("https://play.google.com/store/apps/details?id=com.google.android.webview")) } })
            setSafeContent(box);return
        }
        cache=WebShellCache(this,::online)
        web=WebView(this)
        web.settings.apply {
            javaScriptEnabled=true;domStorageEnabled=true;databaseEnabled=true
            allowFileAccess=false;allowContentAccess=true; // Required for user-selected SAF upload URIs. Top-level content/file navigation stays blocked.
            mixedContentMode=WebSettings.MIXED_CONTENT_NEVER_ALLOW
            setSupportMultipleWindows(true);javaScriptCanOpenWindowsAutomatically=false
            mediaPlaybackRequiresUserGesture=true
        }
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
        CookieManager.getInstance().setAcceptThirdPartyCookies(web,false)
        WebViewCompat.addWebMessageListener(web,"YiChatApp",setOf(WebRoutes.ORIGIN)) { _,message,origin,mainFrame,proxy ->
            if(mainFrame && origin.toString().trimEnd('/')==WebRoutes.ORIGIN) receive(message.data,proxy)
        }
        web.webViewClient=object:WebViewClient() {
            override fun shouldInterceptRequest(view:WebView,request:WebResourceRequest):WebResourceResponse? {
                if(request.method!="GET")return null
                if(WebRoutes.trusted(request.url.toString()) && request.url.path.orEmpty().startsWith("/app-local-record-files/"))
                    return runCatching{graph.recordFiles.open(request.url.lastPathSegment.orEmpty())}.getOrNull()
                return cache.intercept(request.url)
            }
            override fun shouldOverrideUrlLoading(view:WebView,request:WebResourceRequest):Boolean {
                if(!request.isForMainFrame) return !WebRoutes.trusted(request.url.toString())
                return navigate(request.url)
            }
            override fun onReceivedSslError(view:WebView,handler:SslErrorHandler,error:android.net.http.SslError) { handler.cancel();toast("HTTPS 证书验证失败，已拒绝连接") }
            override fun onReceivedError(view:WebView,request:WebResourceRequest,error:WebResourceError) {
                if(WebRoutes.publicAsset(request.url.path.orEmpty()) && !cache.bundledOnly) {
                    cache.bundledOnly=true;view.post { view.reload() }
                }
            }
        }
        web.webChromeClient=object:WebChromeClient() {
            override fun onShowFileChooser(view:WebView,callback:ValueCallback<Array<Uri>>,params:FileChooserParams):Boolean {
                chooser?.onReceiveValue(null);chooser=callback
                return runCatching { chooseFile.launch(params.createIntent());true }.getOrElse { chooser=null;callback.onReceiveValue(null);false }
            }
            override fun onPermissionRequest(request:PermissionRequest) {
                if(request.origin.toString().trimEnd('/')!=WebRoutes.ORIGIN || request.resources.any { it!=PermissionRequest.RESOURCE_AUDIO_CAPTURE }) { request.deny();return }
                microphoneRequest?.deny();microphoneRequest=request
                if(checkSelfPermission(Manifest.permission.RECORD_AUDIO)==PackageManager.PERMISSION_GRANTED) { request.grant(arrayOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE));microphoneRequest=null }
                else microphone.launch(Manifest.permission.RECORD_AUDIO)
            }
            override fun onPermissionRequestCanceled(request:PermissionRequest) { if(microphoneRequest===request) microphoneRequest=null }
            override fun onCreateWindow(view:WebView,isDialog:Boolean,isUserGesture:Boolean,message:android.os.Message):Boolean {
                if(!isUserGesture) return false
                val child=WebView(this@MainActivity)
                child.webViewClient=object:WebViewClient() {
                    override fun shouldOverrideUrlLoading(v:WebView,r:WebResourceRequest):Boolean {
                        if(r.url.scheme in listOf("https","http")) { external(r.url);v.destroy() };return true
                    }
                }
                (message.obj as WebView.WebViewTransport).webView=child;message.sendToTarget();return true
            }
        }
        web.setDownloadListener { _,_,_,_,_ -> toast("请使用聊天中的下载按钮保存文件") }
        setSafeContent(web)
        onBackPressedDispatcher.addCallback(this,object:OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                web.evaluateJavascript("(()=>{const close=document.querySelector('.modal header button[aria-label=关闭]');if(close){close.click();return true}const back=document.querySelector('.conversation .mobile-back');if(back){back.click();return true}return false})()") { handled -> if(handled!="true") finish() }
            }
        })
        (application as YiChatApplication).push.refreshVisible={web.post {
            web.evaluateJavascript("window.dispatchEvent(new Event('yc:notification-sync'))",null)
        }}
        graph.notificationContent.attach { id,hint ->
            val request=buildJsonObject{put("id",id);put("room",hint.room);put("kind",hint.kind);put("event",hint.event)}
            web.evaluateJavascript("window.dispatchEvent(new CustomEvent('yc:notification-fetch',{detail:"+request.toString()+"}))",null)
        }
        web.loadUrl(WebRoutes.ORIGIN+"/")
        consumeIntent(intent)
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while(isActive) {
                    withContext(Dispatchers.IO) {
                        runCatching { graph.restore();if(graph.auth.account!=null) { if(online()) { graph.history.catchUp();graph.history.exchange() };graph.backup.saveInternal() } }
                    }
                    delay(60000)
                }
            }
        }
    }
    private fun setSafeContent(content: View) {
        WindowCompat.setDecorFitsSystemWindows(window,false)
        val dark=resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK==Configuration.UI_MODE_NIGHT_YES
        val root=FrameLayout(this).apply { setBackgroundColor(if(dark) 0xff121f2d.toInt() else android.graphics.Color.WHITE) };safeRoot=root
        root.addView(content,FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,ViewGroup.LayoutParams.MATCH_PARENT))
        val controller=WindowInsetsControllerCompat(window,root)
        controller.isAppearanceLightStatusBars=!dark;controller.isAppearanceLightNavigationBars=!dark
        ViewCompat.setOnApplyWindowInsetsListener(root) { v,insets ->
            val safe=insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            val ime=insets.getInsets(WindowInsetsCompat.Type.ime())
            v.setPadding(safe.left,safe.top,safe.right,maxOf(safe.bottom,ime.bottom))
            WindowInsetsCompat.CONSUMED
        }
        setContentView(root);ViewCompat.requestApplyInsets(root)
    }
    override fun onNewIntent(intent:Intent) { super.onNewIntent(intent);consumeIntent(intent) }
    private fun consumeIntent(incoming:Intent) { val uri=incoming.data;incoming.data=null;incoming.getStringExtra("notification_room")?.takeIf{it.startsWith("!") && it.length<=255}?.let{room->notificationRoom=room;if(::web.isInitialized && WebRoutes.chat(web.url.orEmpty()))web.evaluateJavascript("window.dispatchEvent(new CustomEvent('yc:open-room',{detail:"+JsonPrimitive(room).toString()+"}))",null)};incoming.removeExtra("notification_room");setIntent(incoming);uri?.let(::consumeCallback) }
    private fun consumeCallback(uri:Uri) {
        val hash=SecureStore.hash(uri.toString());if(hash==callbackDigest)return;callbackDigest=hash
        lifecycleScope.launch {
            try {
                val account=withContext(Dispatchers.IO) { graph.auth.callback(uri) }
                if(account!=null) withContext(Dispatchers.IO) { graph.activate(account) }
                val order=uri.getQueryParameter("order")?.takeIf { uri.path=="/app/return" && uri.queryParameterNames==setOf("order") && uri.getQueryParameters("order").size==1 && it.matches(Regex("[A-Za-z0-9_-]{32}")) }
                val deposit=uri.getQueryParameter("wallet_deposit")?.takeIf { uri.path=="/app/return" && uri.queryParameterNames==setOf("wallet_deposit") && uri.getQueryParameters("wallet_deposit").size==1 && it.matches(Regex("[A-Za-z0-9_-]{32}")) }
                if(::web.isInitialized) web.loadUrl(WebRoutes.ORIGIN+"/"+(order?.let { "?order=$it" } ?: deposit?.let { "?wallet_deposit=$it" } ?: ""))
            } catch(e:Exception) { toast(safeError(e)) }
        }
    }
    override fun onSaveInstanceState(out:Bundle) { callbackDigest?.let { out.putString("callback-digest",it) };super.onSaveInstanceState(out) }
    private var updateDialog:AlertDialog?=null
    private var updateCheck:Job?=null
    /** Compares with the latest GitHub release; the browser downloads and installs the APK. */
    private fun checkForUpdate(manual:Boolean,reply:Reply?=null) {
        if(updateCheck?.isActive==true && !manual) return
        if(!online()) { reply?.let { respond(it,buildJsonObject { put("current",BuildConfig.VERSION_NAME);put("available",false);put("error","当前离线") }) };return }
        updateCheck=lifecycleScope.launch {
            val result=runCatching { graph.updates.check(force=manual) }
            val release=result.getOrNull()
            reply?.let { r -> respond(r,buildJsonObject { put("current",BuildConfig.VERSION_NAME);put("available",release!=null);put("latest",release?.versionName?.let(::JsonPrimitive) ?: JsonNull);result.exceptionOrNull()?.let { put("error","无法连接 GitHub 检查更新") } }) }
            if(release==null) return@launch
            if(!manual && graph.updates.snoozed(release)) return@launch
            showUpdate(release)
        }
    }
    private fun showUpdate(release:AppRelease) {
        if(isFinishing || isDestroyed || updateDialog?.isShowing==true) return
        val notes=release.notes.lines().map { it.trim().removePrefix("#").trim() }.filter { it.isNotEmpty() }.take(6).joinToString("\n")
        updateDialog=AlertDialog.Builder(this)
            .setTitle("发现新版本 ${release.versionName}")
            .setMessage("当前版本 ${BuildConfig.VERSION_NAME}。将在浏览器中通过 GitHub 下载安装包，下载完成后点击安装即可覆盖升级，聊天和备份数据保留。"+(if(notes.isNotEmpty()) "\n\n"+notes else ""))
            .setPositiveButton("下载并安装") { _,_ -> downloadUpdate(release) }
            .setNegativeButton("稍后") { _,_ -> graph.updates.snooze(release) }
            .setNeutralButton("发布说明") { _,_ -> openUpdateLink(release.pageUrl) }
            .setOnCancelListener { graph.updates.snooze(release) }
            .show()
    }
    private fun downloadUpdate(release:AppRelease) {
        if(!ReleaseInfo.trustedApk(release.apkUrl)) { toast("更新链接无效");return }
        graph.updates.snooze(release) // Do not re-prompt immediately when returning from the browser.
        openUpdateLink(release.apkUrl)
        toast("浏览器下载完成后，打开安装包并允许安装以完成更新")
    }
    private fun openUpdateLink(url:String) {
        if(!ReleaseInfo.trustedApk(url) && !ReleaseInfo.trustedPage(url)) return
        // A plain VIEW intent lets the default browser handle the APK download and hand it to the installer.
        // Target the default web browser so a GitHub client app cannot claim the link.
        val view=Intent(Intent.ACTION_VIEW,Uri.parse(url)).addCategory(Intent.CATEGORY_BROWSABLE).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val probe=Intent(Intent.ACTION_VIEW,Uri.parse("https://")).addCategory(Intent.CATEGORY_BROWSABLE)
        packageManager.resolveActivity(probe,PackageManager.MATCH_DEFAULT_ONLY)?.activityInfo?.packageName?.takeIf { it!="android" }?.let(view::setPackage)
        runCatching { startActivity(view) }.recoverCatching { BrowserNavigator.external(this,Uri.parse(url)) }.onFailure { toast("无法打开系统浏览器下载更新") }
    }
    private fun navigate(uri:Uri):Boolean {
        if(WebRoutes.chat(uri.toString())) return false
        if(WebRoutes.trusted(uri.toString()) && uri.path=="/auth/login") { lifecycleScope.launch { runCatching { login() }.onFailure { toast(safeError(it)) } };return true }
        external(uri);return true
    }
    private suspend fun login(resume: Boolean = true) {
        val uri=withContext(Dispatchers.IO) { graph.completePendingLogout();graph.auth.begin(resume) }
        BrowserNavigator.open(this,uri,authWindow)
    }
    private fun external(uri:Uri) { runCatching { BrowserNavigator.external(this,uri) }.onFailure { toast("无法打开系统浏览器或此链接不受支持") } }
    fun online():Boolean {
        val manager=getSystemService(ConnectivityManager::class.java)
        return manager.getNetworkCapabilities(manager.activeNetwork)?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)==true
    }
    private fun toast(message:String) { Toast.makeText(this,message,Toast.LENGTH_LONG).show() }
    private fun safeError(error:Throwable) = when(error) { is GatewayError->error.reason;is IllegalArgumentException->error.message ?: "参数无效";else->"操作未完成，请检查登录、网络、目录授权和存储空间" }
    private fun respond(reply:Reply,value:JsonElement=JsonNull) { reply.proxy.postMessage(buildJsonObject { put("id",reply.id);put("ok",true);put("value",value) }.toString()) }
    private fun reject(reply:Reply,error:Throwable) { reply.proxy.postMessage(buildJsonObject { put("id",reply.id);put("ok",false);put("error",safeError(error)) }.toString()) }
    private suspend fun backupStatus():JsonObject = withContext(Dispatchers.IO) {
        buildJsonObject { put("status",graph.backup.status.value);put("exportStatus",graph.backup.exportStatus.value);put("days",graph.backup.exportDays.value?.let(::JsonPrimitive) ?: JsonNull);put("packets",graph.store.database?.local()?.packetCount() ?: 0) }
    }
    private fun receive(raw:String?,proxy:JavaScriptReplyProxy) {
        if(raw==null || raw.length>400000)return
        val data=runCatching { json.parseToJsonElement(raw).jsonObject }.getOrNull() ?: return
        val id=data.s("id");if(!id.matches(Regex("[A-Za-z0-9_-]{1,64}")))return
        val reply=Reply(id,proxy)
        lifecycleScope.launch {
            try {
                val args=data.o("args")
                when(data.s("method")) {
                    "recordFileAvailable" -> respond(reply,buildJsonObject { put("available",graph.recordFiles.available(args.s("id"))) })
                    "systemThemeColor" -> {
                        val color=if(android.os.Build.VERSION.SDK_INT>=31) getColor(android.R.color.system_accent1_500) else 0xff1479b8.toInt()
                        respond(reply,buildJsonObject { put("color",String.format("#%06x",color and 0xffffff)) })
                    }
                    "setChromeColor" -> {
                        val seed=args.s("seed");require(seed.matches(Regex("#[0-9a-fA-F]{6}")))
                        val dark=args.b("dark");val base=if(dark) 0xff101820.toInt() else android.graphics.Color.WHITE
                        val color=androidx.core.graphics.ColorUtils.blendARGB(base,android.graphics.Color.parseColor(seed),if(dark) .04f else .01f)
                        safeRoot?.setBackgroundColor(color)
                        WindowInsetsControllerCompat(window,safeRoot?:web).apply { isAppearanceLightStatusBars=!dark;isAppearanceLightNavigationBars=!dark }
                        respond(reply)
                    }
                    "recordRooms", "recordDays", "recordPage" -> {
                        val active=graph.auth.account?.token;requireNotNull(active){"请先登录"}
                        val value=when(data.s("method")) {
                            "recordRooms"->graph.records.rooms()
                            "recordDays"->graph.records.days(args.s("room"),args.s("start"),args.s("end"))
                            else->graph.records.page(args.s("room"),args.s("day"),args.s("after").ifBlank{"0"})
                        }
                        require(graph.auth.account?.token==active){"会话已锁定"};respond(reply,value)
                    }
                    "localOlder" -> {
                        val active=graph.auth.account?.token;requireNotNull(active){"请先登录"}
                        val value=graph.localTimeline.older(args.s("room"),args.s("cursor"),args["before_ts"]?.jsonPrimitive?.longOrNull ?: Long.MAX_VALUE,args["limit"]?.jsonPrimitive?.intOrNull ?: LocalTimeline.PAGE)
                        require(graph.auth.account?.token==active){"会话已锁定"};respond(reply,value)
                    }
                    "checkUpdate" -> checkForUpdate(true,reply)
                    "session" -> {
                        val a=withContext(Dispatchers.IO) { graph.restore() }
                        respond(reply,buildJsonObject { put("online",online());put("version",BuildConfig.VERSION_NAME);put("capabilities",buildJsonObject{put("notifications",true);put("backgroundNotifications",true)});put("pendingLogout",graph.vault.get("web.pending-logout")!=null);put("session",a?.let { buildJsonObject {
                            put("uid",it.uid);put("user",it.user);put("mxid",it.mxid);put("device",it.device);put("device_id",it.device);put("token",it.token)
                        } } ?: JsonNull) })
                    }
                    "login" -> { login(!args.b("newDevice"));respond(reply) }
                    "lock" -> { withContext(Dispatchers.IO) { graph.lock() };respond(reply) }
                    "logout" -> { withContext(Dispatchers.IO) { graph.logout() };respond(reply,buildJsonObject { put("pending",graph.vault.get("web.pending-logout")!=null) }) }
                    "notificationStatus" -> respond(reply,buildJsonObject{put("enabled",graph.notifications.enabled());put("permitted",graph.notifications.permitted());put("background_supported",true)})
                    "configureNotifications" -> { val enabled=args.b("enabled");graph.notifications.configure(enabled);if(enabled && android.os.Build.VERSION.SDK_INT>=33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS);respond(reply,buildJsonObject{put("enabled",graph.notifications.enabled());put("permitted",graph.notifications.permitted());put("background_supported",true)}) }
                    "backgroundSettings" -> { startActivity(Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS,Uri.parse("package:"+packageName)));respond(reply) }
                    "notificationSettings" -> { startActivity(Intent(android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(android.provider.Settings.EXTRA_APP_PACKAGE,packageName));respond(reply) }
                    "notificationActiveRoom" -> { val room=args.s("room");require(room.isEmpty()||(room.startsWith("!")&&room.length<=255));(application as YiChatApplication).push.activeRoom=room;respond(reply) }
                    "notificationNames" -> { (application as YiChatApplication).push.cacheNames(args.a("rooms").take(500).map{val row=it.jsonObject;row.s("room") to row.s("label")});respond(reply) }
                    "notificationResult" -> { graph.notificationContent.complete(args.s("id"),args);respond(reply) }
                    "notifyRoom" -> { graph.notifications.show(args.s("room"),args.s("event"),args.s("label"),args.s("kind").ifBlank{"private"});respond(reply) }
                    "cancelNotification" -> { graph.notifications.cancel(args.s("room"));respond(reply) }
                    "pendingNotification" -> { respond(reply,buildJsonObject{put("room",notificationRoom?:"")});notificationRoom=null }
                    "backupStatus" -> respond(reply,backupStatus())
                    "backupNow" -> { withContext(Dispatchers.IO) { graph.backup.saveInternal(true) };respond(reply,backupStatus()) }
                    "exportNow" -> { withContext(Dispatchers.IO) { graph.backup.export() };respond(reply,backupStatus()) }
                    "stopExport" -> { withContext(Dispatchers.IO) { graph.backup.stopExport() };respond(reply,backupStatus()) }
                    "configureExport" -> {
                        require(exportConfig==null && saving==null) { "请先完成当前文件选择" }
                        val days=args["days"]?.jsonPrimitive?.intOrNull ?: 0;val password=args.s("password")
                        require(days in 1..3650 && password.length in 12..1024) { "请输入1至3650个整天和至少12字符的独立口令" }
                        exportConfig=ExportConfig(requireNotNull(graph.auth.account).storage,password,days,reply);directoryPicker.launch(null)
                    }
                    "saveStart" -> {
                        require(downloads.isEmpty() && saving==null) { "请先完成当前下载" }
                        val bytes=args["bytes"]?.jsonPrimitive?.longOrNull ?: -1
                        require(bytes in 0..100000000) { "实际下载文件上限100MB" }
                        val name=args.s("name").replace(Regex("[\\\\/\\p{Cntrl}]"),"_").take(120).ifBlank { "YiChat-download" }
                        val mime=args.s("mime").takeIf { it.matches(Regex("[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+")) } ?: "application/octet-stream"
                        val key=UUID.randomUUID().toString();val dir=File(cacheDir,"web-downloads").apply { mkdirs() }
                        downloads[key]=Download(File(dir,key).apply { createNewFile() },name,mime,bytes)
                        respond(reply,obj("transfer" to key))
                    }
                    "saveChunk" -> {
                        val d=requireNotNull(downloads[args.s("transfer")]);val encoded=args.s("data");require(encoded.length<=65536)
                        val bytes=android.util.Base64.decode(encoded,android.util.Base64.NO_WRAP)
                        require(d.file.length()+bytes.size<=minOf(d.expected,100000000)) { "文件大小与声明不一致" }
                        d.file.appendBytes(bytes);respond(reply)
                    }
                    "saveCancel" -> { downloads.remove(args.s("transfer"))?.file?.delete();respond(reply) }
                    "saveFinish" -> {
                        require(saving==null && exportConfig==null) { "请先完成当前文件选择" }
                        val d=requireNotNull(downloads.remove(args.s("transfer")));require(d.file.length()==d.expected) { "文件未完整传输" }
                        saving=d to reply
                        saveDocument.launch(Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType(d.mime).putExtra(Intent.EXTRA_TITLE,d.name))
                    }
                    else -> throw IllegalArgumentException("不支持此 App 操作")
                }
            } catch(e:CancellationException) { throw e }
            catch(e:Exception) { reject(reply,e) }
        }
    }
    override fun onPause() { graph.notifications.foreground=false; if(::web.isInitialized){web.evaluateJavascript("window.dispatchEvent(new Event('yc:background'))",null);web.onPause()};super.onPause() }
    override fun onResume() { super.onResume();if(::web.isInitialized)checkForUpdate(false);graph.notifications.foreground=true;if(::web.isInitialized)web.onResume();graph.notifications.startListener();lifecycleScope.launch { runCatching { withContext(Dispatchers.IO){graph.restore()} }.onSuccess { account -> if(account==null && ::web.isInitialized && WebRoutes.chat(web.url.orEmpty()))web.evaluateJavascript("window.dispatchEvent(new Event('yc:app-session-locked'))",null) } } }
    override fun onDestroy() {
        chooser?.onReceiveValue(null);microphoneRequest?.deny()
        downloads.values.forEach { it.file.delete() };saving?.first?.file?.delete()
        updateDialog?.dismiss();graph.notificationContent.detach();(application as YiChatApplication).push.activeRoom="";(application as YiChatApplication).push.refreshVisible=null
        if(::web.isInitialized) { WebViewCompat.removeWebMessageListener(web,"YiChatApp");web.destroy() }
        super.onDestroy()
    }
}
