package org.yitiao.yichat

import org.junit.Assert.*
import org.junit.Test
import java.security.MessageDigest

class SecurityTest {
    @Test fun portableBackupCrossDeviceAndTamperRejection() {
        val original = "canonical ciphertext packets + withdrawal metadata".toByteArray()
        val encrypted = PortableBackup.encrypt(original, "a separate backup passphrase".toByteArray())
        assertArrayEquals(original, PortableBackup.decrypt(encrypted, "a separate backup passphrase".toByteArray()))
        val corrupted = encrypted.copyOf().apply { this[lastIndex] = (this[lastIndex].toInt() xor 1).toByte() }
        assertThrows(Exception::class.java) { PortableBackup.decrypt(corrupted, "a separate backup passphrase".toByteArray()) }
        assertThrows(Exception::class.java) { PortableBackup.decrypt(encrypted, "the wrong passphrase".toByteArray()) }
        assertFalse(String(encrypted).contains(String(original)))
    }
    @Test fun canonicalPacketBindsLfHashRoomAndEvent() {
        val raw = "{\"event_id\":\"\$event\",\"room_id\":\"!room\",\"type\":\"m.room.encrypted\"}\n".toByteArray()
        val hash = MessageDigest.getInstance("SHA-256").digest(raw).joinToString("") { "%02x".format(it) }
        HistoryRepository.verifyPacket(raw, hash, "!room", "\$event")
        assertThrows(IllegalArgumentException::class.java) { HistoryRepository.verifyPacket(raw.dropLast(1).toByteArray(), hash, "!room", "\$event") }
        assertThrows(IllegalArgumentException::class.java) { HistoryRepository.verifyPacket(raw, "0".repeat(64), "!room", "\$event") }
        assertThrows(IllegalArgumentException::class.java) { HistoryRepository.verifyPacket(raw, hash, "!other", "\$event") }
    }
    @Test fun shareAllowlistRejectsUrlConfusion() {
        val valid = "https://yidt.yitiao.org/explorer/tx/" + "a".repeat(64)
        assertTrue(ShareLinks.allowed(valid))
        for (u in listOf(valid + "?x=1", valid + "#x", valid.replace(".org", ".org.evil.test"), valid.replace("https:", "http:"), valid.replace("yidt.", "evil@yidt."), valid.replace(".org/", ".org:443/"))) assertFalse(u, ShareLinks.allowed(u))
        assertEquals(listOf(valid), ShareLinks.extract("private body $valid other private body"))
    }
}
