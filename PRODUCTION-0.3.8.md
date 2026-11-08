# YiChat 0.3.8：本机备份历史分页与 GitHub 更新检查

Android versionCode 16，包名 org.yitiao.yichat.mobile，使用原签名覆盖升级。

## 改动

App 离线、服务器请求失败或已无更早消息时，“加载更早消息”从本机自动备份的原消息包库分页读取。原生桥重新校验原包 SHA-256、会话和事件身份，过滤撤回、墓碑及状态事件；共享前端去重，联网后继续服务器分页。Room 仅新增 olderReceipts 查询，数据库版本和 schema 不变。

App 启动/回前台检查本仓库 GitHub Releases 最新版，自动联网检查间隔六小时；“稍后”暂缓一天，设置中支持手动检查。更新下载只接受本仓库 GitHub APK 地址；资产名采用 YiChat-<版本>-<versionCode>.apk，目标为系统浏览器。共享网页下载入口更新为 0.3.8，旧 App 不支持的桥调用按既有降级提示处理。

本版保持 plain-20261029-v1，不清理聊天、备份或钱包，不修改后端、数据库、资金协议或 /root/yitiao 源码。沿用原私有签名与 Firebase 客户端配置，配置不进入源码和附件。编译和 lint 无需功能修复；仅另外提交网页下载链接及 Android README 的版本更新。

## 验证

- 环境：Node 22.23.3、OpenJDK 21.0.13、Android SDK platform 36 / build-tools 36.0.0。
- npm ci 成功；Web 单元测试 34 项通过，TypeScript/Vite 构建成功。Gradle 自动重新构建同一份共享前端。
- ./scripts/build.sh 成功，assembleRelease / testDebugUnitTest 通过；Android Debug JVM 18 项，含新增 AppUpdatesTest 3 项，失败、错误及跳过均为 0。
- --dependency-verification strict :app:lintRelease 成功：0 errors，63 warnings、1 hint。未降低 lint 等级、未修改依赖验证元数据或锁文件。
- aapt2 dump badging：versionName 0.3.8、versionCode 16、org.yitiao.yichat.mobile；apksigner 验证成功，v2/v3 签名有效；zipalign 16 KiB 检查通过。
- APK 9772690 字节，SHA-256 `74f1435d383f95ec9853568351d24845ea9a044649caf15ea8387e12bfcb5c8d`。证书 SHA-256 `9e7505f84f84c28ac920a5da3470a452e7923b76c54167ce1c942afc56c7af3e`，与从 GitHub 下载的 0.3.7 APK 完全相同。
- APK 内 app-shell 的 14 个资源与本次 dist 文件集合及内容逐字节一致；部署目录也逐字节一致。公网 13 个路由资源可访问，JS/CSS/WASM 和图片/渲染器内容一致；HTML 保留本次构建内容，Cloudflare 会追加脚本。notification.html 未由现有后端直接暴露，沿用 App WebShellCache 的 APK 回退，不改后端路由。
- chat.yitiao.org /health 正常，message_epoch 未变；/app/download 仍为 404。生产源码 HEAD、diff 和工作树状态的部署前后摘要相同。
- 本次没有物理 Android 设备，adb devices -l 为空；没有执行真机覆盖安装、数据保留、默认浏览器、稍后提示、飞行模式历史/撤回/恢复网络去重和手动检查更新交互验收，不能将 JVM 测试视为这些项目通过。

## 网页发布与回滚

按 WEB-DEPLOYMENT.md 和 web-release.sh 的构建流程执行 npm ci / test / build，使用仓库 promote_web.py 原子发布静态文件。通过独立临时源码目录传入 --repo，避免写入 /root/yitiao，并使 release.json 记录本次真实构建提交 `40e7a7bd5ab934a50a8a26f764da3d9b027a6e7a`。本次静态版本 `/var/lib/yichat/web/releases/20261108T154151Z`，前版 `/var/lib/yichat/web/releases/20261107T030433Z`；旧哈希 assets 已保留。

现有网关每次请求沿 current 链接读取文件，切换后源站与公网检查通过，无需重启后端或执行迁移。若需要回滚，仅原子切换 current 到上述前版，不还原数据库或清理客户端数据。公网默认 Python User-Agent 被既有 Cloudflare 规则拦截（1010），普通浏览器标识访问正常；没有修改 CDN 配置。

## 升级和检查

发布 tag：android-20261108-v0.3.8；资产严格使用 [YiChat-0.3.8-16.apk](https://github.com/yitiaosg/YiChat/releases/download/android-20261108-v0.3.8/YiChat-0.3.8-16.apk)，同时提供 SHA256SUMS。[GitHub Release](https://github.com/yitiaosg/YiChat/releases/tag/android-20261108-v0.3.8) 为正式最新版。

0.3.7 原生源码没有更新检查实现，因此未修改的 0.3.7 无法在本次发布后自动弹出原生“发现新版本 0.3.8”；首次升级需通过网页下载入口或上述 GitHub 链接。0.3.8 安装后才具备为后续版本提示更新的能力。不要卸载或清数据；由用户在设备上覆盖安装并完成既定 a/b/c 验收。

证据位于 delivery/android-0.3.8-20261108。根目录 SHA256SUMS 校验源码及报告；Release 附件 SHA256SUMS 校验 APK。
