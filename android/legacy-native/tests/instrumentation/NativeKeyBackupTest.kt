package org.yitiao.yichat

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.*
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.matrix.rustcomponents.sdk.*
import kotlinx.serialization.json.jsonObject

@RunWith(AndroidJUnit4::class)
class NativeKeyBackupTest {
    @Test fun officialSdkRecoveryCreationUploadAndRecovery() = runBlocking<Unit> {
        val context=InstrumentationRegistry.getInstrumentation().targetContext
        val vault=SecureStore(context)
        
        val base="http://127.0.0.1:"+requireNotNull(InstrumentationRegistry.getArguments().getString("port"))
        val gateway=Gateway(base)
        val raw=gateway.call("/__fixture/accounts/bob",authenticated=false).jsonObject
        val account=Account(raw.s("uid"),raw.s("user"),raw.s("mxid"),raw.s("device"),raw.s("token"),raw.s("proof"))
        gateway.token=account.token
        val scope=CoroutineScope(SupervisorJob()+Dispatchers.Default)
        val matrix=MatrixRepository(context,vault,gateway,scope)
        try {
            matrix.open(account)
            withTimeout(180000) {
                val encryption=matrix.client!!.encryption()
                encryption.waitForE2eeInitializationTasks()
                val key=encryption.enableRecovery(true,null,object: EnableRecoveryProgressListener {
                    override fun onUpdate(status: EnableRecoveryProgress) {}
                })
                assertTrue(key.isNotBlank())
                encryption.waitForBackupUploadSteadyState(null)
                encryption.recover(key)
                assertEquals(RecoveryState.ENABLED,encryption.recoveryState())
                println("PASS: official SDK recovery created, native keys backed up, same-device recovery succeeded")
            }
        } finally { matrix.close();scope.cancel() }
    }
}
