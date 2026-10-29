# 单一网页前端架构（0.3.6）

Android MainActivity 通过受限 WebView 加载与 Web 共用的 Preact / TypeScript 前端。Matrix JS SDK 负责普通消息同步；私聊、群聊及附件均停用端到端加密，通过 HTTPS 传输，服务器可以读取消息。不初始化 Rust Crypto，也不要求密钥备份或设备密钥查询。

- 附件通过 `/api/uploads` 的 128 KiB 分块协议上传；SQLite journal 记录已 fsync 的 offset，网络故障先查进度再续传。最终 SHA-256 与 PostgreSQL 回执成功后才写入原 files/file_hashes。详见 [上传协议](source/yichat/docs/UPLOADS.md)。
- 单写 Web Lock 管理 UI 与后台通知 WebView 的同步实例；账号、MXID、设备和消息 epoch 隔离 IndexedDB 同步缓存及本地消息草稿。
- 业务 API、站内分享和资产授权沿用原架构。转账及红包使用权威 SQLite 账本与原幂等出账流程，赠卡保存固定 URL，预览按需读取当前卡片。
- 原生 FCM 回调立即显示占位，WorkManager 用同源 SDK 补拉普通消息并合并替换；新版注册 preview_version=1 后，推送 data 带最长 1200 UTF-8 字节的文字/类型摘要及账号设备指纹；旧版仍只有路由。首次解锁前隐藏正文，补拉失败保留已显示内容，替换使用 OnlyAlertOnce。
- Kotlin HistoryRepository 根据认证摘要拉取服务器原包；保留接收时间、事件身份和 SHA-256 校验，也保留证据支持的成员补包逻辑。
- Room 原包存储仍由 Keystore 保护，内部快照使用 AES-GCM，外部 YCB1 使用 scrypt + AES-GCM。停用 E2EE 不影响本地存储与导出口令加密。

0.3.4 的一次性明确重置使用官方 Synapse 房间删除 API，清理旧聊天与聊天归档，保留钱包账本、转账订单、赠卡资产凭证和联系人关系。旧房间只保留资金凭证需要的最小元数据并标记 retired；禁止新消息，资金凭证按不可变 UID 只读访问。自动封存、摘要不可变触发器及数据库角色权限保留。App 首次升级清理旧聊天包和旧内部快照，保留钱包业务操作；epoch 标记防止再次清理新消息。旧导出不能替代当前认证摘要，服务器无法远程删除用户自行下载的副本。

WebView 禁止 file 访问、混合内容及第三方 Cookie；桥仅允许准确聊天源的主框架。外链交给系统浏览器，主站授权使用一次性 PKCE 交换，付款仍需在 YIDT 明确确认。release 关闭调试，拒绝 TLS 错误及任意 intent、反射、原生文件路径。

静态缓存只保存公开框架资源，认证 API、私聊媒体和头像不进入公开静态缓存。离线读取已同步的普通消息，未缓存历史、附件、头像和资产操作仍需网络。初次登录需要联网。旧原生实现保留在 android/legacy-native，仅供历史追溯，不参与 APK 编译。
