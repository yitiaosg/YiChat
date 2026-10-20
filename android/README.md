# YiChat Android（2026-10-20 集成候选版）

这是可审查源码与签名候选 APK；目前**没有完成用户列出的全部正式发布验收**。请先阅读交付目录的 VERIFICATION-ANDROID.md 中的阻断项。没有部署生产原生登录、没有生产凭据、没有用真实资金/卡片试跑。

## 构建

JDK 21、Android SDK platform 36 / build-tools 36.0.0、网络及可信系统 CA。Gradle Wrapper 8.13、AGP 8.11.1、Kotlin 2.1.21；minSdk 26、target/compileSdk 36。官方完整 Matrix Rust Kotlin UniFFI AAR 固定 `org.matrix.rustcomponents:sdk-android:26.10.15`，支持 arm64-v8a/x86_64。使用 classic syncV2，不扩开 Sliding Sync 命名空间。

```bash
export JAVA_HOME=/path/to/jdk21
export ANDROID_HOME=/path/to/android-sdk
export YICHAT_SIGNING_CONFIG=/private/path/signing-private.json
./scripts/build.sh -PyichatVersionCode=2
```

签名文件单独私有交付。第一次生成一次，以后必须复用；修改 JSON 中 `storeFile` 为实际绝对路径。不要把密码作为命令行参数，不要提交该目录。Wrapper 分发 SHA256、版本目录、Gradle locks、dependency verification 元数据随源码交付，严格验证固定依赖。Gradle 首次下载较大完整 AAR，无需本机编译 Rust/NDK。

release 固定连接 `https://chat.yitiao.org`，拒绝明文流量。可安装不表示生产原生登录已部署；见 [部署与回滚](docs/DEPLOYMENT.md)。本工程不包含生产密钥/令牌/聊天历史。

## 结构

- Compose + Navigation + ViewModel/StateFlow；Gateway、Auth、Matrix、Business、Media、History、Backup Repository。
- Rust SDK 保存自己的同步/加密/发送队列；App Room 保存业务操作、外层元数据、认证原包/摘要、水位、tombstone。
- 账号/设备独立路径与单写锁，Keystore 包装独立数据密钥。退出锁定数据并撤销网关会话，不删除设备或历史密钥。
- 系统浏览器完成登录/2FA/钱包授权；新增网关原生 PKCE 交接接口，不冒称 OIDC 原生接口。回跳只查询订单，不自动 fund。
- 原 YCard 可信只读渲染器在隔离 AssetLoader WebView；聊天、登录、钱包均不在 WebView。

## 隔离联调

专用测试 PG/SQLite/真实 Synapse；初始化方法沿用 `../source/yichat/README.md`、`../handoff/README.md`。禁止生产 DSN，PG 测试与 Android 长驻 fixture 不并行运行。`android_fixture.py` 是显式选用的 loopback fixture，不会由普通 pytest 自动发现，不能部署该测试端点。

```bash
# 在 source/，专用测试库环境变量准备完毕后
python -m pytest -q -s yichat/tests/android_fixture.py
# 在 android/，另一个终端
./gradlew --dependency-verification strict -PyichatIsolation=true :app:assembleDebug :app:assembleDebugAndroidTest
adb reverse tcp:18018 tcp:18018
adb install -r app/build/outputs/apk/debug/app-x86_64-debug.apk
adb install -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
# ready.json 的 room id 作为 -e direct/-e group，-e port 18018
adb shell am instrument -w -e class org.yitiao.yichat.NativeSdkTest#realSdkTwoAccountsE2eePlainGroupAndPersistentKeys -e port 18018 -e direct ROOM_ID -e group ROOM_ID org.yitiao.yichat.test/androidx.test.runner.AndroidJUnitRunner
```

`NativeSdkTest#composeConversationAndDetails` 单独执行实际 Compose 点击；`NativeProcessPersistenceTest` 在前一次 instrumentation 结束后检查相同原生设备密钥。`NativeKeyBackupTest` 检查 SDK 原生备份创建/上传及同设备恢复。它们不是生产 OAuth、跨设备 SAS 或跨设备 SAF 备份的替代验收。

升级用 `UpgradePersistenceTest` 的 phase=seed/check 和同一个非敏感 marker，期间 `adb install -r` 同证书更高 versionCode；不能卸载清数据。

## 已知阻断

已清理原包经认证、校验、补包及保存后，当前官方 FFI 没有供外部 canonical 包注入其 timeline/解密缓存的接口；本版不伪造恢复正文、不重新发送历史。这条端到端恢复显示仍未完成。跨设备验证/恢复、完整系统浏览器/App Links 场景、资金/赠卡/游戏/外部备份的 Android 点击矩阵也需要继续完成；详见交付验证报告。

[安全边界](docs/SECURITY.md) 包含明文索引、附件标准加密及两类备份的差别。
