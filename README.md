# YiChat

Android 聊天客户端：Kotlin、Jetpack Compose、Material 3，以及官方完整 Matrix Rust SDK Kotlin UniFFI。私聊使用原生端到端加密，群聊沿用服务端策略；业务、钱包、归档与权限复用 YiChat 网关。

**当前是 2026-10-20 集成候选版，尚未完成正式发布验收。** 生产原生登录适配尚未部署；不要把可安装 APK 视为全部功能已上线。

## 下载

[候选版本说明 → GitHub Releases](https://github.com/yitiaosg/YiChat/releases)

| 文件 | 下载 |
| --- | --- |
| arm64-v8a 签名 APK（常用手机 ABI） | [YiChat-0.1.0-2-arm64-v8a.apk](https://github.com/yitiaosg/YiChat/raw/refs/tags/android-20261020-artifacts.2/apk/YiChat-0.1.0-2-arm64-v8a.apk) |
| x86_64 签名 APK（已实际安装及覆盖升级） | [YiChat-0.1.0-2-x86_64.apk](https://github.com/yitiaosg/YiChat/raw/refs/tags/android-20261020-artifacts.2/apk/YiChat-0.1.0-2-x86_64.apk) |
| 完整交付文件 ZIP（含隔离测试 APK、源码、报告和截图） | [下载制品快照](https://github.com/yitiaosg/YiChat/archive/refs/tags/android-20261020-artifacts.2.zip) |
| 原交付源码 tar.gz | [YiChat-source-20261020.tar.gz](https://github.com/yitiaosg/YiChat/raw/refs/tags/android-20261020-artifacts.2/YiChat-source-20261020.tar.gz) |

安装包与交付文件保存在独立的 [制品分支](https://github.com/yitiaosg/YiChat/tree/artifacts/android-20261020-candidate.2) 和固定标签 `android-20261020-artifacts.2`；主分支保存展开的源码与审查资料。GitHub 打包 ZIP 与原交付 ZIP 的封装不同，内部文件逐字节保持一致；解压后使用制品快照内的 `SHA256SUMS` 校验。

[APK SHA256](delivery/APK-SHA256SUMS) · [验证报告及剩余项](VERIFICATION-ANDROID.md) · [测试证据](delivery/evidence/)

## 源码目录

| 位置 | 内容 |
| --- | --- |
| [`android/`](android/) | Compose 客户端、Repository、Room、WorkManager、版本目录、Wrapper、依赖锁与严格校验 |
| [`source/yichat/backend/`](source/yichat/backend/) | 现有聊天网关与最小原生 PKCE 登录/事务元数据补充 |
| [`source/yichat/client/`](source/yichat/client/) | 现有 Web 行为与可信只读渲染器参考；保留经过回归的编辑竞态修复 |
| [`source/yichat/tests/`](source/yichat/tests/) | 隔离 SQLite、真实 Synapse/模块/专用 PG、OIDC/YIDT 与新增原生接口测试 |
| [`source/`](source/) | 原交接包中的主站、钱包、YCard、跳伞等相关源码；不是生产环境快照 |
| [`delivery/`](delivery/) | 测试记录、真实模拟器截图、APK 摘要和 assetlinks 配置 |
| [`handoff/`](handoff/) | 原交接说明；当前接口优先于旧 reference 材料 |

## 构建

要求 JDK 21、Android SDK platform 36 / build-tools 36.0.0；minSdk 26，compile/targetSdk 36。固定 Gradle 8.13、AGP 8.11.1、Kotlin 2.1.21、官方完整 AAR `org.matrix.rustcomponents:sdk-android:26.10.15`，支持 arm64-v8a / x86_64。

```bash
cd android
export JAVA_HOME=/path/to/jdk21
export ANDROID_HOME=/path/to/android-sdk
export YICHAT_SIGNING_CONFIG=/private/path/signing-private.json
./scripts/build.sh -PyichatVersionCode=2
```

已有签名材料必须在源码外私有保存并复用；不要每次构建更换证书，也不要将密码放入命令行或日志。首次维护者可按交接说明创建自己的专用签名；本次交付签名不在仓库或 Release 中。升级现有安装必须使用相同证书及更高 versionCode，不能卸载清数据。

release 固定使用 `https://chat.yitiao.org`，拒绝明文流量；登录、2FA、钱包授权使用系统浏览器。隔离 debug 构建只供专用测试夹具使用，详见 [Android 构建及联调说明](android/README.md)。

## 已验证范围

- 后端 / 真实 Synapse / 专用 PG / Web 浏览器回归：133 项通过；Web 单元测试 5 项通过。
- JVM 单元测试 6 项通过；严格依赖校验、release 构建与 lint 通过。
- API28 AOSP x86_64 软件模拟器：双账号私聊 E2EE、群明文、加密附件、设备密钥重开和进程保留、同设备原生密钥恢复、Compose 点击收发通过。
- 实际签名 release 从 versionCode 1 覆盖升级到 2，保留 Keystore 数据及同一 Matrix 设备密钥；两个 ABI 均通过 v2/v3 验签与 16KB 对齐检查。

以上不代表完整 Android 验收。没有真机结果，也没有执行生产部署。

## 正式发布前的主要剩余项

- 经认证补回的已清理历史原包，尚不能重新装入官方 SDK 时间线显示。
- 生产原生登录交接、钱包返回适配与 HTTPS App Links 部署及完整浏览器验收。
- 跨设备 SAS / 密钥恢复、SAF 自动备份跨设备恢复与完整 Android 资金 / 赠卡 / 游戏测试矩阵。
- 原生头像图片、建房未知结果的跨重启恢复入口，以及长列表、平板、可访问性和压力测试。

完整限制与早期失败记录见 [VERIFICATION-ANDROID.md](VERIFICATION-ANDROID.md)。

## 部署与安全

[部署、迁移、回滚](android/docs/DEPLOYMENT.md) · [原生接口增量](android/docs/NATIVE-API.md) · [存储及安全边界](android/docs/SECURITY.md)

本仓库及 Release 不含生产数据库、环境凭据、令牌、聊天历史、上传私钥或签名密码。资金与资产测试只使用专用隔离夹具。不要将 `android_fixture.py` 测试端点部署到生产。

[`SHA256SUMS`](SHA256SUMS) 校验本次仓库文件快照；原交接 manifest / 摘要以 `*.handoff-original*` 名称保留，仅用于原基线追溯。后续修改源码应相应更新快照摘要。
