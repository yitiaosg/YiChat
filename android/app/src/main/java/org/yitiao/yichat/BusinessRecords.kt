package org.yitiao.yichat

import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.flow.flowOf

@Composable fun BusinessRecords(model: AppModel, close: () -> Unit) {
    val state by model.state.collectAsStateWithLifecycle()
    val records by remember(state.account?.storage) { model.graph.matrix.database?.local()?.operations() ?: flowOf(emptyList()) }.collectAsStateWithLifecycle(emptyList())
    var retry by remember { mutableStateOf<Operation?>(null) }
    AlertDialog(onDismissRequest=close,title={ Text("本人业务与恢复记录") },text={
        Column(Modifier.heightIn(max=500.dp).verticalScroll(rememberScrollState()),verticalArrangement=Arrangement.spacedBy(12.dp)) {
            val visible=records.filter { it.kind in listOf("order","gift","business_message","gift_message") }
            if(visible.isEmpty()) Text("暂无本机业务记录")
            for(op in visible) {
                val d=runCatching { model.graph.business.decode(op) }.getOrNull() ?: continue
                Text(when(op.kind) { "order" -> "转账/红包 · ${d.s("amount")} YIDT"; "gift" -> "赠卡给 ${d.s("recipient")}"; else -> "关联消息出站记录" },style=MaterialTheme.typography.titleSmall)
                Text("${op.state} · ${displayTime(op.updated)}",style=MaterialTheme.typography.labelSmall)
                when(op.kind) {
                    "order" -> TextButton(onClick={ model.recoverOrder(op) }) { Text("查询或核实原订单（不入金）") }
                    "gift" -> {
                        TextButton(onClick={ model.recoverGift(op) }) { Text("只读查询原赠送") }
                        if(op.state=="pending") TextButton(onClick={ retry=op }) { Text("对原卡片重试原赠送") }
                        if(op.state=="transferred") TextButton(onClick={ model.announceRecoveredGift(op) }) { Text("独立发送原赠卡关联消息") }
                    }
                    else -> Text("消息发送失败时请返回原会话，对原消息重试；此记录不会再次执行资金或资产操作。",style=MaterialTheme.typography.bodySmall)
                }
                HorizontalDivider()
            }
        }
    },confirmButton={ TextButton(onClick=close) { Text("关闭") } })
    retry?.let { op -> AlertDialog(onDismissRequest={retry=null},title={Text("确认重试原赠送")},text={Text("保持原卡片、接收人和业务请求身份。若服务器已经完成，将返回原结果；不会再转移一次资产。")},confirmButton={TextButton(onClick={model.recoverGift(op,true);retry=null}){Text("明确确认")}},dismissButton={TextButton(onClick={retry=null}){Text("返回")}}) }
}
