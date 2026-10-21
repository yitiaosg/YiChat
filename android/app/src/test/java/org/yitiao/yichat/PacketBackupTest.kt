package org.yitiao.yichat

import org.junit.Assert.*
import org.junit.Test

class PacketBackupTest {
    @Test fun exportsRemainPortableAndRejectTamperingOrWrongPassword() {
        val raw="verified encrypted packets".toByteArray()
        val packed=PortableBackup.encrypt(raw,"independent export password".toByteArray())
        assertArrayEquals(raw,PortableBackup.decrypt(packed,"independent export password".toByteArray()))
        assertThrows(Exception::class.java) { PortableBackup.decrypt(packed,"wrong password".toByteArray()) }
        packed[packed.lastIndex]=(packed.last().toInt() xor 1).toByte()
        assertThrows(Exception::class.java) { PortableBackup.decrypt(packed,"independent export password".toByteArray()) }
    }
}
