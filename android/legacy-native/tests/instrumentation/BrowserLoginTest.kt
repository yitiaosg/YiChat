package org.yitiao.yichat

import android.content.Intent
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import androidx.test.uiautomator.UiDevice
import androidx.test.uiautomator.Until
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/** Real browser UI, original OIDC provider, real gateway and isolated Synapse. */
@RunWith(AndroidJUnit4::class)
class BrowserLoginTest {
    @Test fun browserWindowReturnsToNativeConversationList() {
        assertEquals("http://127.0.0.1:18018", BuildConfig.GATEWAY)
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val device = UiDevice.getInstance(instrumentation)
        androidx.test.uiautomator.Configurator.getInstance().waitForIdleTimeout = 1500
        device.wakeUp()
        device.executeShellCommand("wm dismiss-keyguard")
        context.startActivity(Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        assertTrue("Native login ready", device.wait(Until.hasObject(By.text("使用主站账号登录").enabled(true)), 90000))
        println("MILESTONE native login ready")
        device.findObject(By.text("使用主站账号登录")).click()
        assertTrue("Browser-owned authorization window", device.wait(Until.hasObject(By.res("com.android.chrome", "close_button")), 90000))
        assertTrue("Real OIDC login page", device.wait(Until.hasObject(By.text("Isolated test login")), 90000))
        println("MILESTONE real browser login form")
        device.takeScreenshot(File(context.filesDir, "browser-auth-window.png"))
        val inputs = device.findObjects(By.clazz("android.widget.EditText"))
        assertEquals("Original login form inputs", 2, inputs.size)
        inputs[0].text = "alice"
        inputs[1].text = "IsolatedPassword123!"
        // Android's input command injects an immediate down/up pair; a timed
        // UiObject2 gesture can become a long press under software emulation.
        val submit = device.findObject(By.text("Sign in")).visibleBounds
        println("Browser submit bounds: $submit; display ${device.displayWidth}x${device.displayHeight}")
        device.executeShellCommand("input tap ${submit.centerX()} ${submit.centerY()}")
        println("MILESTONE submitted browser login")
        assertTrue("Automatic return and native crypto initialization", device.wait(Until.hasObject(By.text("新设备与历史密钥")), 360000))
        device.findObject(By.text("我已了解，稍后处理")).click()
        assertTrue(device.wait(Until.hasObject(By.text("设置")), 30000))
        assertTrue("Real synchronized conversation", device.wait(Until.hasObject(By.text("Android isolated group")), 90000))
        println("MILESTONE native encrypted session and real conversation list")
        val graph = (context.applicationContext as YiChatApplication).graph
        assertEquals("alice", graph.auth.account?.user)
        assertNotNull(graph.auth.account?.device)
        kotlinx.coroutines.runBlocking {
            graph.vault.put("native-process-key", requireNotNull(graph.matrix.client?.encryption()?.curve25519Key()).toByteArray())
        }
        assertEquals(context.packageName, device.currentPackageName)
        device.takeScreenshot(File(context.filesDir, "native-login-success.png"))
        // Activity recreation must not exchange the consumed callback again.
        device.setOrientationLeft()
        assertTrue(device.wait(Until.hasObject(By.text("设置")), 30000))
        assertEquals("alice", graph.auth.account?.user)
        device.setOrientationNatural()
    }
}
