# 单一网页前端架构（0.2.0）

```text
Android MainActivity / 独立 WebView
  ├─ https://chat.yitiao.org/ → 同一 Preact/TypeScript 前端
  │    ├─ matrix-js-sdk + initRustCrypto / WASM / IndexedDB
  │    ├─ 单写 Web Lock；账号+MXID+设备隔离；原有请求调度器
  │    └─ 业务 API、站内分享、聊天附件、归档
  ├─ 静态资源请求 → 同源在线更新 / 内部公开资源缓存 / APK 完整框架
  ├─ YiChatApp 消息桥 → 仅允许准确聊天源的主框架
  │    ├─ 会话、系统授权、锁定与撤销
  │    ├─ 自动内部备份、按天外部导出 / SAF
  │    └─ 分块 Blob 下载到系统文档选择器
  └─ 非聊天页面 → 系统 Auth Tab / Custom Tabs / 浏览器

Kotlin WorkManager + HistoryRepository
  ├─ 原网关认证摘要、服务器原包、服务器已清理数据的在线成员补包
  ├─ Room 保存哈希核验后、Keystore 保护的原消息包
  └─ 内部 AES-GCM 快照；外部 YCB1（scrypt + AES-GCM）
```

不再链接 Matrix Kotlin/Rust FFI、Compose 聊天、第二套资金 UI 或 YCard 聊天渲染器。旧源码放在 `android/legacy-native`，不进入 APK。新设备证明与会话使用 `web.` 前缀；旧 `session` 和 `accounts/` 密钥库保留，匹配同一 uid/MXID 后只复制并重新包装可信消息原包及导出设置。迁移不覆盖密钥、不上传私钥；失败保留原始数据。

WebView 关闭 file 访问、混合内容及第三方 Cookie；content 只用于用户主动选择的系统文件，上层禁止 file/content 导航，准确限制桥的来源与主框架。release 关闭调试，TLS 错误拒绝连接，不允许任意 intent、反射或原生文件路径。系统浏览器持有登录 Cookie，App 通过现有一次性 PKCE 交换取得网关令牌；仅桥接当前网页设备所需 session，不传设备证明。

静态缓存只保存公开框架资源，绝不缓存认证 API、私聊媒体或头像。会话同步快照在独立 IndexedDB，应用展示资料通过现有本地加密 Vault 保存。离线重新打开可恢复已缓存的最近时间线和持久化 Rust 加密库；未缓存历史、头像、附件和订单仍需网络。第一次登录初始化必须联网。

自动备份保存原始消息包，私聊为原生 Matrix 密文；它不等于 Matrix 私钥备份。外部文件不包含会话令牌、设备证明、私钥。导出间隔为 1～3650 个整天，由 WorkManager 在系统条件允许时执行。卸载/清除数据会破坏内部存储，因此用户仍需独立保存恢复密钥及外部导出。

旧原生密钥仍留在原路径，但没有跨 SDK 转换器；新网页设备依赖标准 Matrix 密钥备份或其他设备分享历史会话密钥。经过服务端清理、通过成员补回的原包会安全保存在备份库，尚未注入网页 SDK 时间线；不伪造正文或重新发送历史。
