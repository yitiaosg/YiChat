# 当前状态：0.3.6 上传恢复

0.3.6 / code 14：App 与 Web 使用 128 KiB 分块、服务端已落盘进度确认、丢失回执核对及续传；完成阶段先写 SHA-256 与归档回执再公开附件。旧整包端点保留兼容。草稿 15 分钟无进展后取消，finalizing 状态不得静默删除。详见 PRODUCTION-0.3.6.md 和 source/yichat/docs/UPLOADS.md。

保持 plain-20261029-v1，不清理聊天、不重复退款、不修改资产。不要把原手机网络断连描述为已确定根因；本版通过故障注入验证恢复机制。带宽、额度、请求频率与权限保持原限制。下方为历史背景。

---

# 当前状态：0.3.5 分享预览与 FCM 正文

0.3.5 / code 13：站内分享不重复显示原 URL，保留说明文字、预览与原消息哈希。新 App 注册 preview_version=1，FCM data 增加最长 1200 UTF-8 字节的 message_preview 及 recipient_tag；旧 App 仍收三字段路由。正文来自认证未读原包，核对哈希并重新检查权限；账号切换后拒绝旧正文，首次解锁前显示通用占位。消息类型文案由 server / client 共用测试用例验证。见 PRODUCTION-0.3.5.md。

本版保持 plain-20261029-v1，不重复清理新消息，也不修改钱包、托管款或卡片资产。不要再次执行历史退款或聊天重置。下方为历史背景。

---

# 当前状态：0.3.4 普通消息与一次性聊天清理

当前版本 Android 0.3.4 / code 12，沿用 org.yitiao.yichat.mobile 与原签名。私聊、群聊和附件不再使用 E2EE，服务端拒绝新的加密消息和状态。站点所有者明确要求清空旧聊天，已通过官方 Synapse API 重建空私聊；钱包账本、交易哈希链、资产订单和赠卡凭证独立保留。指定未领取托管款已按正常退款出账流程退回，勿重复操作或改余额。

消息 epoch plain-20261029-v1 只代表这一次授权清理，后续常规部署不要更改。App 首次升级删除旧聊天包/快照/水位，保留钱包操作；不能每次启动清理新消息。保留 SHA-256、自动备份、口令导出、红包/转账/赠卡与站内特殊预览。网页下载指向 GitHub 0.3.4，服务端 APK 下载关闭。见 PRODUCTION-0.3.4.md 与最新 README/CHANGELOG。下方早期 E2EE 信息仅供历史追溯，不能作为当前设计要求。

---

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
