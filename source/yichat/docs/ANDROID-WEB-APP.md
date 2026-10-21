# Android 独立网页容器（0.2.0）

同一套 client 前端由浏览器与独立 Android WebView 使用。index.html 的 yichat-app-bridge 标记表示包含 App 适配，普通浏览器无 YiChatApp 消息端口时保持原行为。

App 注入准确源主框架消息桥，提供现有 PKCE 原生设备 session、锁定/退出、原包备份、SAF 按天导出和分块下载。钱包/订单接口已按 native_devices 提供原有 Android 回跳，无新后端资金操作。不能将设备证明、私钥或完整聊天正文传给浏览器授权页面。

App 模式启用 IndexedDBStore 并先 startup，再初始化持久 Rust 加密库；普通 Web 保持 MemoryStore。在线失败时只使用已授权本机账号、缓存信息和原有密钥，401 仍锁定。离线可读已同步最近时间线，首次初始化和未缓存附件需联网。公开框架资源缓存不包含认证 API。

App 自动原包备份/导出由 Kotlin 现有 HistoryRepository/WorkManager 执行；与 Matrix 密钥备份分开。网页仅控制受限桥，不读取任意本地文件。旧原生私钥原地保留，不能重用旧 Rust 设备到 JS SDK。

部署只需构建并原子替换静态 release，不变更数据库、Synapse、资金或归档规则。旧 release 保留，回滚 current 链接即可。详细 Android 源码、测试与 APK 由 YiChat 独立仓库交付。
