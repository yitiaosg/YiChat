package org.yitiao.yichat

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.IBinder
import android.content.pm.ServiceInfo
import androidx.work.*
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.util.concurrent.TimeUnit
import kotlinx.serialization.json.jsonObject

/** Legacy metadata polling feeds the shared placeholder/content notification pipeline. */
class Notifications(private val context:Context,private val graph:Graph) {
    private val push get()=(context.applicationContext as YiChatApplication).push
    var foreground:Boolean
        get()=push.foreground
        set(value){push.foreground=value}
    private val polling=Mutex()
    private val manager get()=context.getSystemService(NotificationManager::class.java)
    private fun enabledKey()=graph.auth.account?.let{"${it.storage}.notifications"}
    fun permitted()=(Build.VERSION.SDK_INT<33 || context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)==PackageManager.PERMISSION_GRANTED) && manager.areNotificationsEnabled()
    fun enabled()=enabledKey()?.let{graph.vault.get(it)?.decodeToString()=="1"}==true
    fun configure(value:Boolean) {
        val key=requireNotNull(enabledKey()){"请先登录"}
        graph.vault.put(key,(if(value)"1" else "0").toByteArray())
        push.configure(value,graph.auth.account?.storage.orEmpty())
        PushRegistrationWorker.enqueue(context)
        if(value && permitted()) {
            push.channels()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork("yichat-notifications",ExistingPeriodicWorkPolicy.KEEP,
                PeriodicWorkRequestBuilder<NotificationWorker>(15,TimeUnit.MINUTES).setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()).build())
            // Start while an Activity is visible. A background worker must not
            // bypass Android's foreground-service start restrictions.
            if(foreground)startListener()
        } else stop()
    }
    fun resume(){if(enabled())configure(true)}
    fun startListener(){if(enabled()&&permitted()&&!registered())runCatching{context.startForegroundService(Intent(context,BackgroundNotificationService::class.java))}}
    private fun registered()=graph.auth.account?.let{graph.vault.get("${it.storage}.push-registered")?.decodeToString()=="1"}==true
    fun stop(){push.configure(false,graph.auth.account?.storage.orEmpty());context.stopService(Intent(context,BackgroundNotificationService::class.java));WorkManager.getInstance(context).cancelUniqueWork("yichat-notifications");manager.cancelAll()}
    fun cancel(room:String){push.cancel(room);manager.cancel(room,1)}
    fun show(room:String,event:String,label:String="Yichat",kind:String="private") {
        if(!enabled()||!permitted())return
        val hint=PushHint.parse(mapOf("conversation_id" to room,"conversation_type" to kind,"message_id" to event))?:return
        val a=graph.auth.account?:return
        val key="${a.storage}.notified.${SecureStore.hash(room)}"
        if(graph.vault.get(key)?.decodeToString()==event)return
        push.receive(hint);graph.vault.put(key,event.toByteArray())
    }
    suspend fun poll() = polling.withLock {
        if(!enabled() || !permitted())return@withLock
        val a=graph.auth.account?:return@withLock
        val key="${a.storage}.notification-cursor"
        repeat(2) {
            if(graph.auth.account?.token!=a.token)return@withLock
            val cursor=graph.vault.get(key)?.decodeToString()?.takeIf{it.matches(Regex("[0-9]{1,19}"))}
            val result=graph.gateway.call("/api/notifications?after="+(cursor?:"0")).jsonObject
            if(graph.auth.account?.token!=a.token)return@withLock
            for(row in result.a("rooms")){val item=row.jsonObject;show(item.s("room"),item.s("event"),kind=if(item.s("kind")=="group")"group" else "private")}
            graph.vault.put(key,result.s("cursor").toByteArray())
            if(!result.b("has_more"))return@withLock
        }
    }
}

/** User-enabled first-party notification listener; never consumes Matrix /sync
 * or to-device keys. Kept alive when the chat Activity is closed. */
class BackgroundNotificationService:Service(){
    private val scope=CoroutineScope(SupervisorJob()+Dispatchers.IO)
    private var listener:Job?=null
    override fun onBind(intent:Intent?):IBinder?=null
    override fun onCreate(){
        super.onCreate()
        val manager=getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("background_messages","后台消息连接",NotificationManager.IMPORTANCE_LOW))
        val pending=PendingIntent.getActivity(this,0,Intent(this,MainActivity::class.java),PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val notification=Notification.Builder(this,"background_messages").setSmallIcon(R.drawable.ic_notification).setContentTitle("YiChat 后台消息通知")
            .setContentText("正在接收消息通知，点击打开聊天").setContentIntent(pending).setOngoing(true).setOnlyAlertOnce(true).build()
        if(Build.VERSION.SDK_INT>=34)startForeground(2,notification,ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        else startForeground(2,notification)
    }
    override fun onStartCommand(intent:Intent?,flags:Int,startId:Int):Int {
        if(listener?.isActive!=true)listener=scope.launch {
            val graph=(application as YiChatApplication).graph
            try{
                graph.restore()
                while(isActive&&graph.auth.account!=null&&graph.notifications.enabled()&&graph.notifications.permitted()){
                    try{graph.notifications.poll()}
                    catch(e:CancellationException){throw e}
                    catch(e:GatewayError){if(e.status==401){graph.lock();break}}
                    catch(_:Exception){/* Network unavailable; retry without losing the cursor. */}
                    delay(15_000)
                }
            }finally{stopSelf()}
        }
        return START_STICKY
    }
    override fun onDestroy(){scope.cancel();super.onDestroy()}
}
class NotificationWorker(context:Context,params:WorkerParameters):CoroutineWorker(context,params){
    override suspend fun doWork():Result {
        val graph=(applicationContext as YiChatApplication).graph
        return try{graph.restore();graph.notifications.poll();Result.success()}
        catch(e:CancellationException){throw e}
        catch(e:GatewayError){if(e.status==401){graph.lock();Result.success()}else Result.retry()}
        catch(_:Exception){Result.retry()}
    }
}
