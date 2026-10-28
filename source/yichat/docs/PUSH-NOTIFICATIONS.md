# YiChat 推送通知重构

状态：已纳入 Android 0.3.3（versionCode 11），接入外置 Firebase Android 配置和服务账号。Google 认证及 API 校验已执行；实际手机的 FCM 投递、Doze 和重启未首次解锁验证仍需在有 Google Play services 的设备完成。详见根目录 PRODUCTION-0.3.3.md。

## 现状与实现选择

原实现没有 Firebase。原生前台服务每 15 秒轮询 `/api/notifications`，WorkManager 每 15 分钟兜底，通知仅显示通用正文。聊天和私聊解密在共用的 Matrix JS SDK / Rust WASM / WebView IndexedDB 中，原生 PacketStore 不是聊天密钥库。新群按既有策略使用 HTTPS，私聊及旧版加密群继续端到端加密。

新增的 FCM 入口、轮询、UI 实时事件进入同一通知管道：

1. `onMessageReceived` 只校验白名单字段、读取 DP 显示名、立即 `notify`；不访问 Graph、网络或解密器。记录优先级和原始优先级，不记录 payload、token、正文或密钥。
2. 通知标识为 `tag = conversation:<conversation_id>`、`id = conversation_id.hashCode()`。额外 tag 避免不同会话的整数哈希碰撞。PendingIntent action 同样按会话隔离。
3. 占位标题来自本地显示名缓存，缺失为 `Yichat`，正文为 `你有新的消息`。私聊／群聊分别使用新的 `IMPORTANCE_HIGH` 通道。首次显示可响铃，已有同会话通知及替换使用 `setOnlyAlertOnce(true)`。
4. 使用 `setExpedited(RUN_AS_NON_EXPEDITED_WORK_REQUEST)`。unique work 名基于会话 ID，策略为 `APPEND_OR_REPLACE`。`KEEP` 会丢弃工作执行期间到达的新请求；追加任务可顺序补拉；开始网络前跳过旧代任务及已经替换成功的重复请求，最新任务一次取最多 20 条未读消息以合并突发推送。失败返回 success 并保留占位，避免反复重试、阻塞会话链。后续推送或重启解锁可再次补处理。旧代任务可为聚合提供已验证结果，但不能覆盖较新的占位通知。
5. Worker 复用当前 UI WebView 的 SDK；没有 Activity 时创建使用相同 origin、WebView 存储和 `yc:active-client` Web Lock 的后台 WebView。全进程串行运行此桥接，防止不同会话的后台 WebView 竞争一个密钥库。UI 优先，可中止后台实例；普通网页多标签锁不被强制抢占。
6. 服务器提供最多 20 个该会话的未读事件 ID，再由现有 Matrix API 拉消息。检查当前成员、静音、归档、已读、撤回以及事件身份。加密事件只调用现有 SDK 的解密／重试入口；不导出密钥，不另写原语。SDK 正常 `/sync` 接收并持久化 to-device 密钥，没有原生第二套同步器。
7. 成功以同一标识用 MessagingStyle 显示发送者及正文、合并消息。附件用类型标签，不下载附件。网络、密钥、解密、超时失败只写固定错误类别日志。45 秒原生超时；JS 解密等待上限 30 秒。用户已读／清除通知后，任务不会重新创建它。

## Direct Boot、隐私和权限

- `YiChatMessagingService` 声明 `directBootAware=true`，并包含 `firebase-messaging-directboot`。
- DP 的 `notification-names` 只保存“会话哈希 → 显示名”。独立 consent 设置保存通知开关和非秘密账号／设备指纹，用于账号隔离。没有 token、正文、消息密钥或 Matrix 数据库。
- 重启后未首次解锁：只显示占位，完全不构造 Graph／WebView／WorkManager。待处理路由 ID 及通知代号在系统通知 extras 中，不向 DP 名称缓存写消息。
- `ACTION_USER_UNLOCKED` **只支持动态接收**：Application 在未解锁进程中注册 runtime receiver。Manifest 的 `BOOT_COMPLETED` 处理进程在解锁前被回收的情况；`MY_PACKAGE_REPLACED` 处理升级。解锁时从仍可见的通知恢复任务。
- 消息通知为 `VISIBILITY_PRIVATE`，publicVersion 始终为 `Yichat / 你有新的消息`。系统设置若允许锁屏敏感内容，最终展示仍由 Android 的用户设置控制。
- Android 13+ 检查并申请 `POST_NOTIFICATIONS`。用户关闭 App 通知、拒绝权限或禁用通道时，App 不能绕过系统强制显示；注册端会上报禁用状态。用户正在看该会话且 Activity 处于前台时不弹通知，通知入口改为唤醒现有 UI 同步。前台的其他会话仍可通知。
- 退出、锁定、换账号和关闭通知会清理本地显示名／通知并取消内容任务。后台结果检查账号及通知代号；不把旧账号结果显示给新账号。

## 服务端配置及 payload

Android 构建设置环境变量 `YICHAT_FIREBASE_CONFIG`，指向仓库外的 `google-services.json`。Gradle 校验 `package_name=org.yitiao.yichat.mobile`，生成 FirebaseInitProvider 所需标准资源。未配置时不伪造项目、不注册 FCM，兼容轮询仍可用。服务账号私钥绝不可放入 Android 配置或 APK。

网关配置：

```ini
YICHAT_FCM_PROJECT=<真实 Firebase project_id>
YICHAT_FCM_CREDENTIALS=/etc/yichat/private/fcm-service-account.json
```

服务账号文件位于 Git 仓库外，限制为服务运行用户可读。启用 Firebase Cloud Messaging API，授予发送消息所需的 `cloudmessaging.messages.create` 权限。网关使用现有 PyJWT / cryptography 获取限于 `firebase.messaging` scope 的 OAuth2 access token，再调用 HTTPS HTTP v1 API。无需新增 Python 依赖。

`POST /api/push/device` 只接受已授权原生设备的有效网关会话。FCM token 由现有 Fernet 密钥加密存储。onNewToken 调度注册 worker，上报 SDK 当前 token；重新登录、启用、权限变化时重新注册。token 轮换不跳过待推送事件；重新启用从当前高水位开始。服务器独立调度器使用 DB lease、游标和重试退避，处理过期 token、主站会话撤销、静音、归档、已读、成员退出及撤回。

发送至 `https://fcm.googleapis.com/v1/projects/<project_id>/messages:send`：

```json
{
  "message": {
    "token": "<FCM registration token>",
    "android": {
      "priority": "high",
      "direct_boot_ok": true,
      "ttl": "86400s"
    },
    "data": {
      "conversation_id": "!room-id:chat.yitiao.org",
      "conversation_type": "private",
      "message_id": "$matrix-event-id"
    }
  }
}
```

群聊类型为 `group`。没有顶层或 Android `notification` 字段；data 没有昵称、群名、正文、附件数据或密钥。不要用 Firebase 控制台的 Notification 消息代替此协议。FCM message_id 与 Matrix message_id 是不同概念，此处 data 使用 Matrix 事件 ID。

没有配置 FCM 或注册失败的设备继续使用原有前台轮询。成功注册后停止常驻服务，保留周期对账与旧版本兼容。轮询也只拉元数据，并使用同一内容任务；不会产生额外原生 Matrix `/sync`。

## 真机验证

前提：使用包含真实 Firebase 配置、原签名和新 versionCode 的构建覆盖安装；不要卸载真实用户 App 或清除数据。在 App 中启用通知，并授予系统通知权限。准备两个测试账号，分别创建私聊和群聊。必须在有 Google Play services 的实机验证 FCM。

1. 前台、后台、移除最近任务分别发消息：应先看到本地会话名／通用占位，随后同一条通知更新。群聊可展开多条“发送者: 内容”。连发 10 条确认每个会话只有一条通知，替换不再次响铃。不同会话分别聚合。
2. 接收者正在该会话底部阅读：不显示系统通知，界面收到消息。在另一会话页时应该收到通知。读取后或手动划掉通知，在途 Worker 不得重新弹出旧通知。
3. 占位显示后切断网络，或使用没有对应会话密钥的**测试设备**：占位保留。正确恢复密钥后重新发送消息，确认可替换，且正常 UI 仍能解密。不得用生产密钥做故障注入。
4. Doze（先按 Home，屏幕熄灭，模拟拔电）：

```sh
adb shell dumpsys battery unplug
adb shell input keyevent KEYCODE_HOME
adb shell input keyevent KEYCODE_SLEEP
adb shell dumpsys deviceidle force-idle
adb shell dumpsys deviceidle
adb logcat -s YiChatPush
```

从另一设备发送真实 data-only high priority FCM。检查占位及时出现、日志含 `priority` 和 `originalPriority`，工作配额不足时降级为普通任务且占位保留。恢复：

```sh
adb shell dumpsys deviceidle unforce
adb shell dumpsys battery reset
```

5. 锁屏隐私：在系统中选择隐藏敏感通知，正常锁屏后发送消息。锁屏只显示公共版本；解锁后私有通知可显示本机解密内容。测试本地显示名缓存缺失时标题为 `Yichat`。
6. Direct Boot：为测试手机设置 PIN／密码，确认先登录并启用通知，然后 `adb reboot`；**不要输入 PIN**。此时发送真实 FCM，payload 必须有 `direct_boot_ok=true`。应只有占位通知，不启动解密、不访问 CE 存储。首次解锁后无需打开 App，应补拉并更新。再测试占位后进程被系统回收、随后首次解锁的情形。可用 `adb shell dumpsys user` 检查用户解锁状态；普通熄屏不等于 Direct Boot。
7. Android 13+ 分别拒绝／允许权限、禁用单个通道，再恢复。测试静音、归档、已读、退出群、主站退出、换账号和 token 轮换，不应收到不再有资格的消息正文。

“划掉最近任务”与系统设置中的“强行停止”不同。Android 强行停止应用会阻止投递，须用户重新启动解除；不要把它当作正常后台场景。

## 自动化验证与文件清单

单元测试分别覆盖纯 Kotlin 通知规则、SDK 解密边界和服务端 payload／注册／权限／游标／失效 token。Android instrumentation 使用真实 NotificationManager 验证占位、MessagingStyle、稳定标识、静默替换、publicVersion、失败保留及清除后不复活。真实 Matrix 测试使用隔离 Synapse、合成账号和 Chromium，覆盖关闭 UI 后新 Megolm 会话的后台解密及 UI 再打开；不冒充真实 FCM 测试。

运行普通检查：

```sh
cd source/yichat/client && npm test && npm run build
cd .. && python -m pytest -q tests/test_push.py tests/test_native_auth.py tests/test_gateway.py
cd ../../android
./gradlew --dependency-verification strict :app:testDebugUnitTest :app:assembleDebug :app:assembleDebugAndroidTest :app:lintRelease
adb shell am instrument -w -e class org.yitiao.yichat.PushNotificationsTest org.yitiao.yichat.mobile.test/androidx.test.runner.AndroidJUnitRunner
```

真实 Matrix 脚本须独占专用测试 PostgreSQL：启动 `tests/android_fixture.py`，使用新的私有临时目录，等 ready 文件出现；以 `client/test/browser/vite.config.ts` 构建测试入口并启动 Vite preview（避免开发预打包改变 WASM 路径），运行 `tests/browser/notification.mjs`。完成后在该临时目录创建 done 文件，等待服务退出。禁止同时执行其他清空同一测试数据库的 fixture。所有测试入口都不在生产 Vite 构建输入中。

改动文件按模块列出：

- Android：`NotificationPolicy.kt`、`PushNotifications.kt`、`NotificationContent.kt`、`Notifications.kt`、`MainActivity.kt`、`WebGraph.kt`、`WebShellCache.kt`、`AndroidManifest.xml`。
- 构建：`android/app/build.gradle.kts`、`android/app/gradle.lockfile`、`android/gradle/verification-metadata.xml`、`.gitignore`、`source/yichat/.gitignore`。
- 共用客户端：`chat.ts`、`main.tsx`、`notification-content.ts`、`notification-worker.ts`、`app-settings.tsx`、`notification.html`、`vite.config.ts`。
- 服务端：`push.py`、`conversations.py`、`app.py`、`config.py`、`db.py`、`deploy/yichat.env.example`。
- 测试：`NotificationPolicyTest.kt`、`PushNotificationsTest.kt`、`BackgroundNotificationsTest.kt`、`notification-content.test.ts`、`client/test/browser/{notification.html,notification.ts,vite.config.ts}`、`tests/browser/notification.mjs`、`tests/test_push.py`、`tests/test_gateway.py`（补足既有测试 fixture 的已确认成员证据）。
- 文档：根 README、CHANGELOG、本说明及验证结果文件。

完整逐项路径见 [改动文件清单](../../../delivery/push-refactor-20261027/changed-files.txt)。实际检查结果见 [PUSH-VALIDATION.md](PUSH-VALIDATION.md)。

参考：[Firebase 优先级](https://firebase.google.com/docs/cloud-messaging/android-message-priority)、[FCM Direct Boot](https://firebase.google.com/docs/cloud-messaging/customize-messages/android-direct-boot)、[Android Direct Boot](https://developer.android.com/privacy-and-security/direct-boot)、[WorkManager expedited](https://developer.android.com/develop/background-work/background-tasks/persistent/getting-started/define-work#expedited)。
