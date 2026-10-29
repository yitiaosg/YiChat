package org.yitiao.yichat

import android.Manifest
import android.app.*
import android.content.*
import android.content.pm.PackageManager
import android.os.Build
import android.os.UserManager
import android.util.Log
import androidx.work.*
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import java.util.UUID

/** This object can be constructed before unlock. It MUST NOT instantiate Graph. */
class PushNotifications(private val context:Context) {
    private val device=context.createDeviceProtectedStorageContext()
    // Display names only. No account tokens, messages, key material or crypto DBs.
    private val names=device.getSharedPreferences("notification-names",Context.MODE_PRIVATE)
    private val settings=device.getSharedPreferences("notification-consent",Context.MODE_PRIVATE)
    private val cancelled=mutableSetOf<String>()
    private val manager get()=context.getSystemService(NotificationManager::class.java)
    val unlocked get()=context.getSystemService(UserManager::class.java).isUserUnlocked
    fun permitted()=(Build.VERSION.SDK_INT<33||context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)==PackageManager.PERMISSION_GRANTED)&&manager.areNotificationsEnabled()
    fun enabled()=settings.getBoolean("enabled",false)
    fun account()=settings.getString("account","").orEmpty() // Non-secret account/device fence.
    @Synchronized fun configure(enabled:Boolean,account:String) {
        if(account()!=account){cancelled.clear();names.edit().clear().commit();manager.cancelAll();synchronized(history){history.clear()}}
        settings.edit().putBoolean("enabled",enabled).putString("account",account).commit()
        if(!enabled){cancelled.clear();synchronized(history){history.clear()};names.edit().clear().commit();manager.cancelAll();WorkManager.getInstance(context).cancelAllWorkByTag("notification-content")}
    }
    fun cacheNames(rows:List<Pair<String,String>>) {
        names.edit().clear().apply { rows.take(500).forEach{(room,label)->putString(SecureStore.hash(room),label.take(80))} }.apply()
    }
    @Volatile var foreground=false
    @Volatile var activeRoom=""
    @Volatile var refreshVisible:(()->Unit)?=null
    fun viewing(room:String)=NotificationPolicy.suppress(foreground,activeRoom,room)
    fun channels() {
        listOf("private" to "私聊消息","group" to "群聊消息").forEach{(id,label)->
            manager.createNotificationChannel(NotificationChannel("messages_$id",label,NotificationManager.IMPORTANCE_HIGH))
        }
    }
    private fun pending(hint:PushHint)=PendingIntent.getActivity(context,hint.notificationId,
        Intent(context,MainActivity::class.java).setAction(hint.tag).putExtra("notification_room",hint.room)
            .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP),PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    private fun builder(hint:PushHint):Notification.Builder {
        channels()
        val public=Notification.Builder(context,"messages_${hint.kind}").setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(NotificationPolicy.TITLE).setContentText(NotificationPolicy.PLACEHOLDER).setVisibility(Notification.VISIBILITY_PUBLIC).build()
        return Notification.Builder(context,"messages_${hint.kind}").setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(NotificationPolicy.title(names.getString(SecureStore.hash(hint.room),null)))
            .setContentText(NotificationPolicy.initialText(hint,unlocked)).setContentIntent(pending(hint)).setAutoCancel(true)
            .setVisibility(Notification.VISIBILITY_PRIVATE).setPublicVersion(public).setCategory(Notification.CATEGORY_MESSAGE)
            .setGroup(hint.tag)
    }
    private fun current(hint:PushHint)=manager.activeNotifications.firstOrNull{it.tag==hint.tag&&it.id==hint.notificationId}
    @Synchronized fun needsContent(hint:PushHint,generation:String):Boolean {
        if(hint.room in cancelled)return false
        val notification=current(hint)?.notification?:return false
        return notification.extras.getString("yc.generation")==generation&&
            notification.extras.getString(Notification.EXTRA_TEMPLATE)!="android.app.Notification\$MessagingStyle"
    }
    @Synchronized fun cancel(room:String){
        // NotificationManager processes cancel asynchronously. Fence in-flight
        // results immediately rather than trusting a temporarily stale OS query.
        cancelled.add(room);while(cancelled.size>64)cancelled.remove(cancelled.first())
        manager.cancel("conversation:$room",room.hashCode());synchronized(history){history.remove(room)}
    }
    /** Synchronous disk/cache + notify only. No networking, WorkManager or crypto here. */
    @Synchronized fun placeholder(hint:PushHint):String? {
        if(!NotificationPolicy.accepts(hint,account()))return null
        if(viewing(hint.room)){refreshVisible?.invoke();return null}
        if(!enabled()||!permitted())return null
        val old=current(hint)?.notification
        if(hint.room in cancelled&&old?.extras?.getString("yc.event")==hint.event)return null
        if(old?.extras?.getString("yc.event")==hint.event)return old.extras.getString("yc.generation")
        cancelled.remove(hint.room)
        val generation=UUID.randomUUID().toString()
        synchronized(history){
            val previous=history[hint.room];history[hint.room]=NoticeState(hint.copy(preview=null),generation,previous?.messages.orEmpty())
            while(history.size>64)history.remove(history.keys.first())
        }
        val extras=android.os.Bundle().apply{putString("yc.room",hint.room);putString("yc.kind",hint.kind);putString("yc.event",hint.event);putString("yc.generation",generation);putString("yc.account",account())}
        manager.notify(hint.tag,hint.notificationId,builder(hint).setExtras(extras).setOnlyAlertOnce(old!=null).build())
        return generation
    }
    fun enqueue(hint:PushHint,generation:String) {
        if(!unlocked)return // WorkManager's credential-encrypted database is unavailable.
        val request=OneTimeWorkRequestBuilder<NotificationContentWorker>()
            .setInputData(workDataOf("room" to hint.room,"kind" to hint.kind,"event" to hint.event,"generation" to generation,"account" to account()))
            .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST).addTag("notification-content").build()
        WorkManager.getInstance(context).enqueueUniqueWork("notification:${SecureStore.hash(hint.room)}",ExistingWorkPolicy.APPEND_OR_REPLACE,request)
    }
    fun receive(hint:PushHint) {val generation=placeholder(hint)?:return;enqueue(hint,generation)}
    fun afterUnlock() {
        if(!unlocked||!enabled())return
        manager.activeNotifications.forEach { row->val e=row.notification.extras
            val hint=PushHint.parse(mapOf("conversation_id" to e.getString("yc.room").orEmpty(),"conversation_type" to e.getString("yc.kind").orEmpty(),"message_id" to e.getString("yc.event").orEmpty()))
            if(hint!=null&&e.getString("yc.account")==account())e.getString("yc.generation")?.let{enqueue(hint,it)}
        }
    }
    @Synchronized fun replace(hint:PushHint,generation:String,account:String,messages:List<NoticeMessage>) {
        if(!enabled()||!permitted()||viewing(hint.room)||account()!=account||hint.room in cancelled)return
        val old=current(hint)?.notification?:return // User read/dismissed: never resurrect it.
        val state=synchronized(history){val before=history[hint.room]?:NoticeState(hint,old.extras.getString("yc.generation").orEmpty());NotificationPolicy.replace(before,before.generation,messages).also{history[hint.room]=it}}
        if(old.extras.getString("yc.generation")!=generation)return
        if(state.messages.isEmpty())return
        val style=Notification.MessagingStyle(NotificationPolicy.TITLE)
        if(Build.VERSION.SDK_INT>=28)style.isGroupConversation=hint.kind=="group"
        if(hint.kind=="group")style.conversationTitle=NotificationPolicy.title(names.getString(SecureStore.hash(hint.room),null))
        state.messages.forEach{style.addMessage(it.body,it.time,it.sender)}
        val last=state.messages.last()
        manager.notify(hint.tag,hint.notificationId,builder(hint).setExtras(old.extras).setStyle(style)
            .setContentText(if(hint.kind=="group")"${last.sender}: ${last.body}" else last.body).setOnlyAlertOnce(true).build())
    }
    // Memory only. OS NotificationManager owns displayed content; nothing goes into DP storage.
    companion object { private val history=mutableMapOf<String,NoticeState>() }
}

class YiChatMessagingService:FirebaseMessagingService() {
    override fun onMessageReceived(message:RemoteMessage) {
        Log.i("YiChatPush","priority=${message.priority} originalPriority=${message.originalPriority}")
        val hint=PushHint.parse(message.data)?:run{Log.w("YiChatPush","Rejected payload schema");return}
        val notices=(application as YiChatApplication).push
        // Suppressed only for the conversation actually on screen, or explicit OS/user denial.
        val generation=notices.placeholder(hint)?:return
        notices.enqueue(hint,generation)
    }
    override fun onNewToken(token:String) {
        // Never log the token. SDK persists it; the worker obtains its latest value.
        if((application as YiChatApplication).push.unlocked)PushRegistrationWorker.enqueue(this)
    }
}
class NotificationUnlockReceiver:BroadcastReceiver() {
    override fun onReceive(context:Context,intent:Intent) {
        if(intent.action !in setOf(Intent.ACTION_USER_UNLOCKED,Intent.ACTION_BOOT_COMPLETED,Intent.ACTION_MY_PACKAGE_REPLACED))return
        (context.applicationContext as YiChatApplication).push.afterUnlock()
        PushRegistrationWorker.enqueue(context)
    }
}
class PushRegistrationWorker(context:Context,params:WorkerParameters):CoroutineWorker(context,params) {
    override suspend fun doWork():Result {
        val app=applicationContext as YiChatApplication
        if(!app.push.unlocked)return Result.success()
        if(FirebaseApp.getApps(applicationContext).isEmpty())return Result.success()
        return try {
            val graph=app.graph;val a=graph.restore()?:return Result.success()
            val token=withTimeout(20_000){suspendCancellableCoroutine<String>{continuation->
                FirebaseMessaging.getInstance().token.addOnCompleteListener{task->if(continuation.isActive){if(task.isSuccessful)continuation.resumeWith(kotlin.Result.success(task.result))else continuation.resumeWith(kotlin.Result.failure(IllegalStateException("Token unavailable")))}}
            }}
            val enabled=graph.notifications.enabled()&&app.push.permitted()
            graph.gateway.call("/api/push/device",buildJsonObject{put("token",token);put("enabled",enabled);put("preview_version",1)})
            if(graph.auth.account?.token==a.token){graph.vault.put("${a.storage}.push-registered",(if(enabled)"1" else "0").toByteArray());if(enabled)applicationContext.stopService(Intent(applicationContext,BackgroundNotificationService::class.java))}
            Result.success()
        }catch(e:CancellationException){throw e}
        catch(_:Exception){
            val graph=app.graph
            graph.auth.account?.let{graph.vault.delete("${it.storage}.push-registered")}
            if(app.push.foreground)graph.notifications.startListener()
            Log.w("YiChatPush","Token registration pending");Result.retry()
        }
    }
    companion object {
        fun enqueue(context:Context){WorkManager.getInstance(context).enqueueUniqueWork("push-registration",ExistingWorkPolicy.APPEND_OR_REPLACE,
            OneTimeWorkRequestBuilder<PushRegistrationWorker>().setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()).build())}
    }
}
class NotificationContentWorker(context:Context,params:WorkerParameters):CoroutineWorker(context,params) {
    override suspend fun getForegroundInfo():ForegroundInfo {
        // Android 8–11 implements expedited work through a short-lived FGS.
        val manager=applicationContext.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("notification_sync","通知内容同步",NotificationManager.IMPORTANCE_LOW))
        return ForegroundInfo(3,Notification.Builder(applicationContext,"notification_sync").setSmallIcon(R.drawable.ic_notification).setContentTitle(NotificationPolicy.TITLE).setContentText(NotificationPolicy.PLACEHOLDER).setVisibility(Notification.VISIBILITY_PRIVATE).setOnlyAlertOnce(true).build())
    }
    override suspend fun doWork():Result {
        val app=applicationContext as YiChatApplication;val push=app.push
        if(!push.unlocked)return Result.success()
        val hint=PushHint.parse(mapOf("conversation_id" to inputData.getString("room").orEmpty(),"conversation_type" to inputData.getString("kind").orEmpty(),"message_id" to inputData.getString("event").orEmpty()))?:return Result.success()
        val account=inputData.getString("account").orEmpty()
        if(!push.enabled()||push.account()!=account||push.viewing(hint.room))return Result.success()
        // Coalesce a burst before networking: only the latest still-visible
        // placeholder needs a fetch; it pulls up to 20 unread events in one batch.
        if(!push.needsContent(hint,inputData.getString("generation").orEmpty()))return Result.success()
        return try {
            val graph=app.graph;val a=graph.restore()?:return Result.success()
            if(a.storage!=account)return Result.success()
            val messages=withTimeout(45_000){graph.notificationContent.fetch(hint)}
            if(graph.auth.account?.token==a.token)push.replace(hint,inputData.getString("generation").orEmpty(),account,messages)
            Result.success()
        }catch(e:TimeoutCancellationException){Log.w("YiChatPush","Content timeout; placeholder retained");Result.success()}
        catch(e:CancellationException){throw e}
        catch(_:Exception){Log.w("YiChatPush","Content unavailable; placeholder retained");Result.success()}
    }
}
