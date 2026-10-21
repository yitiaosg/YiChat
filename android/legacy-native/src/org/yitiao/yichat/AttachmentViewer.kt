package org.yitiao.yichat

import android.graphics.BitmapFactory
import android.net.Uri
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.MediaItem
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import java.io.File

@Composable fun AttachmentViewer(attachment: Attachment, path: String, save: () -> Unit, close: () -> Unit) {
    val context = LocalContext.current
    val mime = attachment.mime.lowercase()
    val active = mime in setOf("text/html", "application/xhtml+xml", "image/svg+xml", "application/javascript", "text/javascript")
    AlertDialog(onDismissRequest = close, title = { Text(attachment.name) }, text = { Column {
        if (!active && mime in listOf("image/jpeg", "image/png", "image/webp", "image/gif")) {
            val bitmap = remember(path) {
                val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }; BitmapFactory.decodeFile(path, bounds)
                var sample = 1; while (bounds.outWidth / sample > 2048 || bounds.outHeight / sample > 2048) sample *= 2
                BitmapFactory.decodeFile(path, BitmapFactory.Options().apply { inSampleSize = sample })
            }
            bitmap?.let { Image(it.asImageBitmap(), attachment.name, Modifier.fillMaxWidth().heightIn(max = 400.dp)) }
        } else if (!active && (mime.startsWith("audio/") || mime.startsWith("video/"))) {
            val player = remember(path) { ExoPlayer.Builder(context).build().apply { setMediaItem(MediaItem.fromUri(Uri.fromFile(File(path)))); prepare(); playWhenReady = false } }
            DisposableEffect(player) { onDispose { player.release() } }
            AndroidView(factory = { PlayerView(it).apply { this.player = player } }, modifier = Modifier.fillMaxWidth().height(240.dp))
        } else Text(if (active) "此类型含活动内容，App 不执行或内嵌展示。可保存到你选择的目录。" else "文件已下载并经 Matrix SDK 解密（适用于加密附件），可保存到你选择的目录。")
    } }, confirmButton = { TextButton(onClick = save) { Text("保存到 SAF 文件") } }, dismissButton = { TextButton(onClick = close) { Text("关闭并清理临时文件") } })
}
