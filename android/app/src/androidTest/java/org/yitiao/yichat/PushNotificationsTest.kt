package org.yitiao.yichat

import android.app.Notification
import android.app.NotificationManager
import android.os.Build
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

/** No FCM/project/network dependency. Real NotificationManager rendering rules. */
@RunWith(AndroidJUnit4::class)
class PushNotificationsTest {
    @Test fun fcmPreviewDisplaysImmediatelyAndFailureRetainsIt() {
        val context=InstrumentationRegistry.getInstrumentation().targetContext
        val push=(context.applicationContext as YiChatApplication).push
        val manager=context.getSystemService(NotificationManager::class.java)
        val hint=PushHint("!preview:test","private","\$preview","对方向你转了一笔账","a".repeat(64))
        push.configure(true,"a".repeat(64))
        try {
            assertNull(push.placeholder(hint.copy(recipientTag="b".repeat(64))))
            val generation=requireNotNull(push.placeholder(hint))
            val deadline=System.currentTimeMillis()+5000
            while(manager.activeNotifications.none{it.tag==hint.tag}&&System.currentTimeMillis()<deadline)Thread.sleep(50)
            val notification=manager.activeNotifications.first{it.tag==hint.tag}.notification
            assertEquals("对方向你转了一笔账",notification.extras.getCharSequence(Notification.EXTRA_TEXT).toString())
            assertEquals("你有新的消息",notification.publicVersion.extras.getCharSequence(Notification.EXTRA_TEXT).toString())
            assertFalse(notification.extras.containsKey("message_preview"))
            push.replace(hint,generation,"a".repeat(64),emptyList())
            assertEquals("对方向你转了一笔账",manager.activeNotifications.first{it.tag==hint.tag}.notification.extras.getCharSequence(Notification.EXTRA_TEXT).toString())
        } finally {push.cancel(hint.room);push.configure(false,"")}
    }
    @Test fun placeholderReplacementPrivacyFailureAndDismissal() {
        val instrumentation=InstrumentationRegistry.getInstrumentation();val context=instrumentation.targetContext
        if(Build.VERSION.SDK_INT>=33)instrumentation.uiAutomation.executeShellCommand("pm grant ${context.packageName} android.permission.POST_NOTIFICATIONS").close()
        val push=(context.applicationContext as YiChatApplication).push
        val manager=context.getSystemService(NotificationManager::class.java)
        val hint=PushHint("!isolated-notification:test","group","\$first")
        fun current(ready:(Notification)->Boolean={true}):Notification {
            val deadline=System.currentTimeMillis()+5_000
            while(System.currentTimeMillis()<deadline){
                manager.activeNotifications.firstOrNull{it.tag==hint.tag}?.let{if(ready(it.notification))return it.notification}
                Thread.sleep(50)
            }
            error("Test notification did not become visible")
        }
        push.configure(true,"isolated-test");push.cacheNames(listOf(hint.room to "隔离测试群"))
        try {
            val generation=requireNotNull(push.placeholder(hint))
            val placeholder=current{it.extras.getString("yc.generation")==generation}
            assertTrue(push.needsContent(hint,generation))
            assertEquals("隔离测试群",placeholder.extras.getCharSequence(Notification.EXTRA_TITLE).toString())
            assertEquals("你有新的消息",placeholder.extras.getCharSequence(Notification.EXTRA_TEXT).toString())
            assertEquals(Notification.VISIBILITY_PRIVATE,placeholder.visibility)
            assertEquals("Yichat",placeholder.publicVersion.extras.getCharSequence(Notification.EXTRA_TITLE).toString())
            assertEquals(NotificationManager.IMPORTANCE_HIGH,manager.getNotificationChannel("messages_group").importance)
            // No replace call on failure; placeholder remains visible.
            push.replace(hint,generation,"isolated-test",emptyList())
            assertEquals("你有新的消息",current().extras.getCharSequence(Notification.EXTRA_TEXT).toString())
            push.replace(hint,generation,"isolated-test",listOf(NoticeMessage("\$first","测试发送者","测试正文",100)))
            val replaced=current{it.extras.getString(Notification.EXTRA_TEMPLATE)=="android.app.Notification\$MessagingStyle"}
            assertFalse(push.needsContent(hint,generation))
            assertEquals(1,manager.activeNotifications.count{it.tag==hint.tag})
            assertTrue(replaced.flags and Notification.FLAG_ONLY_ALERT_ONCE!=0)
            assertEquals("android.app.Notification\$MessagingStyle",replaced.extras.getString(Notification.EXTRA_TEMPLATE))
            assertEquals("你有新的消息",replaced.publicVersion.extras.getCharSequence(Notification.EXTRA_TEXT).toString())
            val next=hint.copy(event="\$second")
            val nextGeneration=requireNotNull(push.placeholder(next))
            current{it.extras.getString("yc.generation")==nextGeneration}
            assertFalse(push.needsContent(hint,generation))
            assertTrue(push.needsContent(next,nextGeneration))
            push.replace(hint,generation,"isolated-test",listOf(NoticeMessage("\$first","测试发送者","旧任务结果",100)))
            assertEquals("你有新的消息",current().extras.getCharSequence(Notification.EXTRA_TEXT).toString())
            push.cancel(hint.room)
            assertFalse(push.needsContent(next,nextGeneration))
            push.replace(hint,generation,"isolated-test",listOf(NoticeMessage("\$first","测试发送者","测试正文",100)))
            val cancelledBy=System.currentTimeMillis()+5_000
            while(manager.activeNotifications.any{it.tag==hint.tag}&&System.currentTimeMillis()<cancelledBy)Thread.sleep(50)
            assertFalse(manager.activeNotifications.any{it.tag==hint.tag})
        } finally {push.configure(false,"")}
    }
}
