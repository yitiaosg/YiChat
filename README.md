# YiChat

当前版本 **Android 0.3.3 / versionCode 11**，包名 `org.yitiao.yichat.mobile`。

[下载最新版本](https://github.com/yitiaosg/YiChat/releases/latest) · [下载 0.3.3 APK](https://github.com/yitiaosg/YiChat/releases/download/android-20261028-v0.3.3/YiChat-0.3.3-11.apk) · [更新日志](CHANGELOG.md)

YiChat 的 Android App 和 [网页版](https://chat.yitiao.org) 共用 Preact / TypeScript 界面，由 Matrix JS SDK 与 Rust WASM 处理端到端加密私聊。Kotlin 容器负责系统浏览器授权、后台通知、离线框架缓存、原消息包备份和系统文件操作；不维护第二套聊天或加密协议。

## 0.3.3 更新

- 接入 FCM HTTP v1 高优先级纯 data 推送。推送只携带会话 ID、类型及消息 ID，不包含正文、昵称、群名或密钥。
- 原生先显示占位通知，再通过 expedited WorkManager、原有 Matrix JS / Rust 模块和同一设备密钥库补拉并解密。成功后以 MessagingStyle 合并替换；失败保留占位，替换不重复响铃。
- 支持 Direct Boot 显示名缓存、首次解锁后补处理、锁屏公共版本、当前会话抑制及 token 刷新注册。已读或清除通知后，在途任务不能重新弹出它。
- 已使用外置 Firebase 配置及原签名构建。系统推送注册成功后停止常驻连接；未能注册的设备继续使用轮询兼容路径。

设计和真机步骤见 [推送通知说明](source/yichat/docs/PUSH-NOTIFICATIONS.md)，本次发布范围见 [PRODUCTION-0.3.3.md](PRODUCTION-0.3.3.md)。Google 认证、API 校验及本地测试不能替代有 Google Play services 的手机上的实际投递验证。

## 0.3.2 更新

- 开启通知后，后台连接在聊天界面关闭、移除最近任务后继续检查新消息，并显示常驻连接通知。通知不含聊天正文或密钥；保留系统调度作为后备，不额外启动 Matrix `/sync`。应用设置提供后台运行设置入口，系统省电限制仍可能影响连接。
- 修复导入正确恢复密钥后仍无法解密的问题：消息密钥恢复不再被交叉签名初始化失败阻断，已失败的 Matrix 消息会走真实重试解密流程。
- 赠卡成功后自动发送聊天气泡。消息正文只保存不含用户名的固定赠卡 URL，按需显示当前卡片；转手不使 URL 失效，销毁后不再显示卡面。预览仍检查会话权限，不把卡片全部数据放进消息。
- 返回 YIDT 绑定页面后自动刷新钱包状态；区分付款方与收款方未绑定，避免让已绑定用户重复绑定。
- 关闭后重新打开转账、红包或赠卡窗口会开始新操作，不再被之前失败的操作锁定。
- 下载统一使用 **GitHub Releases**；YiChat 服务端不再提供 `/app/download` 安装包下载接口。

完整变更、验证和部署边界见 [PRODUCTION-0.3.2.md](PRODUCTION-0.3.2.md)。较早版本说明保留为历史记录。

## 安装与使用

使用原签名从 0.3.1 或 0.3.2 覆盖升级，**不要卸载或清除应用数据**，以保留设备密钥和本地记录。新装或升级后，在 App 的“消息通知”中确认通知已开启并授予系统权限。推送注册成功后不再需要常驻连接通知；兼容后台连接启用时仍会显示常驻通知。

此前仍无法解密的消息，升级后可重新输入原 Matrix 恢复密钥执行恢复。原消息包备份与加密密钥备份分别管理；原包导出不包含私钥，不能替代密钥备份。

APK 包含完整网页框架与加密 WASM；联网时更新可信同源静态资源，离线时可读取已同步的最近消息。首次初始化、发送、业务操作和未缓存资源需要联网。主站登录及 YIDT 授权使用系统浏览器，返回 App 本身不会自动付款。

## 从源码构建

需要 Node 22+、JDK 21、Android SDK 36 / build-tools 36.0.0，以及源码外的私有签名配置：

```bash
npm --prefix source/yichat/client ci
cd android
export JAVA_HOME=/path/to/jdk21
export ANDROID_HOME=/path/to/android-sdk
export YICHAT_SIGNING_CONFIG=/private/path/signing-private.json
export YICHAT_FIREBASE_CONFIG=/private/path/google-services.json
./scripts/build.sh
```

产物为 `android/app/build/outputs/apk/release/app-release.apk`。Gradle 自动构建并嵌入同一份网页代码。已有安装必须沿用原签名；服务账号私钥、签名密码、服务端环境配置、访问令牌及用户数据不进入源码或发布附件。APK 仅包含 Firebase 客户端初始化所需的配置值。

| 目录 | 用途 |
| --- | --- |
| `source/yichat/client` | App 与 Web 共用的聊天前端 |
| `android/app` | WebView、受限消息桥、后台通知、PKCE 与备份 |
| `source/yichat/backend` | 网关、Matrix 策略与资产接口源码参考 |
| `source/yichat/tests` | 使用隔离账本及专用测试数据库的回归测试 |
| `delivery/offline-fix-20261023` | 本版离线解密与 Android 后台通知验证结果 |
| `android/legacy-native` | 不参与编译的旧原生实现，仅供追溯 |

[Android 构建说明](android/README.md) · [架构](ARCHITECTURE.md) · [接口文档](source/yichat/docs/CHAT-KERNEL-API.md)
