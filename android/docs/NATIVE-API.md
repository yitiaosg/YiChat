# Android 网关增量协议

本文件是新增原生适配说明；现有业务接口仍以 `source/yichat/docs/CHAT-KERNEL-API.md`（含第16节取消订单）为准。

身份分别保存 immutable uid、username、MXID、device_id、主站 SID。App 不接触主站密码、HttpOnly cookie、支付密码或主站签名私钥。服务端继承原 OIDC 签名验证与 SID 撤销规则，原生网关 token 不直连 Synapse。

精确接口/迁移/代理配置/回滚见 DEPLOYMENT.md。native_login 使用固定 HTTPS callback，state/nonce、S256 verifier、60秒一次性交换码；TLS POST 的 JSON 返回 bearer。它不是可直接交给 AppAuth 的 OIDC authorization/token endpoint。URL 不含长期 token。

`native_transactions` 针对官方高层加密 API 不能保留自定义外层 ID 的实际缺口：先持久化同一 SDK txnId 与 attachments/business 元数据，后恢复发送队列；网关只加入经过所属房间、文件所有者与业务权限复核的外层引用，不改 SDK 原密文。GET 查询 accepted_event 用于结果不明，重复不同元数据409。同一 SDK 队列中的原发送才能重试。

原生设备的 Rust 与业务 HTTP 都经过网关同一 uid 调度；没有扩大 Synapse 管理/注册/联邦路径，没有绕过业务建房。429 推迟共享准入时钟，最终10次/秒保护保持。Android 自己的 OkHttp pacing 只覆盖业务请求。

取消仍调用原 `POST /api/orders/{id}/cancel`，仅付款人/未入金；fund/cancel 沿用同一账本事务互斥。Android 先持久化独立业务 key，取消失败查原 id，重新开始才新建 key；取消不是退款。钱包回跳仅带 order 引用并查权威状态。金额为十进制字符串与 BigDecimal。
