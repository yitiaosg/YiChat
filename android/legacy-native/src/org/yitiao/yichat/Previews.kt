package org.yitiao.yichat

import android.annotation.SuppressLint
import android.net.Uri
import android.webkit.*
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.platform.LocalContext
import com.caverock.androidsvg.SVG
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.webkit.WebViewAssetLoader
import kotlinx.serialization.json.*
import kotlinx.coroutines.delay
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.currentStateAsState
import java.io.ByteArrayInputStream
import java.math.BigDecimal

@Composable fun Preview(model: AppModel, url: String, room: String, browse: (Uri) -> Unit, receiptSender: String? = null) {
    var data by remember(url, room) { mutableStateOf<JsonObject?>(null) }
    var unavailable by remember(url, room) { mutableStateOf(false) }
    val lifecycleState by LocalLifecycleOwner.current.lifecycle.currentStateAsState()
    LaunchedEffect(url, room, lifecycleState) {
        if(lifecycleState!=Lifecycle.State.RESUMED) return@LaunchedEffect
        while(true) {
            try { val result=model.graph.business.preview(url,room);data=result as? JsonObject;unavailable=result==JsonNull }
            catch(e: kotlinx.coroutines.CancellationException) { throw e }
            catch(e: Exception) { unavailable=true;break }
            val result=data ?: break
            if(result.s("type")!="order" || result.o("data").s("state") in listOf("paid","refunded","cancelled")) break
            delay(20000)
        }
    }
    val p = data
    if (p != null) Column(Modifier.fillMaxWidth().padding(top = 10.dp)) {
        val d = p.o("data")
        when(p.s("type")) {
            "order" -> {
                val verifiedReceipt = receiptSender == d.s("recipient_mxid") && d.a("claims").any { it.jsonObject.s("user_id") == d.s("recipient_uid") }
                if (receiptSender != null) Text(if (verifiedReceipt) "已收款 · ${if (d.s("state") == "paid") "已到账" else "到账处理中"}" else "回执未核实，请查询原订单")
                Text(if (d.s("kind") == "group") "红包 · ${d.s("title")}" else "转账 · ${d.s("amount")} YIDT", style = MaterialTheme.typography.titleMedium)
                Text(when (d.s("state")) { "awaiting_receipt" -> "待对方主动收款"; "paid" -> "已到账"; "paying" -> "到账处理中"; "cancelled" -> "已取消"; "refunded" -> "已退款"; else -> d.s("state") })
                TextButton(onClick = { model.showOrder(d.s("id")) }) { Text("查看权威订单与允许操作") }
            }
            "card" -> { CardArt(d); Text(d.s("name")); Text(d.s("cid"), style = MaterialTheme.typography.labelSmall) }
            "game" -> {
                // Active games are never rendered: backend permission and
                // snapshot gates are mandatory as well as this local check.
                if (d.b("snapshot") && d.s("status") in listOf("jumped", "crashed")) {
                    GameScene(d); Text("固定快照 · ${d.s("status")} · ${d.s("exit_height")} m")
                } else Text("游戏快照尚未公开")
            }
            "transaction" -> {
                val amount = runCatching { BigDecimal(d.s("credit")).movePointLeft(16).stripTrailingZeros().toPlainString() }.getOrElse { "金额不可读取" }
                Text("$amount YIDT", style = MaterialTheme.typography.titleMedium)
                Text("${d.s("source")} → ${d.s("destination")}", style = MaterialTheme.typography.bodySmall)
                Text(d.s("hash"), style = MaterialTheme.typography.labelSmall)
                d["created"]?.jsonPrimitive?.longOrNull?.let { Text(displayTime(it * 1000)) }
            }
        }
        if (p.s("type") != "order") TextButton(onClick = { browse(Uri.parse(url)) }) { Text("系统浏览器查看原站") }
    }
    if (unavailable) { Text("资源缺失、未公开或无权预览", style = MaterialTheme.typography.labelSmall); TextButton(onClick = { browse(Uri.parse(url)) }) { Text("打开普通链接") } }
}

@Composable fun GiftPreview(model: AppModel, id: String, room: String) {
    var data by remember(id,room) { mutableStateOf<JsonObject?>(null) }; var unavailable by remember(id,room) { mutableStateOf(false) }
    LaunchedEffect(id,room) { runCatching { model.graph.gateway.call("/api/cards/gifts/${Gateway.segment(id)}?room=${Gateway.segment(room)}").jsonObject }.onSuccess { data = it }.onFailure { unavailable = true } }
    data?.let { gift -> Column { CardArt(gift.o("card")); Text(gift.o("card").s("name")); Text("${gift.s("sender")} → ${gift.s("recipient")} · ${if(gift.b("snapshot")) "赠送时卡面快照" else "当前可读卡面"}") } }
    if (unavailable) Text("赠卡快照当前缺失或无权查看")
}

@SuppressLint("SetJavaScriptEnabled")
@Composable fun CardArt(data: JsonObject) {
    val lifecycleState by LocalLifecycleOwner.current.lifecycle.currentStateAsState()
    AndroidView(modifier = Modifier.fillMaxWidth().height(280.dp), factory = { context ->
        val loader = WebViewAssetLoader.Builder().addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(context)).build()
        WebView(context).apply {
            settings.javaScriptEnabled = true; settings.domStorageEnabled = false; settings.allowFileAccess = false; settings.allowContentAccess = false
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW; settings.setSupportMultipleWindows(false)
            settings.blockNetworkLoads = true
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest) = true
                override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse {
                    return loader.shouldInterceptRequest(request.url) ?: WebResourceResponse("text/plain", "UTF-8", 403, "Blocked", emptyMap(), ByteArrayInputStream(ByteArray(0)))
                }
                override fun onPageFinished(view: WebView, url: String) {
                    if (url == "https://appassets.androidplatform.net/assets/card/index.html") view.evaluateJavascript("window.showCard(${view.tag as? String ?: "null"})", null)
                }
            }
            loadUrl("https://appassets.androidplatform.net/assets/card/index.html")
        }
    }, update = { it.tag=data.toString();if(lifecycleState==Lifecycle.State.RESUMED) it.onResume() else it.onPause();it.evaluateJavascript("if(window.showCard)window.showCard(${data})", null) }, onReset={it.onPause()}, onRelease = { it.onPause(); it.stopLoading(); it.destroy() })
}

@Composable private fun GameScene(data: JsonObject) {
    // Geometry follows packaged parachute-scene.js at the same 600x320 scene.
    val h = data["exit_height"]?.jsonPrimitive?.floatOrNull?.coerceAtLeast(0f) ?: 0f
    val jumped = data.s("status") == "jumped"
    val context = LocalContext.current
    val plane = remember { runCatching { SVG.getFromAsset(context.assets, "parachute-plane.svg") }.getOrNull() }
    val jumper = remember { runCatching { SVG.getFromAsset(context.assets, "parachute-jump.svg") }.getOrNull() }
    Canvas(Modifier.fillMaxWidth().height(170.dp)) {
        val sx = size.width / 600f; val sy = size.height / 320f
        drawRect(Brush.verticalGradient(listOf(Color(0xff07142f), Color(0xff244a71), Color(0xff273c58))))
        drawCircle(Color(0xfffff1bc), 24 * sx, Offset(515 * sx, 64 * sy))
        repeat(28) { i -> drawCircle(Color(0xffe5f0ff), sx, Offset(((i * 113 + 37) % 600) * sx, ((i * 73 + 18) % 180) * sy)) }
        repeat(24) { i -> val y = 260f + (i % 4) * 7; drawRect(Color(0xff0a1b35), Offset((i * 28 - 20) * sx, y * sy), Size(22 * sx, (320 - y) * sy)) }
        drawRect(Color(0xff17263d), Offset(0f, 290 * sy), Size(size.width, 30 * sy))
        val ground = 82f - 170f * 10 / 470
        val maxRise = (320f - ground - 170f * 190 / 470 - 24).coerceAtLeast(0f)
        val x = (maxOf(99f, 600f * .15f) + minOf(600f * .33f, h * 2.5f)).coerceIn(100f, 475f)
        val bottom = ground + minOf(maxRise, h * 2.3f)
        val y = 320 - bottom - 34
        if (!jumped) drawCircle(Brush.radialGradient(listOf(Color(0xfffff4b8), Color(0xffed7859), Color.Transparent), center = Offset(x * sx, y * sy), radius = 55 * sx), 55 * sx, Offset(x * sx, y * sy))
        else {
            val canvas = drawContext.canvas.nativeCanvas
            canvas.save(); canvas.scale(sx, sy); canvas.rotate(-minOf(18f, h * 1.8f), x, y)
            plane?.renderToCanvas(canvas, android.graphics.RectF(x - 85, y - 35, x + 85, y + 35)); canvas.restore()
            canvas.save(); canvas.scale(sx, sy)
            val jx = maxOf(12f, x - 100); val jy = minOf(212f, y + 20)
            jumper?.renderToCanvas(canvas, android.graphics.RectF(jx, jy, jx + 70, jy + 90)); canvas.restore()
        }
    }
}
