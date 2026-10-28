# YiChat Android 0.3.3 — 2026-10-28

包名 `org.yitiao.yichat.mobile`，versionName `0.3.3`，versionCode `11`。使用原专用签名；从 0.3.1 / 0.3.2 覆盖升级，无需卸载或清除数据。

[最新发布](https://github.com/yitiaosg/YiChat/releases/latest) · [0.3.3 APK](https://github.com/yitiaosg/YiChat/releases/download/android-20261028-v0.3.3/YiChat-0.3.3-11.apk) · [更新日志](CHANGELOG.md)

## 配置与行为

- 已接入同一 Firebase 项目的 Android 配置及服务账号，配置文件保存在源码仓库外。服务账号私钥不进入源码、APK、日志或 GitHub。
- 服务端发送 HTTP v1 高优先级纯 data 消息：只带会话 ID、private/group 类型和消息 ID。Google OAuth 服务账号认证成功；FCM validate_only 请求仅因合成设备 token 无效而返回 INVALID_ARGUMENT，没有真实投递。
- 原生入口立即显示本地会话名及通用占位，然后由 expedited WorkManager 复用原有 Matrix SDK / Rust 模块、同一设备密钥库补拉并解密。成功以相同通知标识、MessagingStyle、OnlyAlertOnce 替换；失败保留占位。
- 支持 Direct Boot 分流、显示名 DP 缓存、解锁补处理、锁屏 publicVersion、通知权限检查、token 刷新、当前会话抑制与按会话合并。
- FCM 注册成功后停止常驻轮询连接；没有 Google Play services 或暂时无法注册时继续使用兼容路径。原来的主站 OAuth 登录配置不变。
- App 与 Web 共用前端；下载统一指向 GitHub Releases。`/app/download` 及 YiChat 本地 APK 下载路径继续返回 404。

## 验证与边界

后端 push / native auth / gateway 回归 55 项、Web 单元测试 15 项通过。原重构已有 Android 单元测试、真实 NotificationManager、隔离 Matrix 新 Megolm 会话离线解密及重开 UI 保留密钥的验证记录，见 [推送验证记录](source/yichat/docs/PUSH-VALIDATION.md)。本版使用实际 Android 配置与原签名构建，Release JVM 14 项通过；API28 模拟器的 FirebaseInitProvider 初始化与系统通知两项 instrumentation 通过，Release lint 为 0 errors。最终版本号为 0.3.3 / code 11，签名证书与 0.3.2 一致。证据见 [本版验证结果](delivery/fcm-033-20261028/results.json)。

真实 FCM 端到端投递需要新 APK 安装后取得真实设备 token。目前不将 API 校验、无 Google Play services 的模拟器或配置初始化检查描述为真实手机投递验证。首次安装本版后请打开 App，登录、启用通知并允许系统权限；随后验证划掉最近任务、锁屏、Doze 和重启未首次解锁。具体步骤见 [推送通知说明](source/yichat/docs/PUSH-NOTIFICATIONS.md)。

服务端更新前后 Matrix versions 就绪接口均为 200，存证 checkpoint 健康且无 gap。`/health` 在更新前已有 503，原因是 1 条历史失败归档任务；本次不修改历史归档或用户资产。

## 发布文件

- `YiChat-0.3.3-11.apk`：原签名的通用安装包。
- `YiChat-0.3.3-source.tar.gz`：与 main 对应的源码，含测试及说明，不含私有配置。
- `SHA256SUMS`：上述 APK 和源码包的 SHA256 校验值。仓库根目录同名文件用于源码内文件校验，含义不同。
