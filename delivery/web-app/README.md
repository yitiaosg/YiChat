# 本次基础验证证据

browser-result.json / browser.log：8项针对性检查通过，真实隔离网关和Synapse；仅Android消息桥与系统目录选择结果使用测试替身，不代表真机登录/SAF已验收。

web-app-private.png、web-app-offline.png、web-app-backup.png：Playwright的实际手机尺寸页面，数据来自隔离账号；不是Android模拟器截图。

production-web-welcome.png：实际生产HTTPS匿名普通浏览器页面，无App专属UI。

unit-results.json：当前7个Kotlin JVM测试无失败；frontend-build.log、android-build.log为当前构建输出。详细说明见根目录WEB-APP-REFACTOR.md，Android执行范围见ANDROID-RUNTIME.md。
