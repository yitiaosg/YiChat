# YiChat 0.1.1：浏览器授权接力修复

**后续修复：0.1.2 / code 4，见 [CALLBACK-FIX.md](CALLBACK-FIX.md)。实体机曾把 HTTPS 回跳留在浏览器；现改用 Auth Tab 回调，并部署了固定包名的返回 App 按钮。本文件保留为上一版验证记录，不代表 0.1.2 已通过实体机登录。**

2026-10-20。基于用户指定的 `android-20261020-candidate.2`（`325fb6c`），当前本地开发分支 `fix/android-browser-login`。

## 已实现

- 登录和 YIDT 链接由 AndroidX Browser 1.10.0 Custom Tabs 打开，浏览器拥有登录界面、cookie、两步验证及钱包授权。没有内嵌登录 WebView，不再用无 Custom Tabs 的 ACTION_VIEW 打开独立浏览器任务。默认浏览器不支持时，枚举已安装的 Custom Tabs provider，选择兼容浏览器；缺少兼容浏览器时显示可处理错误。
- 精确 HTTPS App Links 返回 `/app/callback` 或 `/app/return`。一次性回调消费后立即清空 Intent URI；旋转/Activity 重建仅保存摘要，避免重复交换旧 code。网关按 state/nonce/PKCE 和主站 UID/SID 验证，code 60秒有效、只用一次。
- 线上此前缺少原生接口及 assetlinks。已从既有候选源码选择性集成原生接入，部署提交 `fee256c`，沿用原 Matrix、资金、权限、归档规则，没有重写内核。主站网页及既有 Web 静态发布保持原版本。
- 原生钱包绑定有 `?client=android`，保留现有登录/CSRF/PIN 校验，成功回 App 并重新查询权威绑定。原生付款授权回原订单，仍需明确 fund；回跳不自动入金。网页原返回行为保留。
- 将 SDK/加密存储初始化移至 IO 线程，显示实际初始化阶段；设备恢复完成前禁止启动同步，同步启停加锁防止并发启动重复任务。
- 修复原生新设备被显示为“浏览器”的设备名称；登录页支持小屏滚动，避免取消入口被挤出可视区域。

## 安装和签名

发布 APK：`YiChat-0.1.1-3-arm64-v8a.apk`；另提供 x86_64 和 universal。minSdk26，target/compile36。新随机专用签名，v2/v3 通过；arm64 包16KB页对齐通过。

包名 `org.yitiao.yichat.mobile`，桌面名称“YiChat 修复版”，与旧候选版并存。新签名不能覆盖旧候选版；**不需要卸载旧 App**，旧包的数据与密钥保留。新包有独立设备密钥，不能直接读取旧包 Keystore；旧会话的解密须原有设备验证/Matrix密钥恢复。

公证书 SHA-256：`9E:75:05:F8:4F:84:C2:8A:C9:20:A5:DA:34:70:A4:52:E7:92:3B:76:C5:41:67:CE:1C:94:2A:FC:56:C7:AF:3E`。后续升级须复用本次签名并提高 versionCode。私有签名仅保存在源码外的0700目录、0600文件中，没有进入 Git、源码包、日志或 APK。

## 已完成验证

- JDK21 / Gradle8.13 / Android SDK36 严格依赖校验构建 debug、release 及 instrumentation APK；固定依赖锁和 SHA-256 verification metadata。Android JVM **7项通过**，含白名单 URL、加密封装与读写重试边界。
- Python/真实隔离 Synapse、专用 PostgreSQL、SQLite 账本、主站 OIDC 和网页浏览器：**140项通过，4项第三方弃用告警，263.17秒**。其中 Chromium 真实网页 **33项 CHECK 通过**；资金/卡片仅隔离测试数据。新增钱包返回/CSRF4项通过。
- 生产备份 `/root/yitiao/backups/yichat-v2-20261020T085701Z`。线上六项服务正常，原 Web 首页/静态资源/缓存一致；11个私密配置/密钥文件摘要与备份一致。账本链和全部已入金订单核对通过，没有使用用户资金试跑。
- 公开 `https://chat.yitiao.org/.well-known/assetlinks.json` 为200、JSON、无重定向、缓存1小时，匹配实际签名。Google Digital Asset Links 远程检查识别旧包及新 mobile 包，无诊断错误。
- 线上 `/auth/native/login` 缺参数400，合法参数307到现有主站 `/oidc/authorize`；`/app/callback`、`/app/return` 可用。不使用真实用户身份完成生产授权。
- release DEX 检查不含隔离网关、fixture账号接口/密码或签名密码。测试-only loopback 回跳不在 release manifest。

## Android 浏览器验证

API28 AOSP x86_64 软件模拟器（无 KVM），实际 Chrome 69 Custom Tabs，使用原 OIDC provider 的隔离登录表单、真实网关和 Synapse。**完整浏览器登录测试通过：1 test，302.614秒**。实际点击原生登录、填写浏览器表单、点击提交、自动回到 App，初始化原生加密会话，确认真实的“Android isolated group”列表，旋转后仍登录且不重复交换 code。该轮没有手工注入 code/token；没有手工帮助点击登录。 随后独立 instrumentation 进程重开相同设备/加密库，**密钥保持测试通过：1 test，18.207秒**，比较 SDK 原生 Curve25519 公钥一致。

测试使用同源 debug 隔离构建，已逐字节比较模拟器安装 APK 与最终 debug 产物，避免把旧安装误当新版本。loopback HTTP 回跳在 debug manifest 中，测试中手动设为该 App 的受支持链接默认处理器；生产 HTTPS App Links 则单独以实际 release 证书、公开响应和 Google 远程校验确认。没有用真实用户密码完成生产登录，也没有实体机结果。

新截图：[浏览器授权窗口](delivery/screenshots/browser-fix-20261020/browser-auth-window.png)、[登录后原生会话列表](delivery/screenshots/browser-fix-20261020/native-login-success.png)。历史 `VERIFICATION-ANDROID.md` 中的候选版截图不作为本次新验证证据。

保留早期失败记录：默认浏览器不支持、初始化超时、模拟器 ADB 断连/旧安装，以及表单自动化兼容问题。调试中的一次人工辅助点击不计全自动通过；最终通过来自清理隔离 App 数据/浏览器 cookie 后的独立运行。初轮长驻 fixture 达到1小时等待上限而失败，不计功能测试通过；后续使用新的隔离 fixture。

## 明确边界

本次针对登录、浏览器授权及返回链路。没有把原候选版全部 App 功能宣称为验收完成。原包恢复后的 timeline 展示、跨设备 SAS/密钥恢复、完整 SAF、原生资金/赠卡/媒体操作矩阵、头像及推送等剩余项见历史报告，仍需后续开发/验收。软件模拟器也不能替代实体 Android 设备、厂商浏览器及慢网的覆盖。

## 复现、部署和回滚

- 具体服务端变更、迁移/部署/回滚：[ANDROID-BROWSER-LOGIN.md](source/yichat/docs/ANDROID-BROWSER-LOGIN.md)。当前接口第17节：[CHAT-KERNEL-API.md](source/yichat/docs/CHAT-KERNEL-API.md)。新源码包含 Android 与相关现有站点/内核代码，未混入运行数据库/生产配置。
- Android 架构/技术栈、严格构建与隔离测试：[android/README.md](android/README.md)。新增真正浏览器 UI 测试 `BrowserLoginTest`，专用原 OIDC/Synapse fixture，禁止部署测试入口；Web/PG与Android长驻fixture不能同时运行。
- 新证据保存在 `delivery/evidence/browser-fix-20261020`；旧候选版记录保留供追溯。签名密码不写命令行，不将私钥加入公开交付。
