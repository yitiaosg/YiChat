package org.yitiao.yichat

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.google.firebase.FirebaseApp
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Checks the real FirebaseInitProvider startup, without exporting a device token. */
@RunWith(AndroidJUnit4::class)
class FirebaseConfigurationTest {
    @Test fun firebaseInitializesWithTheSelectedAndroidProject() {
        val project=InstrumentationRegistry.getArguments().getString("firebaseProjectId").orEmpty()
        assumeTrue(project.isNotBlank())
        val context=InstrumentationRegistry.getInstrumentation().targetContext
        val app=FirebaseApp.getInstance()
        assertEquals(project,app.options.projectId)
        assertEquals("org.yitiao.yichat.mobile",context.packageName)
        val parts=app.options.applicationId.split(':')
        assertTrue(parts.size>=4)
        assertEquals("android",parts[2])
        assertEquals(parts[1],app.options.gcmSenderId)
        assertFalse(app.options.apiKey.isNullOrBlank())
    }
}
