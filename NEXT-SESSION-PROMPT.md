当前版本 0.3.0 / code8；先读 PRODUCTION-20261021.md、README.md、AGENTS.md（如存在）、source/yichat/docs/CHAT-KERNEL-API.md 和 delivery/production-20261021。

请接力维护 YiChat Android。先读 README.md、WEB-APP-REFACTOR.md、ARCHITECTURE.md、android/README.md 和 source/yichat/docs/CHAT-KERNEL-API.md（若文件路径不同先定位），然后查看 git 状态，保护所有未提交改动、原数据和密钥。

当前架构已改为唯一网页前端，不要恢复 Compose 或 Matrix Kotlin FFI：
- source/yichat/client：Preact + TypeScript + Vite；matrix-js-sdk + initRustCrypto/WASM，私聊标准 Matrix E2EE、群依服务端明文策略。
- android/app：Kotlin ComponentActivity + 独立 WebView；AndroidX WebKit、Browser Auth Tab/Custom Tabs、Room、WorkManager、OkHttp；minSdk26/target36、JDK21、Gradle8.13、AGP8.11.1、Kotlin2.1.21。
- 仅聊天主页在 WebView，主站/登录/YIDT/其他页面使用系统浏览器。PKCE 回跳已有服务端支持，钱包回跳不自动 fund。origin 和主框架限制的消息桥提供 session、备份、SAF 文件操作，不要扩大到任意原生能力。
- APK 内置完整网页框架与 WASM，并缓存可信同源公开资源；App 内 IndexedDB 持久同步与加密库，普通浏览器保持原行为。框架按钮导航禁长按选择，正文/输入/密钥可复制。
- 原包自动备份到内部 data；用户可设置 1～3650 整天的外部加密导出、立即导出、停用。原包与 Matrix 私钥备份分开。外部文件不得含令牌/证明/私钥。
- 旧原生代码在 android/legacy-native 不编译。旧数据/私钥原地保留，web. 会话命名空间与设备隔离，绝不删除或把旧 Rust 设备误当作 JS 设备。旧私钥没有自动转换器；用户需标准 Matrix 备份恢复。补回原包尚未注入网页时间线。

当前版本 0.2.0/code7，包名 org.yitiao.yichat.mobile。签名沿用前几次修复的私有证书（不在源码包），已有安装要覆盖升级，绝不能卸载清数据。首次新环境没有证书时可创建自己的随机签名，但不能覆盖现有安装，且须同步站点 assetlinks 公证书。所有密码、生产配置和令牌禁止输出或提交。

构建：npm --prefix source/yichat/client ci；cd android；配置 JAVA_HOME/ANDROID_HOME/YICHAT_SIGNING_CONFIG；./scripts/build.sh。产物 app/build/outputs/apk/release/app-release.apk，通用 ABI。

按用户新反馈修复，只做必要基础验证，不擅自全量回归。禁止真实资金、红包或卡片试跑，使用隔离 fixture。先简述实施步骤，实际修改并交付可安装 APK、源码和真实验证结果。报告清楚哪些是实际 Android 点击、哪些是 Playwright 桥接隔离测试；未完成项不能标通过。

0.3.0 后续补充：已移除聊天设备验证，主站 account_devices 管理 SID 并联动 Chat 锁定；群主头像上传/移除；App record-audit 支持内部原包与每日 ZIP 文件，最多连续5日，摘要仅上传元数据，私聊用标准 Matrix 恢复密钥本地解密。新摘要 record_day_digests 独立绑定原 events-manifest 根，清理前保留；不得修改既有归档字节。详细接口、文件上限与旧日不可用边界见 PRODUCTION-20261021.md。用户要求只做浏览器验证，Android 真机由用户验收。
