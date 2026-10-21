package org.yitiao.yichat

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.browser.auth.AuthTabIntent
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.*
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.distinctUntilChanged
import androidx.navigation.compose.*
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.update
import kotlinx.serialization.json.*
import org.matrix.rustcomponents.sdk.EventOrTransactionId
import java.math.BigDecimal
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

class MainActivity : ComponentActivity() {
    private val model: AppModel by viewModels()
    private var consumedBrowserReturn: String? = null
    private val browserAuth = AuthTabIntent.registerActivityResultLauncher(this) { result ->
        when (result.resultCode) {
            AuthTabIntent.RESULT_OK -> result.resultUri?.let(::consumeBrowserUri)
            AuthTabIntent.RESULT_VERIFICATION_FAILED, AuthTabIntent.RESULT_VERIFICATION_TIMED_OUT ->
                model.state.update { it.copy(error = "浏览器未能验证登录回跳，请更新系统浏览器后重新登录") }
            // A legacy Custom Tab returns CANCELED even after delivering the
            // callback through onNewIntent. Do not erase the pending PKCE state.
        }
    }
    private var backupPassword = ""
    private var attachmentRoom: String? = null
    private var saveSource: String? = null
    private var recorder: android.media.MediaRecorder? = null
    private var recordingFile: java.io.File? = null
    private var voiceRoom: String? = null
    private val microphone = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if(granted) startRecording() else model.state.update { it.copy(error = "未授予麦克风权限，无法录音") }
    }
    @Suppress("DEPRECATION") private fun startRecording() {
        val dir = java.io.File(cacheDir,"temporary").apply { mkdirs() }
        recordingFile = java.io.File(dir,"voice-${System.currentTimeMillis()}.m4a")
        runCatching {
            recorder = android.media.MediaRecorder().apply {
                setAudioSource(android.media.MediaRecorder.AudioSource.MIC); setOutputFormat(android.media.MediaRecorder.OutputFormat.MPEG_4)
                setAudioEncoder(android.media.MediaRecorder.AudioEncoder.AAC); setAudioEncodingBitRate(96000); setAudioSamplingRate(44100)
                setOutputFile(recordingFile!!.absolutePath); setMaxDuration(240000)
                setOnInfoListener { _, what, _ -> if(what == android.media.MediaRecorder.MEDIA_RECORDER_INFO_MAX_DURATION_REACHED) stopRecording() }
                prepare(); start()
            }
            model.recording.value = true
        }.onFailure { recorder?.release(); recorder=null; recordingFile?.delete(); model.state.update { it.copy(error="无法启动录音，请检查麦克风和系统权限") } }
    }
    private fun stopRecording() {
        val active = recorder ?: return
        runCatching { active.stop() }.onSuccess { recordingFile?.let { model.voice.value = it to voiceRoom.orEmpty() } }.onFailure { recordingFile?.delete() }
        active.release(); recorder=null; model.recording.value=false
    }
    private fun voice(room: String) {
        if(recorder!=null) stopRecording()
        else { voiceRoom = room; microphone.launch(android.Manifest.permission.RECORD_AUDIO) }
    }
    private val selectAttachment = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        val room = attachmentRoom; attachmentRoom = null
        if (uri != null && room != null) model.act { model.graph.media.send(uri, room) }
    }
    private val saveAttachment = registerForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri ->
        val source = saveSource; saveSource = null
        if (uri != null && source != null) model.act { kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
            java.io.File(source).inputStream().use { input -> requireNotNull(contentResolver.openOutputStream(uri, "wt")).use { output -> input.copyTo(output) } }
        } }
    }
    private val selectTree = registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        val config = model.pendingExport
        model.pendingExport = null
        if (uri != null) {
            model.act {
                requireNotNull(config) { "目录选择期间应用已重启，请重新设置导出目录、天数和口令" }
                contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
                model.graph.backup.configure(uri, config.first, config.second)
            }
        }
    }
    private val restoreFile = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) model.act { model.graph.backup.restore(uri, backupPassword); backupPassword = "" }
    }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        consumedBrowserReturn = savedInstanceState?.getString("browser-return-digest")
        lifecycleScope.launch { model.state.map { it.account }.distinctUntilChanged().collect { if(it==null && recorder!=null) { stopRecording();model.voice.value?.first?.delete();model.voice.value=null } } }
        setContent { YiChat(model, ::browse, { p, days -> model.pendingExport = p to days; selectTree.launch(null) }, { p -> backupPassword = p; restoreFile.launch(arrayOf("application/octet-stream", "*/*")) }, { room -> attachmentRoom = room; selectAttachment.launch(arrayOf("*/*")) }, { path, name -> saveSource = path; saveAttachment.launch(name) }, ::voice) }
        consumeBrowserReturn(intent)
    }
    override fun onNewIntent(intent: Intent) { super.onNewIntent(intent); consumeBrowserReturn(intent) }
    private fun consumeBrowserReturn(incoming: Intent) {
        val uri = incoming.data
        incoming.data = null
        setIntent(incoming)
        uri?.let(::consumeBrowserUri)
    }
    private fun consumeBrowserUri(uri: Uri) {
        val digest = SecureStore.hash(uri.toString())
        if (digest != consumedBrowserReturn) {
            consumedBrowserReturn = digest
            model.callback(uri)
        }
    }
    override fun onSaveInstanceState(outState: Bundle) {
        consumedBrowserReturn?.let { outState.putString("browser-return-digest", it) }
        super.onSaveInstanceState(outState)
    }
    override fun onStart() { super.onStart(); model.resume() }
    override fun onStop() { stopRecording(); model.pause(); super.onStop() }
    private fun browse(uri: Uri) {
        runCatching { BrowserNavigator.open(this, uri, browserAuth) }
            .onFailure { model.state.update { it.copy(error = "无法打开系统浏览器授权窗口，请安装支持 Custom Tabs 的浏览器并设为默认浏览器") } }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable fun YiChat(model: AppModel, browse: (Uri) -> Unit, chooseBackup: (String, Int) -> Unit, restoreBackup: (String) -> Unit, attach: (String) -> Unit, save: (String,String) -> Unit, voice: (String) -> Unit) {
    val state by model.state.collectAsStateWithLifecycle()
    LaunchedEffect(state.account) { if(state.account==null) model.voice.value?.let { it.first.delete(); model.voice.value=null } }
    val rooms by model.rooms.collectAsStateWithLifecycle()
    val sync by model.sync.collectAsStateWithLifecycle()
    val nav = rememberNavController()
    val activity = requireNotNull(androidx.activity.compose.LocalActivity.current)
    val entry by nav.currentBackStackEntryAsState()
    DisposableEffect(state.keyResult, entry?.destination?.route) {
        if (state.keyResult != null || entry?.destination?.route == "settings") activity.window.addFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE)
        else activity.window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE)
        onDispose { activity.window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE) }
    }
    val snackbar = remember { SnackbarHostState() }
    val dark = state.dark ?: isSystemInDarkTheme()
    MaterialTheme(colorScheme = if (dark) darkColorScheme() else lightColorScheme()) {
        LaunchedEffect(state.error) { state.error?.let { snackbar.showSnackbar(it) } }
        Scaffold(snackbarHost = { SnackbarHost(snackbar) }, topBar = {
            TopAppBar(title = { Column { Text("YiChat", fontWeight = FontWeight.Bold); Text(sync, style = MaterialTheme.typography.labelSmall) } },
                navigationIcon = { if (state.room != null) TextButton(onClick = model::closeRoom) { Text("返回") } },
                actions = { if (state.account != null) { TextButton(onClick = { nav.navigate("new") }) { Text("新建") }; TextButton(onClick = { nav.navigate("settings") }) { Text("设置") } } })
        }) { padding ->
            Box(Modifier.padding(padding).fillMaxSize()) {
                if (state.account == null) {
                    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(28.dp), verticalArrangement = Arrangement.Center) {
                        Text("与一条相连", style = MaterialTheme.typography.headlineLarge)
                        Spacer(Modifier.height(16.dp)); Text("使用系统浏览器完成主站登录与两步验证。私聊由 Matrix 原生端到端加密，群聊为明文。")
                        Spacer(Modifier.height(24.dp))
                        Button(onClick = { model.login(true,browse) }, enabled = !state.busy, modifier = Modifier.fillMaxWidth()) { Text("使用主站账号登录") }
                        OutlinedButton(onClick = { model.login(false,browse) }, enabled = !state.busy, modifier = Modifier.fillMaxWidth()) { Text("使用其他账号或创建新设备") }
                        TextButton(onClick = model::cancelLogin, enabled = !state.busy) { Text("取消本次浏览器登录") }
                    }
                } else NavHost(navController = nav, startDestination = "chats") {
                    composable("chats") {
                        BoxWithConstraints(Modifier.fillMaxSize()) {
                            val wide = maxWidth >= 760.dp
                            Row(Modifier.fillMaxSize()) {
                                if (state.room == null || wide) ConversationList(rooms, state.room, Modifier.weight(if (wide) .35f else 1f), model::openRoom, model::join)
                                if (state.room != null) ChatScreen(model, browse, attach, voice, Modifier.weight(if (wide) .65f else 1f))
                            }
                        }
                    }
                    composable("new") { NewConversation(model) { nav.popBackStack() } }
                    composable("settings") { Settings(model, browse, chooseBackup, restoreBackup) { nav.popBackStack() } }
                }
                if (state.busy) LinearProgressIndicator(Modifier.fillMaxWidth().align(Alignment.TopCenter))
            }
        }
        state.order?.let { OrderDialog(model, it, browse) }
        if(state.newDeviceNotice) AlertDialog(onDismissRequest=model::acknowledgeDevice,title={Text("新设备与历史密钥")},text={Text("本次建立了新的 Matrix 设备。请前往设置验证设备，并选择创建或恢复原生加密密钥备份。缺少旧会话密钥时，旧私聊无法解密；消息包备份和 Matrix 恢复密钥是两件事。")},confirmButton={TextButton(onClick={model.acknowledgeDevice();nav.navigate("settings")}){Text("前往验证与密钥恢复")}},dismissButton={TextButton(onClick=model::acknowledgeDevice){Text("我已了解，稍后处理")}})
        val media by model.mediaPreview.collectAsStateWithLifecycle()
        media?.let { AttachmentViewer(it.first, it.second.path(), { save(it.second.path(), it.first.name) }, model::closeAttachment) }
        val recorded by model.voice.collectAsStateWithLifecycle()
        recorded?.let { (file,room) -> AlertDialog(onDismissRequest = { file.delete(); model.voice.value = null }, title = { Text("发送录音？") }, text = { Text("录音已停止，${file.length()} 字节。确认后上传到录音开始时的会话，私聊按 Matrix 标准加密。") }, confirmButton = { TextButton(onClick = { model.act { try { model.graph.media.send(Uri.fromFile(file),room,"audio/mp4") } finally { file.delete() } }; model.voice.value = null }) { Text("确认上传并发送") } }, dismissButton = { TextButton(onClick = { file.delete(); model.voice.value=null }) { Text("删除录音") } }) }
        state.keyResult?.let { key -> AlertDialog(onDismissRequest = model::clearKey, title = { Text("保存 Matrix 恢复密钥") }, text = { SelectionContainer { Text("请离线妥善保存，勿发到聊天中。它用于恢复加密身份，不是消息包备份口令。\n\n$key") } }, confirmButton = { TextButton(onClick = model::clearKey) { Text("我已妥善保存") } }) }
    }
}

@Composable private fun ConversationList(rooms: List<Conversation>, selected: String?, modifier: Modifier, open: (String) -> Unit, join: (String) -> Unit) {
    var search by remember { mutableStateOf("") }
    var unreadOnly by remember { mutableStateOf(false) }
    Column(modifier.fillMaxHeight()) {
        OutlinedTextField(search, { search = it }, label = { Text("搜索会话") }, modifier = Modifier.fillMaxWidth().padding(12.dp), singleLine = true)
        Row(verticalAlignment = Alignment.CenterVertically) { Checkbox(unreadOnly, { unreadOnly = it }); Text("仅未读") }
        if (rooms.isEmpty()) Text("尚无已同步会话。可新建会话，或等待同步完成。", Modifier.padding(20.dp))
        LazyColumn { items(rooms.filter { it.name.contains(search, true) && (!unreadOnly || it.unread > 0 || it.invited) }, key = { it.id }) { room ->
            ListItem(headlineContent = { Text(room.name) }, supportingContent = { Text(if (room.invited) "收到邀请" else if (room.encrypted) "端到端加密" else "群聊 · 明文") },
                trailingContent = { if (room.invited) TextButton(onClick = { join(room.id) }) { Text("接受") } else if (room.unread > 0) Badge { Text(room.unread.toString()) } },
                modifier = Modifier.clickable { open(room.id) }.background(if (selected == room.id) MaterialTheme.colorScheme.secondaryContainer else MaterialTheme.colorScheme.surface))
            HorizontalDivider()
        } }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable private fun ChatScreen(model: AppModel, browse: (Uri) -> Unit, attach: (String) -> Unit, voice: (String) -> Unit, modifier: Modifier) {
    val state by model.state.collectAsStateWithLifecycle()
    val messages by model.messages.collectAsStateWithLifecycle()
    val room = state.room ?: return
    val progress by model.graph.media.progress.collectAsStateWithLifecycle()
    val recording by model.recording.collectAsStateWithLifecycle()
    val list = rememberLazyListState()
    val coroutine = rememberCoroutineScope()
    var text by remember(room) { mutableStateOf("") }
    var search by remember(room) { mutableStateOf("") }
    var reply by remember(room) { mutableStateOf<ChatMessage?>(null) }
    var edit by remember(room) { mutableStateOf<ChatMessage?>(null) }
    var selected by remember(room) { mutableStateOf<ChatMessage?>(null) }
    var details by remember { mutableStateOf(false) }
    var money by remember { mutableStateOf(false) }
    var gifting by remember { mutableStateOf(false) }
    val atBottom by remember { derivedStateOf { list.layoutInfo.visibleItemsInfo.lastOrNull()?.index == list.layoutInfo.totalItemsCount - 1 || list.layoutInfo.totalItemsCount == 0 } }
    var newMessages by remember(room) { mutableStateOf(false) }
    var previousLast by remember(room) { mutableStateOf<String?>(null) }
    LaunchedEffect(messages.lastOrNull()?.key) {
        val newest = messages.lastOrNull()?.key
        if (newest != previousLast) {
            if (atBottom && search.isEmpty()) { if (messages.isNotEmpty()) list.animateScrollToItem(messages.lastIndex); model.graph.matrix.read() }
            else newMessages = true
            previousLast = newest
        }
    }
    Column(modifier.fillMaxHeight()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(state.roomInfo?.s("kind")?.let { if (it == "direct") "私聊" else "群聊" } ?: "会话", Modifier.weight(1f), style = MaterialTheme.typography.titleLarge)
            TextButton(onClick = { model.wallet(); money = true }) { Text("转账/红包") }
            TextButton(onClick = { model.roomDetails(); model.cards(1); gifting = true }) { Text("赠卡") }
            TextButton(onClick = { model.roomDetails(); details = true }) { Text("详情") }
        }
        OutlinedTextField(search, { search = it }, label = { Text("搜索已加载消息") }, singleLine = true, modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp))
        TextButton(onClick = model::history, modifier = Modifier.align(Alignment.CenterHorizontally)) { Text("加载更早历史") }
        LazyColumn(state = list, modifier = Modifier.weight(1f).fillMaxWidth(), contentPadding = PaddingValues(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            items(messages.filter { search.isBlank() || it.body.contains(search, true) }, key = { it.key }) { message ->
                Column(Modifier.fillMaxWidth(), horizontalAlignment = if (message.own) Alignment.End else Alignment.Start) {
                    Text(message.sender.substringBefore(":"), style = MaterialTheme.typography.labelSmall)
                    Surface(color = if (message.own) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceContainerHighest,
                        shape = MaterialTheme.shapes.medium, modifier = Modifier.widthIn(max = 340.dp).combinedClickable(onClick = {}, onLongClick = { selected = message })) {
                        Column(Modifier.padding(12.dp)) {
                            SelectionContainer { Text(message.body) }
                            if (!message.redacted && !message.encryptedFailure) ShareLinks.extract(message.body).forEach { Preview(model, it, room, browse, if(message.receipt) message.sender else null) }
                            if (!message.redacted) message.giftId?.let { GiftPreview(model,it,room) }
                            if (!message.redacted) message.attachment?.let { media -> TextButton(onClick = { model.openAttachment(media) }) { Text("下载附件 · ${media.name}") } }
                            Text(displayTime(message.timestamp), style = MaterialTheme.typography.labelSmall)
                            if (message.pending) Text("发送中", style = MaterialTheme.typography.labelSmall)
                            if (message.failed) TextButton(onClick = { model.act { model.graph.matrix.retry(message) } }) { Text("发送失败 · 重试原消息") }
                        }
                    }
                }
            }
        }
        if (newMessages) TextButton(onClick = { coroutine.launch { if (messages.isNotEmpty()) list.animateScrollToItem(messages.lastIndex); newMessages = false; model.graph.matrix.read() } }, modifier = Modifier.align(Alignment.CenterHorizontally)) { Text("有新消息 ↓") }
        if (reply != null || edit != null) Row(Modifier.padding(horizontal = 12.dp)) { Text((if (edit != null) "编辑：" else "回复：") + (edit ?: reply)!!.body.take(60), Modifier.weight(1f)); TextButton(onClick = { reply = null; edit = null; text = "" }) { Text("取消") } }
        progress?.let { p -> Row(verticalAlignment = Alignment.CenterVertically) { Text("上传 ${p.first} / ${p.second} 字节", Modifier.weight(1f)); TextButton(onClick = { model.graph.gateway.cancelUpload() }) { Text("取消上传") } } }
        TextButton(onClick = { voice(room) }, enabled = progress == null && !state.busy) { Text(if(recording) "正在录音 · 点击停止（最长4分钟）" else "录音（使用时申请麦克风）") }
        Row(Modifier.imePadding().navigationBarsPadding().padding(8.dp), verticalAlignment = Alignment.Bottom) {
            TextButton(onClick = { attach(room) }, enabled = progress == null && !state.busy) { Text("附件") }
            OutlinedTextField(text, { if (it.codePointCount(0, it.length) <= 2000) text = it }, label = { Text("消息") }, modifier = Modifier.weight(1f), maxLines = 5)
            Button(onClick = { val submitted = text; model.send(submitted, reply, edit) { if(text==submitted) text=""; reply=null;edit=null } }, enabled = text.isNotBlank() && !state.busy, modifier = Modifier.padding(start = 8.dp)) { Text("发送") }
        }
    }
    selected?.let { m -> AlertDialog(onDismissRequest = { selected = null }, title = { Text("消息操作") }, text = {
        Column {
            if (m.id is EventOrTransactionId.EventId && !m.redacted && !m.encryptedFailure) TextButton(onClick = { reply = m; selected = null }) { Text("回复") }
            if (m.editable && !m.redacted && m.giftId == null && ShareLinks.extract(m.body).none { "/orders/" in it }) TextButton(onClick = { edit = m; text = m.body; selected = null }) { Text("编辑") }
            if (m.own && !m.redacted && m.giftId == null && m.timestamp > System.currentTimeMillis() - 300000 && ShareLinks.extract(m.body).none { "/orders/" in it }) TextButton(onClick = { model.act { model.graph.matrix.redact(m) }; selected = null }) { Text("撤回（服务器复核5分钟限制）") }
            if (m.failed) TextButton(onClick = { model.act { model.graph.matrix.retry(m) }; selected = null }) { Text("重试原事务") }
        }
    }, confirmButton = { TextButton(onClick = { selected = null }) { Text("关闭") } }) }
    if (details) RoomDetails(model) { details = false }
    if (money) CreateOrder(model, browse) { money = false }
    if (gifting) GiftDialog(model) { gifting = false }
}

@Composable private fun NewConversation(model: AppModel, done: () -> Unit) {
    val state by model.state.collectAsStateWithLifecycle()
    var q by remember { mutableStateOf("") }; var group by remember { mutableStateOf(false) }; var name by remember { mutableStateOf("") }
    var picked by remember { mutableStateOf(setOf<String>()) }
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Text("新建会话", style = MaterialTheme.typography.headlineSmall)
        Row(verticalAlignment = Alignment.CenterVertically) { Checkbox(group, { group = it; picked = emptySet() }); Text("群聊（明文）") }
        if (group) OutlinedTextField(name, { name = it }, label = { Text("群名称") })
        OutlinedTextField(q, { q = it }, label = { Text("用户名开头") })
        Button(onClick = { model.search(q) }, enabled = q.isNotBlank()) { Text("查询真实用户") }
        LazyColumn(Modifier.weight(1f)) { items(state.profiles.toList()) { p -> val person = p.jsonObject; val user = person.s("username")
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) { Checkbox(user in picked, { picked = if (group) if (user in picked) picked - user else picked + user else setOf(user) }); Text(person.s("nickname").ifBlank { user } + " · " + user) }
        } }
        Button(onClick = { model.createRoom(picked.toList(), group, name, done) }, enabled = picked.isNotEmpty() && !state.busy) { Text("打开或创建会话") }
        TextButton(onClick = done) { Text("返回会话") }
    }
}

@Composable private fun RoomDetails(model: AppModel, close: () -> Unit) {
    val state by model.state.collectAsStateWithLifecycle(); var invite by remember { mutableStateOf("") }; var leaving by remember { mutableStateOf(false) }
    var role by remember { mutableStateOf<Pair<String, Boolean>?>(null) }
    AlertDialog(onDismissRequest = close, title = { Text("会话详情") }, text = { Column(Modifier.verticalScroll(rememberScrollState())) {
        Text(state.room.orEmpty()); Text("加密模式：${state.roomInfo?.s("encryption")}")
        state.profiles.forEach { p -> val person = p.jsonObject
            Text(person.s("nickname") + " @" + person.s("username") + "\n" + person.s("biography"), Modifier.padding(vertical = 8.dp))
            val ownPower = state.power?.o("users")?.get(state.account?.mxid.orEmpty())?.jsonPrimitive?.intOrNull ?: 0
            val target = person.s("mxid"); val targetPower = state.power?.o("users")?.get(target)?.jsonPrimitive?.intOrNull ?: 0
            Text("权限等级 $targetPower", style = MaterialTheme.typography.labelSmall)
            if (state.roomInfo?.s("kind") == "group" && ownPower >= 100 && target != state.account?.mxid && targetPower < ownPower)
                TextButton(onClick = { role = target to (targetPower < 50) }) { Text(if (targetPower < 50) "设为管理员" else "设为普通成员") }
        }
        if (state.roomInfo?.s("kind") == "group") { OutlinedTextField(invite, { invite = it }, label = { Text("邀请用户名（服务器复核权限）") }); TextButton(onClick = { model.invite(invite) }, enabled = invite.isNotBlank()) { Text("发送邀请") } }
        TextButton(onClick = { leaving = true }) { Text("本人退出会话") }
    } }, confirmButton = { TextButton(onClick = close) { Text("关闭") } })
    if (leaving) Confirm("确认本人退出？", "退出后将不能继续发送消息，本地历史和设备密钥保留。", { model.leave(); leaving = false; close() }) { leaving = false }
    role?.let { (id, promote) -> Confirm("确认修改权限", "$id → ${if (promote) "管理员 50" else "普通成员 0"}，最终权限由服务器检查。", { model.moderator(id, promote); role = null }) { role = null } }
}

@Composable private fun CreateOrder(model: AppModel, browse: (Uri) -> Unit, close: () -> Unit) {
    val s by model.state.collectAsStateWithLifecycle(); var amount by remember { mutableStateOf("") }; var count by remember { mutableStateOf("1") }; var title by remember { mutableStateOf("") }; var confirm by remember { mutableStateOf(false) }
    val group = s.roomInfo?.s("kind") == "group"; val wallet = s.wallet
    val target = s.roomInfo?.let { if(it.s("peer")==s.account?.mxid) it.s("owner") else it.s("peer") }.orEmpty()
    AlertDialog(onDismissRequest = close, title = { Text(if (group) "群红包" else "私聊转账") }, text = { Column(Modifier.verticalScroll(rememberScrollState())) {
        if (wallet == null) Text("正在读取真实钱包状态")
        else {
            if (!wallet.b("enabled")) Text("服务器当前未启用资金功能")
            Text("付款钱包：${wallet.s("binding").ifBlank { "未绑定" }}")
            if (wallet.s("binding").isBlank()) TextButton(onClick = { browse(Uri.parse(wallet.s("bind_url"))) }) { Text("前往真实 YIDT 绑定") }
            Text("对象：${target.ifBlank { model.rooms.value.firstOrNull { it.id==s.room }?.name ?: s.room.orEmpty() }}")
            OutlinedTextField(amount, { amount = it }, label = { Text("本金 YIDT（精确十进制）") })
            if (group) OutlinedTextField(count, { count = it }, label = { Text("红包份数 1–1000") })
            OutlinedTextField(title, { title = it.take(100) }, label = { Text("标题") })
            val total = runCatching { BigDecimal(amount).add(BigDecimal(wallet.o("fees").s("incoming"))).add(BigDecimal(wallet.o("fees").s("outgoing_per_share")).multiply(BigDecimal(if (group) count else "1"))) }.getOrNull()
            Text("入金费：${wallet.o("fees").s("incoming")}；每份出金费：${wallet.o("fees").s("outgoing_per_share")}\n预计总扣款：${total?.toPlainString() ?: "请填写有效金额/份数"}")
            Text("创建不扣款。YIDT 批准返回后，仍需在 App 明确确认入金。")
        }
    } }, confirmButton = { TextButton(onClick = { confirm = true }, enabled = wallet?.b("enabled") == true && wallet.s("binding").isNotBlank() && amount.isNotBlank()) { Text("核对并创建") } }, dismissButton = { TextButton(onClick = close) { Text("关闭") } })
    if (confirm) Confirm("确认金额和对象", "本金 $amount YIDT，${if(group) count else "1"} 份。订单创建后再核对冻结钱包与费用。", { model.createOrder(amount, if (group) count.toIntOrNull() ?: 0 else 1, title); confirm = false; close() }) { confirm = false }
}

@Composable private fun OrderDialog(model: AppModel, order: JsonObject, browse: (Uri) -> Unit) {
    val state by model.state.collectAsStateWithLifecycle()
    var action by remember(order.s("id")) { mutableStateOf<String?>(null) }
    val terminal = order.s("state") in listOf("paid", "refunded", "cancelled")
    LaunchedEffect(order.s("id"), terminal) { while (!terminal) { delay(20000); model.pollOrder(order.s("id")) } }
    AlertDialog(onDismissRequest = model::dismissOrder, title = { Text(if (order.s("kind") == "direct") "转账" else order.s("title").ifBlank { "群红包" }) }, text = {
        Column(Modifier.verticalScroll(rememberScrollState())) {
            Text(order.s("amount") + " YIDT", style = MaterialTheme.typography.headlineMedium)
            Text("状态：${order.s("state")}\n付款：${order.s("sender")}\n收款：${order.s("recipient").ifBlank { "符合条件的群成员" }}\n来源：${order.s("source")}\n目标：${order.s("destination")}\n入金费：${order.s("incoming_fee")}\n出金预留：${order.s("outgoing_fee_reserve")}")
            order["expires"]?.jsonPrimitive?.longOrNull?.let { Text("截止：${displayTime(it * 1000)}") }
            order.a("claims").forEach { c -> val x = c.jsonObject; Text("${x.s("user")} · ${x.s("amount")} YIDT · ${x.s("state")}") }
            if (order.s("my_amount").isNotBlank()) Text("本人金额：${order.s("my_amount")}")
            if (order.s("best").isNotBlank()) Text("手气最佳：${order.s("best")}")
            if (order["refund"] is JsonObject) Text("退款：${order.o("refund").s("amount")} · ${order.o("refund").s("state")}")
            if (order.s("authorize_url").isNotBlank()) TextButton(onClick = { browse(Uri.parse(order.s("authorize_url"))) }) { Text("系统浏览器打开 YIDT 批准") }
            for ((a, label) in listOf("fund" to "确认入金", "receive" to "主动收款", "claim" to "领取红包", "cancel" to "取消订单")) if (order.o("actions").b(a)) OutlinedButton(onClick = { action = a }) { Text(label) }
            if (order.s("deposit").isNotBlank() && order.s("sender_uid") == state.account?.uid) TextButton(onClick = { model.sendOrder() }) { Text("发送关联资金气泡") }
            if (order.s("state") == "paid" && order.s("recipient_uid") == state.account?.uid) TextButton(onClick = { model.sendOrder(true) }) { Text("发送已收款回执") }
        }
    }, confirmButton = { TextButton(onClick = model::dismissOrder) { Text("关闭") } }, dismissButton = { TextButton(onClick = { model.showOrder(order.s("id")) }) { Text("查询原订单") } })
    action?.let { a -> Confirm(if (a == "cancel") "确认取消订单" else "确认此操作", if (a == "cancel") "本次不再入金、不发送消息。已入金订单不能取消，取消不是退款。" else "对原订单 ${order.s("id")} 执行 $a。金额与结果由服务器确认。", { model.orderAction(a,order.s("id")); action = null }) { action = null } }
}

@Composable private fun Settings(model: AppModel, browse: (Uri) -> Unit, choose: (String, Int) -> Unit, restore: (String) -> Unit, close: () -> Unit) {
    val state by model.state.collectAsStateWithLifecycle(); val backup by model.graph.backup.status.collectAsStateWithLifecycle(); val history by model.graph.history.status.collectAsStateWithLifecycle()
    var password by remember { mutableStateOf("") }; var recovery by remember { mutableStateOf("") }; var logout by remember { mutableStateOf(false) }
    var records by remember { mutableStateOf(false) }
    val exportDays by model.graph.backup.exportDays.collectAsStateWithLifecycle()
    val exportStatus by model.graph.backup.exportStatus.collectAsStateWithLifecycle()
    var days by remember { mutableStateOf("1") }
    LaunchedEffect(exportDays) { exportDays?.let { days = it.toString() } }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("账号与安全", style = MaterialTheme.typography.headlineMedium)
        Text("${state.account?.user}\nUID ${state.account?.uid}\n${state.account?.mxid}\n设备 ${state.account?.device}")
        TextButton(onClick = { browse(Uri.parse("https://yitiao.org/account/")) }) { Text("系统浏览器修改头像、资料及主站安全设置") }
        OutlinedButton(onClick={ records=true }) { Text("本人业务记录 · 核实未知结果") }
        Row { TextButton(onClick = { model.theme(false) }) { Text("浅色") }; TextButton(onClick = { model.theme(true) }) { Text("深色") }; TextButton(onClick = { model.theme(null) }) { Text("跟随系统") } }
        Text("Matrix 密钥备份", style = MaterialTheme.typography.titleLarge)
        Text("此处恢复身份与历史会话密钥；服务器已删除的消息包需另外恢复。")
        Button(onClick = model::enableKeyBackup) { Text("创建原生加密密钥备份及恢复密钥") }
        OutlinedTextField(recovery, { recovery = it }, label = { Text("Matrix 恢复密钥") }, visualTransformation = PasswordVisualTransformation())
        OutlinedButton(onClick = { model.recoverKeys(recovery); recovery = "" }, enabled = recovery.isNotBlank()) { Text("从服务器恢复原生密钥") }
        OutlinedButton(onClick = model::setupVerification) { Text("打开原生设备验证") }
        if (state.verificationStage.isNotBlank()) {
            Text(state.verificationStage); state.sas?.let { Text(it, style = MaterialTheme.typography.headlineSmall) }
            Row { TextButton(onClick = { model.verify("request") }) { Text("请求验证") }; TextButton(onClick = { model.verify("accept") }) { Text("接受") }; TextButton(onClick = { model.verify("start") }) { Text("开始 SAS") } }
            if (state.sas != null) Button(onClick = { model.verify("approve") }) { Text("双方完全一致，批准") }
            TextButton(onClick = { model.verify("cancel") }) { Text("取消验证") }
        }
        HorizontalDivider(); Text("原消息包自动备份", style = MaterialTheme.typography.titleLarge); Text(history); Text(backup)
        Text("自动保存在应用私有 data 目录，无需选择目录或设置口令。内部数据由系统密钥保护；卸载应用会删除内部数据。")
        TextButton(onClick = { model.act { model.graph.backup.saveInternal(force = true) } }, enabled = !state.busy) { Text("立即保存内部备份") }
        Text("外部加密导出", style = MaterialTheme.typography.titleMedium); Text(exportStatus)
        OutlinedTextField(days, { days = it.filter(Char::isDigit).take(4) }, label = { Text("导出间隔（整天，1–3650）") }, singleLine = true,
            keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = androidx.compose.ui.text.input.KeyboardType.Number))
        OutlinedTextField(password, { password = it }, label = { Text("独立导出/恢复口令，至少12字符") }, visualTransformation = PasswordVisualTransformation())
        Button(onClick = { choose(password, requireNotNull(days.toIntOrNull())); password = "" }, enabled = !state.busy && password.length >= 12 && days.toIntOrNull() in 1..3650) { Text("选择目录并启用按天导出") }
        if (exportDays != null) {
            TextButton(onClick = { model.act { model.graph.backup.export() } }, enabled = !state.busy) { Text("立即导出到已选目录") }
            TextButton(onClick = { model.act { model.graph.backup.stopExport() } }, enabled = !state.busy) { Text("停用外部自动导出") }
        }
        OutlinedButton(onClick = { restore(password); password = "" }, enabled = password.length >= 12) { Text("从外部加密消息包备份恢复") }
        Text("归档使用 UTC 自然日；消息时间按系统时区显示。没有推送服务，进程退出后不保证实时收信。")
        Text("此版本恢复入口会验证并保存原消息包。服务器已清理的包尚未接入 SDK 时间线，暂不能在聊天直接显示；恢复原生密钥不会替代这一步。")
        TextButton(onClick = { browse(Uri.parse("https://chat.yitiao.org/archives/")) }) { Text("原站归档下载与本地校验说明") }
        TextButton(onClick = { logout = true }) { Text("退出 YiChat 并锁定本地数据") }
        TextButton(onClick = { browse(Uri.parse("https://yitiao.org/account/")) }) { Text("主站登录与全站退出设置") }
        TextButton(onClick = close) { Text("返回会话") }
    }
    if (logout) Confirm("退出 YiChat？", "撤销当前 SID 关联聊天会话，停止同步并锁定本地库。保留历史、设备密钥及用户备份。", { model.logout(); logout = false }) { logout = false }
    if (records) BusinessRecords(model) { records=false }
}

@Composable private fun Confirm(title: String, body: String, confirm: () -> Unit, dismiss: () -> Unit) {
    AlertDialog(onDismissRequest = dismiss, title = { Text(title) }, text = { Text(body) }, confirmButton = { TextButton(onClick = confirm) { Text("明确确认") } }, dismissButton = { TextButton(onClick = dismiss) { Text("返回") } })
}
fun displayTime(ms: Long): String = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss z").withZone(ZoneId.systemDefault()).format(Instant.ofEpochMilli(ms))
