当前版本 **0.3.1 / code 9**；新增群解散/只读历史、8名管理员上限、个人归档/置顶、App通知、交易记录及账户最终注销联动。见 [PRODUCTION-0.3.1.md](PRODUCTION-0.3.1.md)。

# YiChat Android 0.3.0

App 使用独立 Android WebView 运行 **chat.yitiao.org 的同一套 Preact / TypeScript 前端**，不再维护 Compose 聊天界面。Matrix JS SDK + Rust WASM 负责原有 E2EE；Kotlin 只负责系统浏览器授权、离线框架缓存、内部加密原包备份、按天外部导出和系统文件选择。

当前包名 `org.yitiao.yichat.mobile`，versionCode **8**，通用 APK；复用 0.1.1～0.1.4 的私有签名，支持保留数据覆盖升级。不要卸载或清除存储。下载制品及本次验证以 [PRODUCTION-20261021.md](PRODUCTION-20261021.md) 为准；仓库中的早期候选版报告均为历史记录。

- 聊天留在 App；主站、登录、YIDT 和其他页面由系统浏览器打开。登录继续采用 Auth Tab / Custom Tabs + PKCE，回 App 后交换会话，钱包返回只查询，不自动入金。
- APK 内置完整网页框架与加密 WASM；在线刷新可信同源静态资源，离线仍可打开。已成功同步的最近消息和加密密钥保存在 App 自己的 IndexedDB；离线可读缓存，首次初始化、发送、业务操作和未缓存资源需要联网。
- 框架按钮、导航等文字不可长按选择；消息正文、恢复密钥和输入框仍可复制。
- App 设置有内部快照、按整数天数定期外部导出、立即导出及停用。原包备份与 Matrix 解密密钥备份分别管理。
- 旧原生数据库及私钥原地保留；新网页设备不会误用旧 Rust 设备身份。旧原生私钥不能自动转换为网页私钥，历史解密需要原 Matrix 密钥备份/恢复。

构建需要 Node 22+、JDK 21、Android SDK 36 / build-tools 36.0.0，以及源码外私有签名配置：

```bash
npm --prefix source/yichat/client ci
cd android
export JAVA_HOME=/path/to/jdk21
export ANDROID_HOME=/path/to/android-sdk
export YICHAT_SIGNING_CONFIG=/private/path/signing-private.json
./scripts/build.sh
```

输出 `android/app/build/outputs/apk/release/app-release.apk`。Gradle 自动构建并嵌入同一网页源码；依赖锁与严格 SHA256 校验随源码交付。签名密钥、生产配置、令牌和聊天数据不入库。

| 目录 | 用途 |
| --- | --- |
| `source/yichat/client` | 唯一聊天前端，App 与 Web 共用 |
| `android/app` | WebView 容器、受限消息桥、PKCE、备份和文件接口 |
| `android/legacy-native` | 停止编译的旧原生聊天源码和历史测试，仅供追溯 |
| `source/yichat/backend` | 现有网关源码参考，本次未重写聊天或资金内核 |
| `source/yichat/tests/browser/web-app.mjs` | 隔离真实 Synapse 的网页容器基础验证 |

[架构](ARCHITECTURE.md) · [本次说明、验证与回滚](WEB-APP-REFACTOR.md) · [下次接力提示词](NEXT-SESSION-PROMPT.md) · [Android 构建](android/README.md)
