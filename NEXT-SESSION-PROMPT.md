# 当前交付：YiChat Android 0.3.3

推送重构已合入本版，包名 org.yitiao.yichat.mobile，versionCode 11。构建需要仓库外的 YICHAT_FIREBASE_CONFIG 与 YICHAT_SIGNING_CONFIG；不得上传服务账号 JSON、签名私钥或生产环境文件。使用原签名覆盖安装，保留 IndexedDB / Rust 加密存储。

当前设计与真机验证见 source/yichat/docs/PUSH-NOTIFICATIONS.md，发布范围见 PRODUCTION-0.3.3.md。FCM 真实投递尚需安装新版取得真实设备 token；不得将无 GMS 模拟器测试或 validate_only 的无效 token 响应描述为端到端投递成功。

以下保留旧版交接背景：

当前正式版本为 **0.3.2 / code 10**。先读 README.md、CHANGELOG.md、PRODUCTION-0.3.2.md、ARCHITECTURE.md 和 android/README.md；检查 Git 状态并保护未提交修改及既有数据。

- App 与 Web 共用 source/yichat/client；保持 Matrix JS SDK + Rust WASM，不引入第二套设备身份或同步循环。
- 后台消息使用用户启用的前台通知服务及 WorkManager 后备，仅读取通知元数据；禁止后台额外消费 Matrix to-device 队列。
- 已失败 Matrix 事件需要真实重试解密。有效消息备份恢复不得被交叉签名初始化失败阻断，也不得重置原有签名或密钥。
- 赠卡消息正文只保存固定 URL；预览验证会话权限，转手可看，销毁不显示卡面。不为新赠卡写完整渲染快照。
- 业务窗口重新打开即新操作，不恢复旧未完成操作；已完成资金/资产交易不应因 UI 关闭而回滚。
- 下载统一使用 GitHub Releases。不要恢复 YiChat `/app/download` 或在站点托管 APK。
- 已有安装使用原包名及私有证书覆盖升级，不卸载、不清理密钥或归档。私钥和生产配置不入库。
- 构建按 android/README.md；所有资产与消息验证只用隔离 fixture，报告区分浏览器、模拟器与真机。
