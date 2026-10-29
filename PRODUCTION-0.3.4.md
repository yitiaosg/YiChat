# YiChat Android 0.3.4 — 2026-10-29

包名 `org.yitiao.yichat.mobile`，versionName `0.3.4`，versionCode `12`，沿用原专用签名。

[下载 APK](https://github.com/yitiaosg/YiChat/releases/download/android-20261029-v0.3.4/YiChat-0.3.4-12.apk) · [GitHub Release](https://github.com/yitiaosg/YiChat/releases/tag/android-20261029-v0.3.4) · [更新日志](CHANGELOG.md)

## 行为

本次按站点所有者明确要求停用聊天端到端加密，并执行一次旧聊天清理。私聊、群聊和附件通过 HTTPS 传输，服务器可以读取正文，不再需要会话密钥。前端不调用 initRustCrypto，不查询设备密钥、不导入恢复密钥、不加密上传附件；服务端拒绝新的 m.room.encrypted 和 m.room.encryption 事件。

旧 Matrix 房间通过官方管理员删除 API 清理，重建两个普通空私聊，保留联系人关系。旧聊天消息、聊天附件和聊天归档清理；钱包账本、交易哈希、资产订单和赠卡凭证保留。旧会话标记 retired，禁止新内容；历史资金凭证以不可变 UID 验证只读访问。清理前的未领取指定托管款通过正常退款出账流程处理，重复执行不会再次付款；不直接修改余额或篡改历史交易。

客户端消息 epoch 为 plain-20261029-v1。网页清理旧聊天 IndexedDB，保留账户业务库的资金操作，清理该库内旧聊天包、撤回缓存与水位。App 首次升级清理旧聊天原包、内部快照和旧水位，保留钱包业务操作，设置 epoch 后不会再次清理新消息。不需要卸载或清除应用数据。离线设备须升级并打开后才能清理本地副本；用户自行导出的文件无法由服务器远程删除，旧导出也不能通过当前认证摘要恢复为新记录。

SHA-256 原包、附件、整日封存校验和自动备份继续保留；PostgreSQL 归档 schema、摘要不可变触发器、角色权限及未来封存流程没有降低。本地 AES-GCM 存储和 scrypt + AES-GCM 导出继续保留。转账、红包、赠卡气泡、固定卡片 URL、YIDT/卡片/游戏链接预览保留，并带入当前线上已有的斗地主房间和对局记录预览。

FCM 继续发送三个字段的高优先级纯 data 推送，不含消息正文或可识别信息。Worker 直接补拉普通消息；网络、身份、旧事件或超时失败保留占位。MessagingStyle、OnlyAlertOnce、Direct Boot、锁屏公共版本、token 刷新和当前会话抑制保留。真实手机的 FCM 投递仍需安装新版后验证。

## 验证

- 后端、真实隔离 Synapse 和浏览器回归 123 项通过：资金/红包/赠卡/钱包绑定/存证/历史/推送，以及服务器拒绝新加密事件、普通消息恢复和完整房间删除。
- 浏览器覆盖两账号收发、刷新、另一个设备无密钥读历史、附件原字节下载、卡片与游戏特殊预览，并确认没有密钥请求或密文发送。
- Web 单元测试 15 项、TypeScript 与 Vite 构建通过。Android Release JVM 14 项、严格依赖验证和 Release lint（0 errors）通过。
- 实际 Room 数据库的首次清理/保留钱包业务/二次不清理新消息测试，以及 NotificationManager 占位替换和 Firebase SDK 初始化测试共 3 项在 API28 模拟器通过，见 delivery/plain-chat-034-20261029 的结果。
- 线上清理后：Matrix、网关和归档旧聊天事件均为 0，活动会话均为 plain，加密状态事件为 0；checkpoint 健康，/health 返回 200。钱包哈希链和托管对账通过，历史钱包交易哈希保留。

## 真机检查

覆盖安装后打开 App，确认两个空私聊、钱包余额及历史交易。用两个测试账号在私聊/群聊收发文字、回复、图片和文件；刷新、切换设备、划掉最近任务再打开，均无需导入密钥。检查转账授权返回不自动扣款、显式收款、红包领取、赠卡气泡和站内特殊 URL 预览。检查内部备份、口令导出和 SHA-256 校验。

启用通知及系统权限后检查前台当前会话抑制、后台消息替换、连发合并、网络失败保留占位。Doze 可使用 `adb shell dumpsys battery unplug`、`adb shell dumpsys deviceidle force-idle`；结束后使用 `adb shell dumpsys deviceidle unforce`、`adb shell dumpsys battery reset`。重启后首次解锁前只显示通用占位，解锁后补拉更新。需要有 Google Play services 的真机，AOSP 模拟器不等于真实 FCM 投递验证。
