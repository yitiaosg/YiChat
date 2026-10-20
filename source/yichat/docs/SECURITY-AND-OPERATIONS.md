# 运行边界

- 公共入口只有 Caddy→5018。8018、5432 均本机监听；没有公开对象目录、原生媒体仓库、联邦、注册、管理或对象直传。禁止额外映射这些端口作为替代入口。
- 身份稳定依赖 account_ids.uid 和既有 Matrix identity 映射；旧 MXID、设备、交叉签名、加密备份保留。主站 sid 每请求检查，原生 token 由模块可靠撤销，设备密钥不随主站退出删除。
- Matrix 房间消息与资产操作都使用服务器注册的会话类型/参与者。私聊发信动态检查，群聊不因私聊拉黑默认禁止共同发言。设备间通道只接受 Matrix Olm/密钥请求/验证信令，不是聊天消息接口；未来客户端不得把 to-device 内部数据渲染成会话、卡片或资金指令。既有 E2EE 会话仍可交换恢复/群密钥；服务器不能检查加密内文。
- 文件内容/当天快照读取须等待实际源 allocation fence 被持久游标覆盖，不能用延迟副本中的旧 join 掩盖遗漏的 leave。整日文件另需完整日权限证据。
- API PostgreSQL role 不能写封存摘要；采集、封存、清理使用独立角色，摘要禁止 UPDATE/DELETE。现有 OS 部署仍使用 root 与原本地账本事务，不把 SQL 角色隔离等同于抵御整台宿主被攻陷。
- 正文/E2EE 私钥不进入日志。源事件只读采集、真实附件字节 SHA、版本/规格和接收日确定归档。摘要不是第三方不可抵赖证明，也不能防止恶意服务器。
- 归档采集/封存失败、心跳过期、哈希不符、旧附件缺失、源回退、晚到已封存日期事件都会停止对应清理并在 journal 产生 ERROR；已封存日期出现新缺口时隔离完整日期下载，不能继续冒充完整文件。
- 清理只经证据门控。Synapse 状态/前向极值/必要加密状态、权限证据和金融/资产账本有明确例外。对象版本、额外缓存、CDN 或全盘备份由运营方落实对应生命周期；应用不声称删除用户副本。
- 资金入金/出金使用真实原账本和固定 reference，不接收客户端“成功通知”。账本与 outbox 偏离先对账，不盲目重付。主站退出阻止新授权，但不撤销已经承诺给接收者的财务任务。

日常核验：

```bash
sudo /root/yitiao/yichat/.venv/bin/python -m backend.manage status
sudo /root/yitiao/yichat/.venv/bin/python -m backend.manage reconcile
sudo journalctl -u yichat-archive -u yichat-synapse -p err --since today
sudo ss -lntp
```

接口、财务、归档字节规格和回退分别见 API.md、FUNDS.md、ARCHIVE-V1.md、DEPLOYMENT.md。任何恢复不能混用新财务记录与旧账本，也不能重新生成已有用户的 uid/Matrix 身份。
