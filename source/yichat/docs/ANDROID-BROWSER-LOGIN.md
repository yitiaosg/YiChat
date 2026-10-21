# Android 浏览器授权修复

2026-10-20 回跳补丁（App 0.1.2）：登录改用 Auth Tab 的 ActivityResult，兼容旧浏览器 Intent 回调；`/app/callback` 增加固定指向 `org.yitiao.yichat.mobile` 的返回 App 按钮及同源脚本 `/app/native-return.js`。不依赖手机已启用 HTTPS 链接关联；自动打开受浏览器限制时点按钮继续。PKCE、一次性交换及固定 HTTPS redirect_uri 不变。本次仅需更新 native_auth.py 并重启 yichat，无数据库迁移；可恢复到 fee256c 的该文件回滚。本次按用户要求不做完整回归。

来源：YiChat `android-20261020-candidate.2`，提交 `325fb6c`。线上基础版本 `20238cd`。本补丁只合入原生网关接入，不替换主站配置、Web 静态发布目录、Matrix 核心或资金内核。

## 原因与行为

旧 App 的 ACTION_VIEW 打开完整浏览器；线上尚无 native login/callback/return 与 assetlinks 路由。仅更新 APK 无法登录。修复版使用 AndroidX Browser Custom Tabs，主站已有 OAuth/两步验证及 YIDT 仍在浏览器执行，HTTPS App Links 返回原生 App。一次性回调只消费一次，Activity 重建不重复交换。

公开 App Links 包含旧候选版及可并存的 `org.yitiao.yichat.mobile`。新签名不能覆盖旧签名，故修复包独立安装以保留旧 App 数据。新证书 SHA-256：`9E:75:05:F8:4F:84:C2:8A:C9:20:A5:DA:34:70:A4:52:E7:92:3B:76:C5:41:67:CE:1C:94:2A:FC:56:C7:AF:3E`。私钥不在源码、文档或服务器路由中。

## 部署

1. 完成隔离测试；确认生产工作区干净，记录原提交及 Web current 链接。
2. 运行现有 `yichat/deploy/backup.py` 创建整体备份。禁止把测试账本或测试配置复制到生产。
3. 合入 `backend/app.py,db.py,funds.py,matrix.py,native_auth.py`、根目录 `yidt.py` 以及接口文档。其他代码及密钥保持原文件。使用原服务环境执行 `python -m backend.manage migrate`，仅幂等添加 native_* 表。
4. 重启 yichat、yidt。现有 Caddy 反代直接提供 `/.well-known/assetlinks.json`，无需另改代理。它须 HTTPS 200/application/json、无重定向，证书指纹须与实际 APK 签名完全一致。
5. 检查六项服务、网关/YIDT健康、公开 App Links、原 Web 首页、native 参数拒绝及合法登录起始跳转。执行账本 reconcile，只输出一致性结论，不输出用户金额/订单标识。

## 构建

JDK21、Android SDK36/build-tools36.0.0、固定 Gradle8.13 与仓库 wrapper 校验和，保持依赖锁及 SHA-256 verification-metadata。设置 `YICHAT_SIGNING_CONFIG` 指向源码外0600权限的签名配置，然后：

```sh
cd android
./gradlew --dependency-verification strict :app:assembleRelease :app:testDebugUnitTest
```

发布 `app-arm64-v8a-release.apk`；x86_64用于模拟器，universal体积较大。隔离测试另加 `-PyichatIsolation=true`，只改变 debug 的固定 loopback 网关，测试 APK/HTTP 回跳不能当发布包分发。release 固定 HTTPS、拒绝明文流量，manifest 不包含 loopback intent-filter。

## 回滚

恢复 `20238cd` 对应网关及 yidt.py 源码并重启服务；保留新增表，不删除设备/密钥/归档，不单独恢复旧资金数据库。Web 发布目录不变。若需停止原生会话，使用原会话撤销机制；不要调用会删除 Matrix 设备的 logout。

Android 后续更新须复用本次私有签名，增加 versionCode。不要通过卸载旧 App 回滚或迁移密钥；可并存包不能直接读取旧包 Keystore。备份私有签名独立于公开源码和 APK。

## 验证边界

自动化只使用专用 loopback 身份、SQLite 账本和受限 PostgreSQL 测试角色。生产检查不登录真实用户、不操作资金资产。模拟器覆盖不等于实体机和所有厂商浏览器覆盖；App 历史包恢复展示、SAF、推送及其他原候选版未验收项目不因本补丁自动视为完成。
