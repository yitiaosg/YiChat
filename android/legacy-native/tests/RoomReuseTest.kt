package org.yitiao.yichat

import org.junit.Assert.*
import org.junit.Test
import kotlinx.serialization.json.*

class RoomReuseTest {
    private val alice = "@stable_alice:chat.yitiao.org"
    private val bob = "@stable_bob:chat.yitiao.org"
    private fun rooms(kind: String, owner: String, peer: String) = buildJsonArray {
        add(obj("room" to "!existing:chat.yitiao.org", "kind" to kind, "owner" to owner, "peer" to peer))
    }
    @Test fun existingDirectIsReusedInBothOwnerDirections() {
        assertEquals("!existing:chat.yitiao.org", reusableDirectRoom(rooms("direct",alice,bob),alice,bob))
        assertEquals("!existing:chat.yitiao.org", reusableDirectRoom(rooms("direct",bob,alice),alice,bob))
    }
    @Test fun groupAndOtherRecipientAreNotMatched() {
        assertNull(reusableDirectRoom(rooms("group",alice,bob),alice,bob))
        assertNull(reusableDirectRoom(rooms("direct",alice,"@other:chat.yitiao.org"),alice,bob))
    }
}
