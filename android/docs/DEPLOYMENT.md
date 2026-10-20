# 原生适配部署与回滚

本交付没有生产主机权限、环境文件、主站/网关凭据，也没有执行生产部署。不要把本地签名 APK 或隔离 OAuth 测试描述成生产原生登录已开通。

## 变更范围

现有网关仍是主站 OIDC 回调客户端，Web cookie OAuth 不变。新增三个表族及一个网关元数据表，只含短期原生交接数据、设备恢复凭据的 SHA256、必要文件/业务引用和消息事务结果；不接收 Matrix 私钥或解密正文。

- `native_logins` 绑定现有登录状态到 App state/nonce/PKCE；600秒登录事务。
- `native_codes` 绑定已验证主站签名 uid、用户名、SID，60秒一次性交换码。只存码摘要。
- `native_devices` 保存设备恢复证明摘要及 uid/MXID/device；原生重新授权必须证明原设备所有权，不能仅提交 device_id。
- `native_transactions` 用 uid+device+room+SDK txnId 唯一绑定外层附件/业务引用及接受的 event_id；同事务不同引用409。原 SDK 加密正文保持原样。

`backend.db.migrate()` 的 CREATE TABLE IF NOT EXISTS 在服务启动执行；没有改现有金融表、订单冻结字段、Matrix 密钥或封存规格。备份 chat.db 后执行，保留原 SHA/版本及代码发布目录。

## 接口

1. 系统浏览器 GET `/auth/native/login`：仅 state、nonce、code_challenge、code_challenge_method=S256、redirect_uri。回跳严格固定为 `YICHAT_ORIGIN/app/callback`，不接收任意 URI。
2. 主站继续回现有 `/auth/callback`，依原逻辑验证 RS256 issuer/audience/nonce/uid/SID。原生事务不改 Web 的 yc_session cookie；只303回 App 的 code+state。
3. App TLS POST `/auth/native/exchange`：code/state/nonce/code_verifier/固定 redirect_uri，以及恢复原设备时的 device_proof。无 Origin、Sec-Fetch-Mode 或浏览器会话；PKCE 验证后一次性消费。返回网关 Bearer、真实身份/设备、device_proof。所有验证失败都拒绝；重放/超时400、SID失效401。
4. `/app/callback`、`/app/return` 为无 App 时的静态 Web 回退，不反射查询参数、不自动执行资金操作。
5. POST `/api/native/rooms/{room}/transactions/{txn}` 只接受 attachments / business 元数据；只限原生设备、当前房间及本人上传。GET 同路查询原接受结果。网关在 SDK 原 send 请求上添加已绑定的最小外层 ID，仍运行全部现有 policy/Synapse 校验。
6. 原生设备创建的 YIDT intent 使用固定 `/app/return?order={id}`。钱包批准后只带订单引用；App再查原订单，用户另行明确 fund。Web原返回保持不变。需要同步部署 `yidt.py` 的原生返回适配；不要只更新 App。

FFI 26.10.15 实际提供 classic `syncV2`，本次没有扩大 Matrix unstable/admin/register/federation 路由。所有原生设备的业务及 SDK 请求在网关统一按 uid 约6次/秒准入，原10请求/秒最终限制保留。429将共享的 native_requests 虚拟时钟后移；长轮询前后 SID 仍复核。这个实现是服务端协调，不能称为 OkHttp 截获了 Rust HTTP。

## 上线顺序

1. 隔离环境先执行 Python 回归和 Android 验收。只使用专用数据库/测试用户，禁止生产 DSN、资产或签名私钥。
2. 保存原版本代码、部署配置和一致性数据库快照；`chat.db`与主账本遵循现有整体备份规则，不单独回滚金融账本。
3. 更新网关源码、`yidt.py`；以现有服务用户和配置启动，确认表创建、Web OAuth/全站 SID 退出及健康检查；依旧不公开 Synapse/PG 端口。
4. 在 chat 域名发布本目录 assetlinks.json，HTTP200，application/json，无重定向。文件证书指纹对应本次随机专用签名；以后升级必须复用该证书。
5. Caddy 主机段可在已有 reverse_proxy 前加：

```caddy
handle /.well-known/assetlinks.json {
    root * /etc/yichat/android
    header Content-Type application/json
    file_server
}
```

复制文件为 `/etc/yichat/android/.well-known/assetlinks.json`。检查现有 handle/route 顺序，以当前实际 Caddy 配置为准。
6. 使用授权测试账号验证安装/未安装时的 HTTPS App Links、系统浏览器取消、SID退出、重放/超时及原设备重新授权。未进行这些生产验证前，状态仍为待部署/待验收。
7. 反向代理与 uvicorn 对 OAuth/native callback 禁止记录含 code/state 的查询字符串；应用不启用正文或认证头日志。

## 回滚

恢复原网关和钱包代码及 Caddy 路由；先撤下原生 App 入口/提示未完成登录。附加表可保留，不删除设备、密钥或账本。已有 native token 会话如需停止，按 sessions 中与 native_devices 对应的 uid/device 设置 revoked，由原维护流程撤销 upstream token；不要调用删除设备的 Matrix logout。不得独立恢复旧 userdata.db 来覆盖新资金记录。

新 APK 使用同一专用 keystore 覆盖升级；不通过卸载回滚 App，以免删除 Keystore 和未导出的历史密钥。更高 versionCode 发布修复版。签名材料另行私有保存，源码包没有 keystore 或密码。

## 其他经过回归的修改

Web Composer 编辑初始化使用 `useLayoutEffect`，避免进入编辑态后的被动 effect 覆盖用户刚输入的内容；保留原交互与测试。Android 业务 GET 可安全恢复服务器已关闭的连接；所有业务写入及上传使用 one-shot body，禁用透明连接重试，503 Retry-After=0 也不得自动重放 fund。
