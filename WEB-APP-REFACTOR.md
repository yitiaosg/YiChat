# YiChat 0.2.0：复用网页的 Android App

本次将聊天界面统一到现有 Web 前端，删除活动构建中的 Compose / Matrix Kotlin FFI。旧源码移到 android/legacy-native，旧数据、设备密钥和归档不删除。包名仍为 org.yitiao.yichat.mobile，code7，同证书覆盖升级，通用 APK 约8MB。

## 实现

- 独立 WebView 只显示聊天主页；登录、主站、YIDT、其他站点交由系统浏览器。现有 Auth Tab / Custom Tabs + PKCE、App Links 和钱包返回沿用。返回订单不自动 fund。
- 网页与 App 共用 source/yichat/client。App 仅增加受准确源/主框架限制的消息桥，无任意原生调用、文件路径访问或凭据导出。
- APK 内置完整静态框架与加密 WASM；可信同源公开静态资源在线更新，失败回退内部缓存或 APK。不缓存认证 API/媒体。
- App 用 IndexedDBStore 保存同步快照，原有 Rust WASM 密钥持久保存；普通 Web 仍使用原先临时时间线。已登录且完成首次初始化后，离线可打开已缓存最近会话、解密已缓存私聊、搜索缓存并写草稿；发送、首次初始化、未缓存历史/媒体/订单需要网络。恢复联网后 SDK 继续同步。
- 离线不自动刷新资料、发送已读或启用附件/资金操作，避免失败弹层遮挡页面。顶部离线条不遮住会话标题。
- 框架按钮/导航不可长按选择，正文、输入框与恢复密钥仍可复制。附件使用系统文件选择，录音按麦克风权限；图片原图在聊天弹层查看。下载通过受限100MB分块桥调用系统保存窗口，取消不产生未处理错误。
- App 数据备份入口提供内部加密快照、按1～3650整数天外部导出、立即导出、停用。后台任务由系统择机执行。内部备份在 no_backup/backups，原包库在 no_backup/web-accounts；私聊保存原生密文。
- 外部 YCB1 沿用 scrypt + AES-GCM，不含登录令牌、设备证明和 Matrix 私钥；解密密钥依旧在“安全与密钥备份”独立管理。
- 新网页设备有独立 web. 会话命名空间，避免误用旧原生设备。仅同一uid/MXID的旧可信原包及导出设置可迁移，旧数据库和私钥原地保留。

## 已执行基础验证

本次按要求做针对性基础验证，未执行完整业务回归，也未操作真实资金或卡片。

| 检查 | 结果及边界 |
| --- | --- |
| 网页 TypeScript / Vite production build | 通过；含真实 Rust WASM 和两种站内渲染器 |
| 网页单元测试 | 5/5通过：精确金额、Unicode、预览白名单、429、可信原包校验 |
| Kotlin JVM | 7/7通过：链接白名单/缓存边界、可携备份与篡改、429及未知结果处理 |
| Release / Debug / AndroidTest APK | 严格依赖校验构建通过 |
| Android lintRelease | 0错误；36条现有版本更新、本地化与特性检查等警告 |
| APK签名/对齐/内容 | v2/v3签名通过，zipalign通过，APK中的9个框架文件与验证dist逐字节一致 |
| Playwright + 独立真实 Synapse | 8/8通过：手机布局/禁选、双账号私聊E2EE、刷新保留密钥、群明文、附件下载分块桥、离线重开解密缓存、App备份面板/整数天校验、重新联网同步；无页面JS异常 |
| Android实际基础运行 | API28安装成功，3项通过/1项现代引擎条件跳过：内置资源、内部加密备份、更新提示及浏览器Intent；现代WebView真机登录/SAF未验收 |
| 生产匿名普通Web页面 | 实际HTTPS打开成功，登录入口正常，无App专属UI，无JS异常 |

Playwright 中仅原生消息端口与系统目录选择结果由测试替身提供；同步、Rust加密、HTTP上传、消息及服务端权限均为独立真实网关/Synapse。该结果不能替代真机 Auth Tab、原生SAF文件读写和Android WebView内的完整登录验收。

Android 新容器的 WebShellTest 已编译，覆盖离线APK框架、真实WebMessage端口、IndexedDB重开、浏览器Intent和内部加密备份。模拟器实际执行结果另见 delivery/web-app/ANDROID-RUNTIME.md；未执行的项目不得视为通过。

## 已知限制

- 首次安装/首次网页设备加密初始化必须联网；离线不会提供未下载的附件、头像、更多历史或资产状态。缓存不是全量历史恢复。
- 旧原生 Matrix 私钥保留，但没有 Rust Kotlin→JS SDK 的本地自动转换器。升级网页设备后，旧私聊历史需要使用原 Matrix 密钥备份/恢复或其他可信设备共享历史密钥。
- 经认证补回的服务器已清理原包会保存在原生备份库，尚未注入网页SDK时间线；不会伪造解密正文或重新发送历史。
- 本次没有全量资金、赠卡、跨设备恢复/验证、所有厂商系统浏览器和后台定时精度测试。Android可能延后WorkManager任务。

## 构建、部署、回滚

详见 README.md / android/README.md。网页 npm ci 后，Gradle preBuild 自动构建并嵌入同一dist；私有签名配置位于源码外。现有安装用同证书、更高code覆盖升级，禁止卸载或清除存储。

2026-10-20 已部署共享前端，服务器源码commit `4250084486e041ecc77f20775928475a04cd18d7`，静态release `/var/lib/yichat/web/releases/20261020T153651Z`。未变更后端、资金、Synapse和归档规则。修改前私有备份为 `/root/yitiao/backups/yichat-v2-20261020T152007Z`。

网页回滚（只替换静态链接，旧release仍保留）：

```bash
sudo ln -s /var/lib/yichat/web/releases/20261020T040117Z /var/lib/yichat/web/current.rollback
sudo mv -Tf /var/lib/yichat/web/current.rollback /var/lib/yichat/web/current
```

App会拒绝不含bridge标记的旧网页入口，自动使用APK内的兼容框架。若要回滚App实现，请从上一提交b2e583f重新构建，并将versionCode提高至8或以上、沿用同一签名；不要尝试卸载/清数据降级。两类设备库互相保留，不能强行复用不同SDK私钥库。

## 签名

沿用前版随机生成并私有保存的开发证书，以支持覆盖升级和现有App Links：SHA256 `9e7505f84f84c28ac920a5da3470a452e7923b76c54167ce1c942afc56c7af3e`。本次未重新生成证书，签名私钥和配置不会进入源码包。
