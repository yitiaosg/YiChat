package org.yitiao.yichat

import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.serialization.json.*

@Composable fun GiftDialog(model: AppModel, close: () -> Unit) {
    val state by model.state.collectAsStateWithLifecycle()
    var recipient by remember { mutableStateOf("") }; var page by remember { mutableStateOf(1) }; var confirm by remember { mutableStateOf(false) }
    AlertDialog(onDismissRequest = close, title = { Text("赠送本人真实 YCard") }, text = {
        Column(Modifier.verticalScroll(rememberScrollState())) {
            Text("确认赠送会转移资产；关联消息发送失败不会再次赠送。")
            if (state.gift == null) {
                state.profiles.map { it.jsonObject }.filter { it.s("username") != state.account?.user }.forEach { p ->
                    Row { RadioButton(recipient == p.s("username"), { recipient = p.s("username") }); Text(p.s("nickname").ifBlank { p.s("username") }) }
                }
                val cards = state.cards?.a("items")?.map { it.jsonObject }.orEmpty().filter { it.b("giftable") }
                if (state.cards != null && cards.isEmpty()) Text("此页没有本人可赠的卡片")
                cards.forEach { c -> OutlinedButton(onClick = { model.selectCard(c.s("id")) }) { Text(c.s("name") + " · " + c.s("cid")) } }
                Row {
                    TextButton(onClick = { page--; model.cards(page) }, enabled = page > 1) { Text("上一页") }
                    Text("第 $page 页", Modifier.padding(8.dp))
                    val total = state.cards?.get("total")?.jsonPrimitive?.intOrNull ?: 0
                    TextButton(onClick = { page++; model.cards(page) }, enabled = page * 24 < total) { Text("下一页") }
                }
                state.card?.let { card -> CardArt(card); Text(card.s("name") + " → " + recipient.ifBlank { "请选择当前会话成员" }) }
            } else {
                Text("服务器确认已转移资产：${state.gift!!.s("id")}")
                state.card?.let { CardArt(it) }
                Button(onClick = model::announceGift, enabled = !state.busy) { Text("独立发送关联赠卡消息") }
            }
        }
    }, confirmButton = { if(state.gift == null) TextButton(onClick = { confirm = true }, enabled = state.card != null && recipient.isNotBlank() && !state.busy) { Text("预览并确认赠送") } else TextButton(onClick = close) { Text("完成") } }, dismissButton = { TextButton(onClick = close) { Text("关闭") } })
    if (confirm) AlertDialog(onDismissRequest = { confirm = false }, title = { Text("确认资产转移") }, text = { Text("将 ${state.card?.s("name")} 赠给 $recipient。此操作转移卡片所有权，关闭界面不能撤销已完成的赠送。") }, confirmButton = { TextButton(onClick = { model.giftCard(recipient); confirm = false }) { Text("明确确认赠送") } }, dismissButton = { TextButton(onClick = { confirm = false }) { Text("返回") } })
}
