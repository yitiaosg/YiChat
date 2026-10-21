# Android 0.1.2 登录回跳修复

只修改浏览器登录完成后返回 App 的链路。上一版依赖 Android HTTPS App Links；部分手机把回跳留在浏览器，旧提示页没有可用的 App 返回按钮。

登录使用 AndroidX Browser 1.10.0 Auth Tab，以 ActivityResult 接收固定的 `https://chat.yitiao.org/app/callback`；旧浏览器自动使用 Custom Tabs，保留原 Intent 回调。两种入口共用一次性消费保护，不因兼容回调的 CANCELED 结果清除待交换状态。

网关回跳页新增“返回 YiChat 修复版，完成登录”按钮，使用固定包名 `org.yitiao.yichat.mobile` 的 Android intent；Android 浏览器也会尝试自动返回。若浏览器拦截自动打开应用，用户点击按钮即可继续。参数仅允许唯一的 code/state 和 URL 安全随机串，不接受任意目标包、组件或 fallback URL。页面无第三方脚本，不将查询参数插入 HTML；外部同源脚本遵守原 CSP。

原 HTTPS redirect_uri、PKCE、state、nonce、60 秒单次交换规则、设备恢复凭据、资金及加密密钥逻辑保持原实现。需从 App 重新发起登录，旧的过期交换码不能使用。

## 安装与构建

版本 0.1.2 / versionCode 4。沿用上一版“YiChat 修复版”的包名与签名，可以覆盖升级，不需要卸载。原候选版仍独立保留。

```sh
cd android
# YICHAT_SIGNING_CONFIG 指向源码之外的既有私有签名配置
./gradlew --dependency-verification strict --max-workers=2 :app:assembleRelease
```

## 服务端部署与回滚

本次服务端只需更新 `yichat/backend/native_auth.py`，重启 yichat；不迁移数据库，不更新 Web 静态资源，不重启 YIDT/Matrix。部署前创建原工具备份。本次部署前网关提交为 `fee256c`，回滚只恢复该文件并重启 yichat，保留数据库和密钥。

## 验证范围

实际结果：release 构建成功（含 lintVital），arm64 APK v2/v3 签名及 16KB 页对齐通过；定向 Python 路由测试 1 passed（21 deselected）；JavaScript 10 个用例通过，涵盖自动返回、按钮、固定包名、重复参数/注入/来源拒绝；线上页面及脚本 HTTP 200、CSP/no-store/no-referrer 检查通过。服务端已部署为 3f8e942，部署前备份 /root/yitiao/backups/yichat-v2-20261020T124125Z。

按用户要求没有重跑完整测试，没有使用真实账号或资金。实体机登录结果待用户验证。

实现依据：[Android 官方 Auth Tab API](https://developer.android.com/reference/androidx/browser/auth/AuthTabIntent)、[Chrome Auth Tab 与兼容回退](https://developer.chrome.com/docs/android/custom-tabs/guide-auth-tab)。
