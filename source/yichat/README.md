# YiChat Web 与服务端 v2

本目录包含正式 Web 聊天客户端、主站 OAuth 网关、原生 Matrix Rust Crypto、Synapse 策略模块、YIDT/YCards 业务和 PostgreSQL 归档。前端源码位于 client/，不使用 Telegram 代码；归档仅下载与本地校验，不导入重发。

主站 `https://yitiao.org`，会话入口 `https://chat.yitiao.org`，钱包 `https://yidt.yitiao.org`。Caddy 只将聊天域名转发到 `127.0.0.1:5018`；Synapse `127.0.0.1:8018` 无公开监听/媒体仓库/联邦/注册。PostgreSQL 只监听本机，使用独立库和 ingest/seal/read/cleanup 角色。

## 操作与交付

- [Web 构建、部署与回滚](docs/WEB-DEPLOYMENT.md)
- [Web 实现与边界](docs/WEB-IMPLEMENTATION.md)
- [本轮实测](docs/WEB-TEST-RESULTS.md)
- [当前聊天内核完整接口文档](docs/CHAT-KERNEL-API.md)
- [账户页面、GMT+0及YIDT启用更新](docs/ACCOUNT-UPDATE-20261019.md)
- [部署、备份、迁移、回滚和必填项](docs/DEPLOYMENT.md)
- [接口、认证、关系、隐私和 SDK 契约](docs/API.md)
- [归档 v1 字节格式、鉴权、保留和信任边界](docs/ARCHIVE-V1.md)
- [YIDT 实际账本、状态机、费用、对账与恢复](docs/FUNDS.md)
- [真实测试记录](docs/TEST-RESULTS.md)
- [运行边界](docs/SECURITY-AND-OPERATIONS.md)
- 本地校验工具：[tools/verify_archive.py](tools/verify_archive.py)

```bash
cd /root/yitiao/yichat
sudo .venv/bin/python -m backend.manage status
sudo .venv/bin/python -m backend.manage reconcile
sudo systemctl status yichat yichat-synapse yichat-archive
```

Python 3.11，Synapse 固定 `1.163.0`，依赖在 `requirements.in`/`requirements.lock`。Web 构建使用 Node 22，运行服务不需要 Node。客户端使用 matrix-js-sdk 的 initRustCrypto()；设备验证和备份沿用原协议。模块使用 [Synapse](https://github.com/element-hq/synapse/tree/v1.163.0) 的回调，并有一个明确固定版本的只读事件源适配器，不修改核心或写内部表。

资金默认保持原配置；缺官方托管钱包或显式开关时返回 503。YIDT 是原项目已有的中心化哈希链账本，精度 `10^-16`，没有外部 RPC/区块确认/重组协议。本次不会使用真实资产试跑。
