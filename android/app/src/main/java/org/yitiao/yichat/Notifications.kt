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

/** Notifications contain no decrypted message or key material. No second Matrix sync. */
class Notifications(private val context:Context,private val graph:Graph) {
    @Volatile var foreground=false
    private val polling=Mutex()
    private val manager get()=context.getSystemService(NotificationManager::class.java)
    private fun enabledKey()=graph.auth.account?.let{"${it.storage}.notifications"}
    fun permitted()=(Build.VERSION.SDK_INT<33 || context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)==PackageManager.PERMISSION_GRANTED) && manager.areNotificationsEnabled()
    fun enabled()=enabledKey()?.let{graph.vault.get(it)?.decodeToString()=="1"}==true
    fun configure(value:Boolean) {
        val key=requireNotNull(enabledKey()){"请先登录"}
        graph.vault.put(key,(if(value)"1" else "0").toByteArray())
        if(value && permitted()) {
            manager.createNotificationChannel(NotificationChannel("messages","聊天消息",NotificationManager.IMPORTANCE_DEFAULT).apply{description="不显示聊天正文；归档及免打扰会话不通知"})
            WorkManager.getInstance(context).enqueueUniquePeriodicWork("yichat-notifications",ExistingPeriodicWorkPolicy.KEEP,
                PeriodicWorkRequestBuilder<NotificationWorker>(15,TimeUnit.MINUTES).setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()).build())
            // Start while an Activity is visible. A background worker must not
            // bypass Android's foreground-service start restrictions.
            if(foreground)startListener()
        } else stop()
    }
    fun resume(){if(enabled())configure(true)}
    fun startListener(){if(enabled()&&permitted())runCatching{context.startForegroundService(Intent(context,BackgroundNotificationService::class.java))}}
    fun stop(){context.stopService(Intent(context,BackgroundNotificationService::class.java));WorkManager.getInstance(context).cancelUniqueWork("yichat-notifications");manager.cancelAll()}
    fun cancel(room:String){manager.cancel(room,1)}
    fun show(room:String,event:String,label:String="YiChat") {
        if(!enabled() || !permitted())return
        require(room.startsWith("!") && room.length<=255 && event.length<=255)
        val a=graph.auth.account?:return
        val key="${a.storage}.notified.${SecureStore.hash(room)}"
        if(graph.vault.get(key)?.decodeToString()==event)return
        val intent=Intent(context,MainActivity::class.java).putExtra("notification_room",room).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        val pending=PendingIntent.getActivity(context,room.hashCode(),intent,PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val notification=Notification.Builder(context,"messages").setSmallIcon(R.drawable.ic_notification).setContentTitle(label.take(80)).setContentText("收到新的聊天消息").setContentIntent(pending).setOnlyAlertOnce(true).setAutoCancel(true).setVisibility(Notification.VISIBILITY_PRIVATE).setCategory(Notification.CATEGORY_MESSAGE).build()
        manager.notify(room,1,notification);graph.vault.put(key,event.toByteArray())
    }
    suspend fun poll() = polling.withLock {
        if(!enabled() || !permitted() || foreground)return@withLock
        val a=graph.auth.account?:return@withLock
        val key="${a.storage}.notification-cursor"
        repeat(2) {
            if(foreground || graph.auth.account?.token!=a.token)return@withLock
            val cursor=graph.vault.get(key)?.decodeToString()?.takeIf{it.matches(Regex("[0-9]{1,19}"))}
            val result=graph.gateway.call("/api/notifications?after="+(cursor?:"0")).jsonObject
            if(graph.auth.account?.token!=a.token || foreground)return@withLock
            for(row in result.a("rooms")){val item=row.jsonObject;show(item.s("room"),item.s("event"))}
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
