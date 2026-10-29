# YiChat Android 0.3.5 — 2026-10-29

包名 `org.yitiao.yichat.mobile`，versionName `0.3.5`，versionCode `13`，沿用 0.3.4 的签名。

[下载 APK](https://github.com/yitiaosg/YiChat/releases/download/android-20261029-v0.3.5/YiChat-0.3.5-13.apk) · [GitHub Release](https://github.com/yitiaosg/YiChat/releases/tag/android-20261029-v0.3.5) · [更新日志](CHANGELOG.md)

## 站内分享显示

已识别的站内 URL 从消息正文显示中移除，只显示对应预览；旁边的说明文字保留。会话列表、回复摘要、发送中及失败草稿采用同样规则。预览仍可打开原分享内容。普通外链、未识别路由、明确取消预览的消息及超过预览数量上限的链接保留原显示，避免无预览时丢失内容。

消息原包、URL 和接受时的 SHA-256 均不修改，不重新发送或回写历史消息；转账、红包、赠卡权限及资产逻辑保持不变。

## FCM 文字及类型摘要

新版原生注册请求增加 `preview_version: 1`。服务端为旧设备增加默认值为 0 的兼容列，不修改 token、游标和通知偏好。旧设备继续收到原三字段路由，新设备在原 data 中追加 `message_preview` 和 `recipient_tag`。仍是 Android high priority / direct_boot_ok 的纯 data 消息，不带 notification 字段。

普通消息直接显示文字。私聊的非文字及业务消息示例：

| 类型 | 通知正文 |
| --- | --- |
| 转账 | 对方向你转了一笔账 |
| 收款回执 | 对方已确认收款 |
| 赠卡 | 对方向你赠送了一张卡片 |
| 图片 | 对方发送了一张图片 |
| 语音 | 对方发送了一条语音 |
| 视频 | 对方发送了一段视频 |
| 文件 | 对方发送了一个文件 |
| 分享卡片 | 对方分享了一张卡片 |

群聊红包摘要为“发送了一个红包”，合并补拉后由 MessagingStyle 显示实际发送者。接收回调无需网络便显示 FCM 预览，后续继续补拉、核对并合并未读消息；网络或源数据异常时保留已显示的内容。

服务器只读取该接收者当前有权访问的未读、未撤回原包，核对事件身份和 SHA-256。发送前重新检查静音/已读、会话有效性和通知启用状态。预览最多 1200 UTF-8 字节，超长文字按完整码点截断，避免超过 FCM 4096 字节上限。非文字类型摘要不带原 URL 或附件名。服务端与客户端使用同一组用例验证摘要文案。

账号设备指纹与本地 Account.storage 一致，切换账号后迟到的旧正文被拒绝。正文不写入 DP 会话名缓存、自定义通知 extras 或 WorkManager 输入；首次解锁前只显示通用正文。保留 VISIBILITY_PRIVATE、公用版本、前台当前会话抑制、稳定通知标识、OnlyAlertOnce 和后台任务合并。锁屏最终可见性遵循系统用户设置。

## 数据与升级

本版不变更 `plain-20261029-v1`，不再次清空 0.3.4 之后的新消息，不修改钱包账本、托管款、卡片或现有资产凭证。旧版本覆盖安装即可，安装后打开一次并保持消息通知开启，使新版 FCM 协议注册生效；无需卸载或清除数据。网页与 App 下载统一指向 GitHub。

## 验证

- 推送与摘要测试 41 项通过，包括旧协议兼容、默认列迁移、UTF-8 截断、授权及原包哈希校验、静音/关闭通知竞态和日志不记录正文。
- 网关回归 17 项通过；真实隔离 Synapse + Chromium 的 6 个场景通过，包括纯预览、说明文字保留、会话摘要隐藏 URL、卡片和游戏渲染、无密钥读历史及附件原字节下载。
- Web 单元测试 17 项、TypeScript 与 Vite 构建通过。
- Android Release JVM 15 项、严格依赖验证、Release lint 通过。API28 的真实 NotificationManager 与 Firebase 初始化共 3 项通过：立即显示 FCM 预览、失败保留、锁屏公共版本和错误账号拒绝。
- 签名与 0.3.4 相同，网页 dist 与 APK 内置页面逐文件一致。结果见 delivery/preview-push-035-20261029。

真实 Google FCM 投递仍需有 Google Play services 的手机验证。覆盖安装后检查 App 打开、后台、划掉最近任务、锁屏及 Doze；用两个测试账号发送文字、转账、赠卡与附件，确认正文/类型提示。`adb shell dumpsys deviceidle force-idle` 进入 Doze，结束后用 `adb shell dumpsys deviceidle unforce` 退出。重启未首次解锁时应为通用提示，解锁后补拉；前台正在阅读该会话时不弹通知。
