> 2026-10-20 接力修复：当前版本为 0.1.1 / code 3、可并存包 `org.yitiao.yichat.mobile`。当前部署、下载及验证以根目录 [BROWSER-LOGIN-FIX.md](../BROWSER-LOGIN-FIX.md) 为准。以下候选版记录保留为历史，原 APK 链接不是本次修复包。

# 2026-10-20 候选版交付材料

APK、完整交付文件和原交付源码压缩包存于 [独立制品分支](https://github.com/yitiaosg/YiChat/tree/artifacts/android-20261020-candidate.2)，不进入主分支源码历史。固定标签为 `android-20261020-artifacts.2`，从 [候选版本页面](https://github.com/yitiaosg/YiChat/releases) 或根 README 下载。该候选版尚未完成正式发布验收。

- [验证报告与剩余项](../VERIFICATION-ANDROID.md)
- [APK SHA256](APK-SHA256SUMS)
- [验签/对齐、构建、测试和升级证据](evidence/)
- [真实软件模拟器截图](screenshots/)
- [实际专用签名证书匹配的 assetlinks.json](config/assetlinks.json)

签名私钥及密码包另行私有保管，不提交本仓库、不作为 Release 附件。自动生成的运行会话、数据库、生产环境文件及依赖缓存同样不提交。

`evidence` 同时保留早期失败及最终重测日志；最终状态以根目录验证报告为准。所有手机截图来自模拟器，不是真机。
