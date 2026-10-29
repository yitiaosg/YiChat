# YiChat

当前版本 **Android 0.3.4 / versionCode 12**，包名 `org.yitiao.yichat.mobile`。

[最新发布](https://github.com/yitiaosg/YiChat/releases/latest) · [下载 0.3.4 APK](https://github.com/yitiaosg/YiChat/releases/download/android-20261029-v0.3.4/YiChat-0.3.4-12.apk) · [更新日志](CHANGELOG.md) · [网页版](https://chat.yitiao.org)

Android App 与网页版共用 Preact / TypeScript 界面和 Matrix JS SDK。**私聊和群聊均停用端到端加密**，通过 HTTPS 传输，服务器可以读取消息；不再需要会话密钥、密钥导入或 Rust 加密初始化。Kotlin 容器提供系统浏览器授权、通知、离线缓存、原消息包备份和文件操作。

## 0.3.4 更新

- 私聊与群聊统一使用普通 Matrix 消息；服务端拒绝新的加密消息及加密状态事件。新设备、刷新和后台通知不依赖会话密钥。
- 按站点所有者要求执行一次旧聊天清理，重建空私聊并保留联系人关系；钱包账本、资产流水、转账订单与赠卡凭证独立保留。历史资金凭证仍按不可变 UID 校验访问权限。
- App 升级首次打开时清理旧聊天包、旧快照与聊天水位；网页旧同步缓存及旧消息草稿隔离清理。该清理只执行一次，不会在每次登录时删除新消息，也不清空钱包业务记录。
- SHA-256 原包、附件和每日归档校验、自动备份、本地存储加密及口令加密导出继续保留。
- 转账、红包、赠卡气泡、卡片固定 URL 和站内特殊预览继续保留，包括现有斗地主邀请及对局记录预览。没有更改钱包绑定与资金幂等结算规则。
- FCM 继续只携带会话 ID、类型和消息 ID；先显示通用占位，再补拉消息替换。失败保留占位、锁屏隐私、Direct Boot、会话合并及 OnlyAlertOnce 行为保留。

本次变更和验证见 [PRODUCTION-0.3.4.md](PRODUCTION-0.3.4.md)。此前版本中的 E2EE、密钥恢复说明仅为历史记录。

## 安装与使用

使用原签名覆盖升级，无需卸载或清除应用数据。**本次旧聊天清理为站点所有者明确要求的一次性重置，旧聊天不会通过恢复密钥或旧备份重新导入。** 已自行下载到设备或其他位置的旧导出文件需要自行处理；服务器无法远程删除这些副本。

首次打开后确认“消息通知”已开启并授予系统权限。FCM 注册成功后停止常驻连接；无法注册的设备保留兼容后台连接。真实 FCM 投递仍应在有 Google Play services 的手机上验证。

App 自动备份原消息包到内部存储；导出文件继续使用独立口令加密。联网时更新可信同源网页资源，离线时读取已缓存消息；首次登录、发送和资产操作需要联网。主站登录及 YIDT 授权通过系统浏览器完成，返回 App 本身不会自动付款。安装包统一使用 GitHub Releases，YiChat 自托管 APK 下载接口保持关闭。

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
| `delivery/offline-fix-20261023` | 0.3.2 历史验证结果 |
| `android/legacy-native` | 不参与编译的旧原生实现，仅供追溯 |

[Android 构建说明](android/README.md) · [架构](ARCHITECTURE.md) · [接口文档](source/yichat/docs/CHAT-KERNEL-API.md)
