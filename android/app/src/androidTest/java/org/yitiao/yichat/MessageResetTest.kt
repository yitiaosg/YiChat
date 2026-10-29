package org.yitiao.yichat

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/** Real Room DB: explicit reset deletes chats once and retains asset operations. */
@RunWith(AndroidJUnit4::class)
class MessageResetTest {
    @Test fun chatResetKeepsWalletOperationAndNewMessages() = runBlocking {
        val context=InstrumentationRegistry.getInstrumentation().targetContext
        val graph=Graph(context.applicationContext as android.app.Application)
        val a=Account("isolated-epoch-test","isolated","@isolated:chat.yitiao.org","TEST","not-a-real-token","not-a-proof")
        val root=File(context.noBackupFilesDir,"web-accounts/${a.storage}")
        root.deleteRecursively()
        graph.store.open(a)
        try {
            val dao=requireNotNull(graph.store.database).local()
            dao.saveOperation(Operation("wallet-preserved","funds","paid",byteArrayOf(1),1))
            dao.saveOperation(Operation("old-chat-missing","missing_packet","pending",byteArrayOf(2),2))
            dao.addPacket(Packet("!old","\$old",1,"old-hash",byteArrayOf(3)))
            dao.receipt(TrustedReceipt("!old","\$old","old-hash",1,byteArrayOf(4)))
            dao.tombstone(Tombstone("!old","\$withdrawn",2))
            val backup=File(context.noBackupFilesDir,"backups/${a.storage}/packets.enc")
            backup.parentFile!!.mkdirs();backup.writeBytes(byteArrayOf(5))
            graph.resetChatEpoch(a)
            assertEquals(0L,dao.packetCount());assertTrue(dao.receipts().isEmpty());assertTrue(dao.tombstones().isEmpty())
            assertNull(dao.operation("old-chat-missing"));assertEquals("paid",dao.operation("wallet-preserved")?.state)
            assertFalse(backup.exists());assertEquals(MESSAGE_EPOCH,dao.watermark("@message-epoch")?.cursor)
            dao.addPacket(Packet("!new","\$new",3,"new-hash",byteArrayOf(6)))
            graph.resetChatEpoch(a)
            assertEquals(1L,dao.packetCount());assertNotNull(dao.operation("wallet-preserved"))
        } finally {graph.store.close();root.deleteRecursively()}
    }
}
