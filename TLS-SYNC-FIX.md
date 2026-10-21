# Android 0.1.4 / code 6：原生 HTTPS 与会话创建

用户现象：网页登录和业务 API 可用，原生同步一直失败；创建好友会话成功，但 SDK 尚未收到房间就打开，显示未同步，Web 出现新空会话。

## 根因及修复

SDK 的 Android TLS 平台初始化缺失。固定版本 sdk-android 26.10.15 的 native 库导出了 `Java_org_matrix_rustsdk_Android_init`，其平台代码要求在任何 Client/网络请求之前用 Application Context 初始化 JavaVM 和 rustls-platform-verifier。之前没有这个 JNI 入口调用，也没有打包该 verifier 的 JVM 组件；HTTP loopback 的隔离测试不会执行 HTTPS 证书校验，因此此前测试没暴露这项缺失。0.1.3 的退避仅处理失败恢复，没有修复 HTTPS 平台接入。

增加官方约定包名的 `org.matrix.rustsdk.Android.init(Context)` 桥接，在 Application.onCreate 中先初始化，再执行 SDK initPlatform，之后才允许 Graph/Client/worker 使用 SDK。TLS 继续使用 Android 系统验证，不关闭证书校验、不信任任意证书、不开放 release 明文通信。SDK tracing 不输出到文件、系统日志或第三方。

native 内嵌 rustls-platform-verifier 0.7.1，对应 JVM 组件 org.rustls:rustls-platform-verifier:0.2.0。仅为该模块增加官方 Maven archive 仓库，固定 archive 提交 1aa691352a5e0c215210dfc9a3c2f2078639dc91。AAR SHA-256 为 aa021794230fbc2f0be355999e2cf67398dd563de066a2e17001ffbd0b69101b；加入依赖锁和严格验证元数据，其他库不升级。

新建私聊前从服务器取得真实 MXID 和已加入会话登记，双方方向均可匹配复用。创建过程加互斥；创建后等待 SDK 取得会话，再打开和返回列表，不再固定等500毫秒就使用未同步房间。不因同步失败删除、清理或重建服务器已有房间，原创建请求的幂等键/未知结果查询保持原规则。

原内部备份、按天外部导出、持久密钥、Auth Tab 登录、资产操作均保持原有实现。无需服务端部署或数据库迁移。

## 定向检查

只做原生 HTTPS 匿名连接检查、会话复用/同步恢复单元测试、release 构建与签名检查。原生检查访问真实 chat.yitiao.org 的公开版本和被鉴权保护的 profile；不使用生产账号、会话凭据、消息正文或资金。

实际结果：

- 修复前，业务客户端 HTTPS 成功；同环境的原生 SDK 抛出 `InternalException: Expect rustls-platform-verifier to be initialized`，定向测试失败，准确复现传输层故障。
- 修复后，API28 软件模拟器安装实际 non-debuggable release 0.1.4/code6（同签名），同一测试 `OK (1 test)`，16.582 秒。原生 SDK 通过 HTTPS 得到网关预期的匿名鉴权拒绝 M_UNKNOWN_TOKEN，证明已跨过 TLS 并取得真实 Matrix JSON 响应，而非把匿名探测当用户已登录。
- 会话复用方向/群聊排除/其他接收者排除及同步恢复定向单元测试共6项通过；严格依赖验证下 release 构建通过；arm64 v2/v3 签名和16KB页对齐通过。

没有运行两账号的完整收发矩阵；原生 HTTPS 测试只验证本次实际缺失的平台接入。测试模拟器已在结束后停止。

没有全量测试。实体机的完整会话收发仍需用户验证。

## 安装与回滚

包名与签名沿用“YiChat 修复版”，直接覆盖安装 0.1.4 / code 6，无需卸载，不清空数据。源码构建使用已有源码外签名配置：

```sh
cd android
./gradlew --dependency-verification strict --max-workers=2 :app:assembleRelease
```

回滚需用同一签名重建旧源码并提高 versionCode 后覆盖安装，不卸载或删除 data/密钥。服务器保持 3f8e942。

依据：[SDK 官方 Android 平台桥接源码](https://github.com/matrix-org/matrix-rust-sdk/blob/main/bindings/matrix-sdk-ffi/src/platform/android_platform.rs)、[rustls 官方 Android 接入说明](https://github.com/rustls/rustls-platform-verifier#android)。
