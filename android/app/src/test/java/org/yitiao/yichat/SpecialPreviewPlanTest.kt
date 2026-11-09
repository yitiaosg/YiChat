package org.yitiao.yichat
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test
class SpecialPreviewPlanTest {
 @Test fun transferUrlIsBackedUpOnceEvenWhenBothFieldsCarryIt() {
  val url="https://chat.yitiao.org/orders/"+"a".repeat(32)
  val content=buildJsonObject{put("body",url);put("org.yitiao.share",obj("url" to url,"type" to "receipt"))}
  val result=SpecialPreviewPlan.requests("!room",content);assertEquals(1,result.size);assertEquals(url,result.single().resource);assertEquals("!room",result.single().body?.s("room"))
 }
 @Test fun giftAndUrlOnlyRecordAreIncludedWithoutOpeningTheMessage() {
  val gift="b".repeat(32);val hash="c".repeat(64)
  val content=buildJsonObject{put("body","https://chat.yitiao.org/records/$hash");put("org.yitiao.business",obj("kind" to "gift","id" to gift))}
  val result=SpecialPreviewPlan.requests("!room:x",content)
  assertTrue(result.any{it.resource=="gift:$gift"&&it.path.endsWith("room=%21room%3Ax")})
  assertTrue(result.any{it.resource=="record:$hash"&&it.room.isEmpty()})
 }
 @Test fun arbitraryExternalUrlsNeverGenerateBackupRequests() {
  assertTrue(SpecialPreviewPlan.requests("!room",obj("body" to "https://evil.test/orders/"+"a".repeat(32))).isEmpty())
 }
}
