package org.yitiao.yichat

import org.junit.Assert.*
import org.junit.Test

class NotificationPolicyTest {
    private val hint=PushHint("!conversation:example","group","\$event")
    private val message=NoticeMessage("\$event","联系人","本机解密的正文",100)
    @Test fun payloadWhitelistRejectsIdentifyingDataAndInvalidRouting() {
        val data=mapOf("conversation_id" to hint.room,"conversation_type" to "group","message_id" to "\$event")
        assertEquals(hint,PushHint.parse(data))
        assertNull(PushHint.parse(data+mapOf("body" to "plaintext")))
        assertNull(PushHint.parse(data+mapOf("sender" to "nickname")))
        assertNull(PushHint.parse(data-mapOf("message_id" to "\$event").keys))
        assertNull(PushHint.parse(data+mapOf("conversation_type" to "direct")))
    }
    @Test fun negotiatedPreviewIsBoundedAndHiddenUntilFirstUnlock() {
        val data=mapOf("conversation_id" to hint.room,"conversation_type" to "group","message_id" to hint.event,"message_preview" to "测试正文","recipient_tag" to "a".repeat(64))
        val parsed=requireNotNull(PushHint.parse(data))
        assertEquals("测试正文",NotificationPolicy.initialText(parsed,true))
        assertTrue(NotificationPolicy.accepts(parsed,"a".repeat(64)))
        assertFalse(NotificationPolicy.accepts(parsed,"b".repeat(64)))
        assertNull(PushHint.parse(data-"recipient_tag"))
        assertEquals(NotificationPolicy.PLACEHOLDER,NotificationPolicy.initialText(parsed,false))
        assertEquals(NotificationPolicy.PLACEHOLDER,NotificationPolicy.initialText(hint,true))
        assertNull(PushHint.parse(data+mapOf("message_preview" to "字".repeat(401))))
        assertNull(PushHint.parse(data+mapOf("sender" to "untrusted name")))
    }
    @Test fun stableIdentityIncludesRoomTagToProtectAgainstHashCollisions() {
        assertEquals(hint.notificationId,hint.copy(event="\$second").notificationId)
        assertNotEquals(PushHint("!Aa","private","\$a").tag,PushHint("!BB","private","\$b").tag)
    }
    @Test fun placeholderUsesLocalDisplayNameOrSafeFallback() {
        assertEquals("群名称",NotificationPolicy.title("群名称"))
        assertEquals("Yichat",NotificationPolicy.title(null))
        assertEquals("Yichat",NotificationPolicy.title(""))
        assertEquals("你有新的消息",NotificationPolicy.PLACEHOLDER)
    }
    @Test fun failureOrMissingKeysPreservesPlaceholder() {
        val placeholder=NoticeState(hint,"generation")
        assertSame(placeholder,NotificationPolicy.replace(placeholder,"generation",null))
        assertSame(placeholder,NotificationPolicy.replace(placeholder,"generation",emptyList()))
        assertTrue(placeholder.messages.isEmpty())
    }
    @Test fun successfulDecryptionReplacesAndMergesMessagesWithoutDuplicates() {
        val placeholder=NoticeState(hint,"generation")
        val first=NotificationPolicy.replace(placeholder,"generation",listOf(message))
        assertEquals(listOf(message),first.messages)
        val second=message.copy(id="\$second",time=200)
        val merged=NotificationPolicy.replace(first,"generation",listOf(message,second))
        assertEquals(listOf(message,second),merged.messages)
        assertEquals(first.hint.notificationId,merged.hint.notificationId)
    }
    @Test fun staleGenerationCannotReplaceLatestPlaceholder() {
        val latest=NoticeState(hint,"new-generation")
        assertSame(latest,NotificationPolicy.replace(latest,"old-generation",listOf(message)))
    }
    @Test fun onlyVisibleConversationIsSuppressed() {
        assertTrue(NotificationPolicy.suppress(true,hint.room,hint.room))
        assertFalse(NotificationPolicy.suppress(true,"!another",hint.room))
        assertFalse(NotificationPolicy.suppress(false,hint.room,hint.room))
    }
}
