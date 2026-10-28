# 推送通知验证记录 — 2026-10-27

基线：GitHub main `5d4855da7f2b5e0262e30eb33dfc8584b2f085aa`（Android 0.3.2）。本次实现位于独立工作树 `/home/claude/yichat-push-refactor`、分支 `refactor/push-notifications`。不混入原工作目录的其他改动。

| 检查 | 结果 |
|---|---|
| Web 单元测试 | 15 项通过，含缺失密钥／超时不返回替换内容 |
| TypeScript / Vite 生产构建 | 通过，含独立后台 notification.html |
| 后端 push + native auth + gateway | 55 项通过 |
| Android JVM 单元测试 | 14 项通过，其中新增通知规则 7 项 |
| Android Debug / AndroidTest APK | 构建通过 |
| Gradle 离线、锁定依赖、严格 SHA256 校验 | 通过，未使用跳过校验选项 |
| Android Release lint | 0 errors，56 warnings、1 hint（包含既有警告及新依赖资源警告） |
| API 28 NotificationManager instrumentation | 1 项通过：占位、失败保留、公共版本、MessagingStyle、稳定标识、静默替换及已清除通知不复活 |
| 真实隔离 Matrix E2EE | 初始端到端加密交换、关闭 UI 后新 Megolm 会话由后台解密、UI 再打开保留密钥及同步缓存，均通过 |

证据：[Android 系统通知结果](../../../delivery/push-refactor-20261027/android-notification-result.txt)、[真实 Matrix 结果](../../../delivery/push-refactor-20261027/matrix-notification-result.txt)、[结构化结果](../../../delivery/push-refactor-20261027/results.json)。测试只使用临时账号、临时资产、专用测试 PostgreSQL、隔离 Synapse 和模拟器。

检查中发现并修正的相关问题：

- Firebase 传递依赖引入旧版 Fragment，与现有 Activity Result API 冲突。显式使用 Fragment 1.8.9，通过 lint；新增依赖写入 lockfile 及 SHA256 verification metadata。
- Kotlin stdlib-common 在 JVM runtime 是重定向到 stdlib 的组件；旧 lockfile 错把四个 runtime 配置列为直接解析 common。仅修正这四个配置归属，保留 compile、插件和具体 JVM stdlib 的锁定，并以不带 `--write-locks` 的严格构建复验通过。
- 既有两个网关测试在未修改的 0.3.2 基线上同样失败：其模拟 Matrix 适配器只写了历史成员证据，缺少已确认当前成员表。本次补齐 fixture 的成员证据后，55 项检查全部通过；没有放宽产品权限校验。
- 系统发布通知是异步的，instrumentation 使用有界等待检查已显示通知，避免把 OS 队列延迟误判为通知丢失。
- 真实 WASM 浏览器测试使用专用构建与 preview，固定代理到隔离网关 18018，避免开发预打包改变 WASM 地址；测试 hook 不进入生产构建／APK。

尚未验证：真实 Firebase 项目下的 FCM 投递、高优先级降级、真实 WorkManager quota 耗尽、Android 13+ 实机运行时权限、厂商省电策略、真实手机重启未首次解锁。这些需要实际 Firebase Android 配置、服务端发送凭据和有 Google Play services 的设备。Manifest、Direct Boot 分流、权限分支和服务端协议已有实现，不能据此声称已通过真机验证。

本次没有将新版本部署到线上，也没有更新 GitHub Release。现有 0.3.2 及 GitHub 下载入口继续工作。启用 FCM 前须完成 [配置和真机步骤](PUSH-NOTIFICATIONS.md)，并使用原签名和递增 versionCode 构建。

## 2026-10-28：0.3.3 配置接入

已提供并匹配 Firebase Android 配置和服务账号，Google 服务账号 OAuth 认证成功。HTTP v1 的 validate_only 校验请求返回合成 token 的 INVALID_ARGUMENT；没有实际发送通知。本版 Release JVM 14 项和 API28 instrumentation 2 项通过，其中包含真实 FirebaseInitProvider 启动配置检查。服务端已更新，新增设备注册和未读事件查询接口，下载仍只通过 GitHub。真实手机 FCM 投递仍需安装新 APK 后验证。发布构建与原签名信息见 [0.3.3 发布说明](../../../PRODUCTION-0.3.3.md)。
