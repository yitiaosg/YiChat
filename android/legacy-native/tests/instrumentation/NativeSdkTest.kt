package org.yitiao.yichat

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.matrix.rustcomponents.sdk.*
import java.io.File
import java.util.UUID
import androidx.test.uiautomator.UiDevice
import androidx.test.uiautomator.By
import androidx.test.uiautomator.Until
import android.content.Intent
import kotlinx.serialization.encodeToString

/** Test-only loopback gateway. No credentials or test endpoints ship in release. */
@RunWith(AndroidJUnit4::class)
class NativeSdkTest {
    @Test fun realSdkTwoAccountsE2eePlainGroupAndPersistentKeys() = runBlocking<Unit> {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val args = InstrumentationRegistry.getArguments()
        val base = "http://127.0.0.1:" + requireNotNull(args.getString("port"))
        val direct = requireNotNull(args.getString("direct")); val group = requireNotNull(args.getString("group"))
        val context = instrumentation.targetContext
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        val aHttp = Gateway(base); val bHttp = Gateway(base)
        suspend fun account(http: Gateway, name: String): Account {
            val r = http.call("/__fixture/accounts/$name", authenticated = false).jsonObject
            return Account(r.s("uid"),r.s("user"),r.s("mxid"),r.s("device"),r.s("token"),r.s("proof")).also { http.token = it.token }
        }
        val a = account(aHttp,"alice"); val b = account(bHttp,"bob")
        val vault = SecureStore(context)
        val alice = MatrixRepository(context,vault,aHttp,scope); val bob = MatrixRepository(context,vault,bHttp,scope)
        try {
            alice.open(a); bob.open(b)
            withTimeout(180000) { while (alice.client!!.getRoom(direct)==null || bob.client!!.getRoom(direct)==null) delay(1000) }
            assertNotEquals(a.storage,b.storage)
            alice.select(direct); bob.select(direct)
            val text = "Android native E2EE " + UUID.randomUUID()
            alice.send(text)
            withTimeout(180000) { while (bob.messages.value.none { it.body==text && !it.encryptedFailure }) delay(1000) }
            val native = aHttp.call("/_matrix/client/v3/rooms/${Gateway.segment(direct)}/messages?dir=b&limit=10").jsonObject.a("chunk")
            assertTrue(native.any { it.jsonObject.s("type")=="m.room.encrypted" })
            assertFalse(native.toString().contains(text))
            val key = alice.client!!.encryption().curve25519Key()
            assertNotNull("native device key must exist",key)
            vault.put("native-process-key",key!!.toByteArray())
            println("PASS: two-account native E2EE, ciphertext verified at gateway")
            alice.close();alice.open(a)
            assertEquals(key,alice.client!!.encryption().curve25519Key())
            println("PASS: same device key persisted across SDK close/reopen")
            withTimeout(120000) { while(alice.client!!.getRoom(group)==null) delay(500) }
            alice.select(group);bob.select(group)
            val plain = "Android plain group " + UUID.randomUUID()
            alice.send(plain)
            withTimeout(120000) { while(bob.messages.value.none { it.body==plain }) delay(500) }
            val events = aHttp.call("/_matrix/client/v3/rooms/${Gateway.segment(group)}/messages?dir=b&limit=10").jsonObject.a("chunk")
            assertTrue(events.any { it.jsonObject.s("type")=="m.room.message" && it.jsonObject.o("content").s("body")==plain })
            println("PASS: group plaintext received by second account and verified at gateway")
            val clear = File(context.cacheDir,"matrix-attachment-fixture.txt").apply { writeText("native Matrix attachment fixture") }
            val encrypted=File(context.cacheDir,"matrix-attachment-fixture.bin")
            val descriptor=MatrixAttachment.encrypt(clear,encrypted)
            val file=aHttp.upload(direct,encrypted){_,_->}
            val source=MediaSource.fromJson(buildJsonObject { put("file",JsonObject(descriptor+("url" to JsonPrimitive(file.s("url"))))) }.toString())
            // An authenticated reference is required before the other member
            // downloads; sending uses the same durable txn metadata pipeline.
            alice.select(direct)
            alice.sendMedia(direct,MessageType.File(FileMessageContent("fixture.txt",null,null,source,FileInfo("text/plain",clear.length().toULong(),null,null))),buildJsonObject { put("org.yitiao.attachments",JsonArray(listOf(JsonPrimitive(file.s("id"))))) })
            bob.select(direct)
            withTimeout(120000) { while(bob.messages.value.none { it.attachment?.name=="fixture.txt" }) delay(500) }
            val attachment=bob.messages.value.first { it.attachment?.name=="fixture.txt" }.attachment!!
            val media=MediaRepository(context,bob,bHttp).download(attachment)
            try { assertEquals(clear.readText(),File(media.path()).readText()) } finally { media.close() }
            clear.delete();encrypted.delete()
            println("PASS: encrypted attachment outer binding, download and byte equality")
        } finally { alice.close();bob.close();scope.cancel() }
        // Exercise the actual Compose screens against the same isolated service.
        // This build flag is debug-only; release always uses the HTTPS chat domain.
        assertEquals(base,BuildConfig.GATEWAY)
        vault.put("session",json.encodeToString(a).toByteArray())
        println("PASS: account saved for separate process/UI tests")
    }

    @Test fun composeConversationAndDetails() {
        val instrumentation=InstrumentationRegistry.getInstrumentation()
        val context=instrumentation.targetContext
        val device=UiDevice.getInstance(instrumentation)
        androidx.test.uiautomator.Configurator.getInstance().waitForIdleTimeout=1500
        device.wakeUp()
        device.executeShellCommand("wm dismiss-keyguard")
        context.startActivity(Intent(context,MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        assertTrue(device.wait(Until.hasObject(By.text("Android isolated group")),180000))
        device.findObject(By.text("Android isolated group")).click()
        assertTrue(device.wait(Until.hasObject(By.text("加载更早历史")),60000))
        assertTrue(device.wait(Until.hasObject(By.textContains("Android plain group")),120000))
        val composeText="Compose UI send "+UUID.randomUUID()
        device.findObjects(By.clazz("android.widget.EditText")).last().text=composeText
        requireNotNull(device.wait(Until.findObject(By.text("发送").enabled(true)),90000)).click()
        val sentDeadline=android.os.SystemClock.elapsedRealtime()+90000
        while(android.os.SystemClock.elapsedRealtime()<sentDeadline && device.findObjects(By.clazz("android.widget.EditText")).lastOrNull()?.text?.isNotEmpty()!=false) android.os.SystemClock.sleep(500)
        assertEquals("send must clear submitted draft only after SDK queue accepts it","",device.findObjects(By.clazz("android.widget.EditText")).last().text)
        device.wait(Until.findObject(By.text("有新消息 ↓")),15000)?.click()
        assertTrue(device.wait(Until.hasObject(By.text(composeText)),60000))
        if(device.hasObject(By.pkg("com.android.inputmethod.latin"))) device.pressBack()
        device.takeScreenshot(File(context.getExternalFilesDir(null),"android-group.png"))
        requireNotNull(device.wait(Until.findObject(By.text("详情")),60000)).click()
        assertTrue(device.wait(Until.hasObject(By.text("会话详情")),60000))
        device.takeScreenshot(File(context.getExternalFilesDir(null),"android-room-details.png"))
        device.findObject(By.text("关闭")).click()
        device.findObject(By.text("返回")).click()
        device.takeScreenshot(File(context.getExternalFilesDir(null),"android-conversations.png"))
    }
}

@RunWith(AndroidJUnit4::class)
class NativeProcessPersistenceTest {
    @Test fun sameNativeDeviceKeyAfterProcessDeath() = runBlocking<Unit> {
        val c=InstrumentationRegistry.getInstrumentation().targetContext
        val vault=SecureStore(c)
        val account=json.decodeFromString<Account>(requireNotNull(vault.get("session")).decodeToString())
        val base="http://127.0.0.1:"+requireNotNull(InstrumentationRegistry.getArguments().getString("port"))
        val gateway=Gateway(base).apply { token=account.token }
        val scope=CoroutineScope(SupervisorJob()+Dispatchers.Default)
        val matrix=MatrixRepository(c,vault,gateway,scope)
        try {
            matrix.open(account)
            val key=matrix.client!!.encryption().curve25519Key()
            assertNotNull(key)
            assertEquals(requireNotNull(vault.get("native-process-key")).decodeToString(),key)
            println("PASS: Matrix native device key survived instrumentation process death")
        } finally { matrix.close();scope.cancel() }
    }
}
