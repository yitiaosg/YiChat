# YiChat Android 网页容器

当前 0.3.5 / code 13 / `org.yitiao.yichat.mobile`。单 APK 不分 ABI，Matrix 使用同源网页 SDK，聊天不再启用 E2EE。最低 Android 8（API26），需要 Android System WebView / Chrome 内核 100+，并支持 WebMessageListener、Web Locks、IndexedDB；过旧内核会提示更新。

JDK21、Android SDK36/build-tools36、Node22+。Gradle8.13、AGP8.11.1、Kotlin2.1.21，AndroidX WebKit / Browser、Room、WorkManager、OkHttp。无 Compose/Matrix FFI。先在仓库根目录运行 `npm --prefix source/yichat/client ci`，再：

```bash
cd android
export JAVA_HOME=/path/to/jdk21
export ANDROID_HOME=/path/to/android-sdk
export YICHAT_SIGNING_CONFIG=/private/path/signing-private.json
export YICHAT_FIREBASE_CONFIG=/private/path/google-services.json
./scripts/build.sh
./gradlew --dependency-verification strict :app:assembleDebug :app:assembleDebugAndroidTest :app:lintRelease
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
adb shell am instrument -w -e class org.yitiao.yichat.WebShellTest org.yitiao.yichat.mobile.test/androidx.test.runner.AndroidJUnitRunner
```

在全新隔离模拟器执行 instrumentation；它使用匿名网页与本地测试数据库，不使用真实账号。不要对已有用户存储的手机运行测试。JVM 测试覆盖链接边界、备份口令/篡改、429 和未知结果查询。真实双账号 JS SDK 普通消息同步、刷新与离线缓存的基础检查使用 `source/yichat/tests/browser/web-app.mjs` 和独立 loopback fixture；详见本次报告。

网页代码来自 `../source/yichat/client`，preBuild 自动构建并嵌入完整 dist。在线使用同源带 `yichat-app-bridge` 标记的新网页，失败回退 APK，避免老页面绕过 App 登录。更新网页无需维护第二套聊天 UI。

只准确聊天源主框架能调用桥；网页外链统一系统浏览器。退出锁定数据并排队撤销服务器会话，保留归档与登录凭据。旧原生源和测试已归档于 `legacy-native`；不要恢复旧 JNI 初始化或将旧设备证明注入新 JS 加密库。

签名配置必须位于源码外，不入库；现有安装升级必须同证书、更高 versionCode。回滚 APK 也必须提高 versionCode，不要使用卸载或降级清数据。

安装包统一从 [GitHub Releases](https://github.com/yitiaosg/YiChat/releases/latest) 下载。后台通知预览及分享显示说明见 [0.3.5 更新](../PRODUCTION-0.3.5.md)。
