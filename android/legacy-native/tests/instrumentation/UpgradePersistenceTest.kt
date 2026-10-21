package org.yitiao.yichat
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class UpgradePersistenceTest {
    @Test fun keystoreEnvelopeAndAccountIsolationAcrossSignedUpgrade() {
        val c=InstrumentationRegistry.getInstrumentation().targetContext
        val args=InstrumentationRegistry.getArguments();val v=SecureStore(c)
        val expected=requireNotNull(args.getString("marker")).toByteArray()
        if(args.getString("phase")=="seed") v.put("upgrade-marker",expected)
        assertArrayEquals(expected,v.get("upgrade-marker"))
        val a=v.accountKey("isolation-fixture-alice","data")
        val b=v.accountKey("isolation-fixture-bob","data")
        assertFalse(a.contentEquals(b))
        val packet=v.encrypt("isolation-fixture-alice","packet-event",expected)
        assertArrayEquals(expected,v.decrypt("isolation-fixture-alice","packet-event",packet))
        assertThrows(Exception::class.java) { v.decrypt("isolation-fixture-bob","packet-event",packet) }
        assertThrows(Exception::class.java) { v.decrypt("isolation-fixture-alice","other-event",packet) }
    }
}
