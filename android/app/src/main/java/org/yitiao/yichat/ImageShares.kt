package org.yitiao.yichat

import android.content.ClipData
import android.content.Context
import android.content.Intent
import androidx.core.content.FileProvider
import java.io.File
import java.util.UUID

/** Only user-generated PNG transfers can enter the exported, read-only URI scope. */
object ImageShares {
    fun intent(context: Context, source: File, mime: String, link: String): Intent {
        require(mime == "image/png" && source.length() in 8..100000000) { "只能分享 PNG 图片" }
        val magic = byteArrayOf(0x89.toByte(),0x50,0x4e,0x47,0x0d,0x0a,0x1a,0x0a)
        require(source.inputStream().use { stream -> val head=ByteArray(8);stream.read(head)==8 && head.contentEquals(magic) }) { "图片格式无效" }
        require(WebRoutes.chat(link) && java.net.URI(link).path.startsWith("/records/")) { "聊天记录链接无效" }
        val directory=File(context.cacheDir,"shared-images").apply { mkdirs() }
        directory.listFiles()?.filter { System.currentTimeMillis()-it.lastModified()>86400000 }?.forEach { it.delete() }
        val image=File(directory,"YiChat-record-${UUID.randomUUID()}.png")
        check(source.renameTo(image)) { "无法准备分享图片" }
        val uri=FileProvider.getUriForFile(context,BuildConfig.APPLICATION_ID+".sharedfiles",image)
        return Intent(Intent.ACTION_SEND).setType("image/png").putExtra(Intent.EXTRA_STREAM,uri)
            .putExtra(Intent.EXTRA_TEXT,link).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            .apply { clipData=ClipData.newRawUri("YiChat record",uri) }
    }
}
