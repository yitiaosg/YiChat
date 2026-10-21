package org.yitiao.yichat

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.work.*
import kotlinx.coroutines.CancellationException
import java.util.concurrent.TimeUnit
import kotlinx.serialization.json.jsonObject

/** Notifications contain no decrypted message or key material. No second Matrix sync. */
class Notifications(private val context:Context,private val graph:Graph) {
    @Volatile var foreground=false
    private val manager get()=context.getSystemService(NotificationManager::class.java)
    private fun enabledKey()=graph.auth.account?.let{"${it.storage}.notifications"}
    fun permitted()=(Build.VERSION.SDK_INT<33 || context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)==PackageManager.PERMISSION_GRANTED) && manager.areNotificationsEnabled()
    fun enabled()=enabledKey()?.let{graph.vault.get(it)?.decodeToString()=="1"}==true
    fun configure(value:Boolean) {
        val key=requireNotNull(enabledKey()){"请先登录"}
        graph.vault.put(key,(if(value)"1" else "0").toByteArray())
        if(value && permitted()) {
            manager.createNotificationChannel(NotificationChannel("messages","聊天消息",NotificationManager.IMPORTANCE_DEFAULT).apply{description="不显示聊天正文；归档及免打扰会话不通知"})
            WorkManager.getInstance(context).enqueueUniquePeriodicWork("yichat-notifications",ExistingPeriodicWorkPolicy.UPDATE,
                PeriodicWorkRequestBuilder<NotificationWorker>(15,TimeUnit.MINUTES).setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()).build())
        } else stop()
    }
    fun resume(){if(enabled())configure(true)}
    fun stop(){WorkManager.getInstance(context).cancelUniqueWork("yichat-notifications");manager.cancelAll()}
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
    suspend fun poll() {
        if(!enabled() || !permitted() || foreground)return
        val a=graph.auth.account?:return
        val key="${a.storage}.notification-cursor"
        repeat(2) {
            if(foreground || graph.auth.account?.token!=a.token)return
            val cursor=graph.vault.get(key)?.decodeToString()?.takeIf{it.matches(Regex("[0-9]{1,19}"))}
            val result=graph.gateway.call("/api/notifications?after="+(cursor?:"0")).jsonObject
            if(graph.auth.account?.token!=a.token || foreground)return
            for(row in result.a("rooms")){val item=row.jsonObject;show(item.s("room"),item.s("event"))}
            graph.vault.put(key,result.s("cursor").toByteArray())
            if(!result.b("has_more"))return
        }
    }
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
