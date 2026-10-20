# YiChat Android 集成候选版

这是实际可安装、同一专用证书签名的候选APK及源码，**尚未完成正式发布验收**。先读 VERIFICATION-ANDROID.md 的阻断/剩余项。

- `apk/YiChat-0.1.0-2-arm64-v8a.apk`：手机常用ABI，minSdk26/target36。
- `apk/YiChat-0.1.0-2-x86_64.apk`：模拟器ABI，已实际安装与覆盖升级。
- `YiChat-source-20261020.tar.gz`：原生源码、最小后端/钱包/Web补充、构建脚本、锁/校验、测试、部署/迁移/回滚说明。
- `backend-and-web.patch`、`backend-delta.json`：相对原ZIP的11文件可审查补丁，已在原基线dry-run通过。
- `config/assetlinks.json`：必须由chat域名部署，证书匹配实际APK。`android/docs/DEPLOYMENT.md`给出部署与回滚步骤。
- `evidence/`、`screenshots/`：隔离测试/真实模拟器点击、验签/对齐、升级和早期失败记录。
- `apk/YiChat-ISOLATION-*`：仅用于README中的专用fixture/SDK/Compose测试，不是生产登录演示，不要用真实账户或资产。release没有这些测试端点/loopback URL。

release 固定使用HTTPS chat域名。未部署新增原生PKCE交接及App Links前，不能保证生产登录回跳；本次没有生产部署权限，也未执行生产部署。

安装升级用同一证书、更高versionCode，使用覆盖安装，不要卸载清数据。新生成的专用签名材料在单独的 `YiChat-signing-PRIVATE.zip` 中（不在公开交付ZIP里），包含私钥和密码，离线私有保存，勿公开/勿提交Git。该私有归档本身未加密，请导入你的密码管理器或加密离线介质；解压后按PRIVATE-README调整配置的绝对路径。
