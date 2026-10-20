# YIDT 转账、红包和资产事务

2026-10-19后续更新：用户已授权启用生产资金功能，使用核实存在的原官方托管钱包；当前状态见health/session，流程与字段详见 [CHAT-KERNEL-API.md](CHAT-KERNEL-API.md)。下述缺配置时拒绝操作的保护仍保留。

## 真实系统

原项目 `yidt_core.py`、`userdata.db` 是权威中心化哈希链账本，SCALE=10^16、最低红包金额 CENT=10^14，gas=39062500000000 原子单位，即 0.00390625 YIDT。所有余额/账务沿用原表，无第二套同名余额。Python 整数计算，SQLite 十进制 TEXT 储存，避免 64 位溢出和 JSON 浮点损失。

官方托管地址必须匹配现有 `yidt_core.OFFICIAL`。配置缺失或 `YICHAT_FUNDS_ENABLED` 未显式为 true 时，真实资金 API 503。本账本没有外部广播、区块高度、外部确认要求或链重组协议，不虚构 RPC/private key。账本事务提交就是本系统确认点；hash chain、postings、余额和 escrow 对账失败即停止出金。若将来改成外部链，需要另实现确认深度、重组回退、广播/查询和终局性适配，不能沿用数据库原子事务假装链上原子性。

绑定沿用 YIDT 导入/钱包控制验证与服务绑定，不接受一个任意填写的收款地址。新订单固定不可变双方 uid、当时的用户名记录、room、类型、源/目标钱包、本金、份数、费用、授权 intent、幂等键；冻结字段有禁止 UPDATE 的触发器。更改绑定不会静默改订单地址。已进入财务队列的记录不会因消息撤回、聊天清理或退出而消失。

## API 与授权

- POST `/api/orders`：`{room,key,amount,count?,title?}`，key 为 16～100 位字母/数字/_/-；同 uid/key 相同 payload 幂等，不同 payload 409。
- GET `/api/orders/{id}`：真实状态、固定钱包、金额、费用、已领取列表、手气最佳、本人金额、允许 actions。必须原会话成员；URL 本身不赋权。
- POST `/api/orders/{id}/fund`：发送者原 sid，经真实 YIDT 页面批准的短期 intent；绑定 stamp/地址、精确 payload、金额、余额和 sid 在入金事务再次核实。不接受客户端 hash、余额或入金回调自证。
- POST `/api/orders/{id}/receive`：`{room}`，仅私聊指定 uid、当前已确认成员和订单原目标绑定钱包；点击才确认收款。
- POST `/api/orders/{id}/claim`：`{room}`，仅所属群当前已确认成员；点击即确认领取。

URL 为 `https://chat.yitiao.org/orders/{id}`。页面只读查看不会入金或领取，提供独立明确动作，不是聊天卡片 UI。付款批准页显示本金、付款钱包、收款钱包/群成员绑定要求、入金 gas 和另计出金费用。

## 本金与费用

路径始终 **发送者→官方托管钱包→接收者**。完整本金独立收费，不沿用其他旧产品的 all-in 自动削减本金规则。发送者支付本金+出金 gas 预留+入金 gas。托管中的本金和费用在既有 yidt_escrow 隔离，其他 YIDT 官方出金不能消费已托管余额。每笔接收者出金 gross=分配本金+gas，净到账恰为分配本金。

转账预留 1 笔出金 gas。红包预留 count 笔；部分领取到期后，退款包括未分配本金和未使用费用扣除一笔退款 gas，已分配待出金本金与费用保留。最多 count 笔领取，或较少领取加一笔退款，不重用已预留资金。

## 状态与并发

私聊：awaiting_deposit →（真实入金确认）awaiting_receipt →（指定收款人确认）paying →（账本出金提交）paid。入金确认后默认 86400 秒未收款进入 refunding → refunded；可通过 YICHAT_TRANSFER_TTL_SECONDS 配置。已确认出金不再与超时退款竞争。

群：awaiting_deposit → active → paying → paid；默认入金确认后 86400 秒过期，未分配余额进入 refunding → refunded，可由 YICHAT_PACKET_TTL_SECONDS 配置。金额≥0.01×count，每份≥0.01，1～1000 份。标题最多 100 码点。

安全随机分配用 secrets.randbelow，最小单位整数：剩余 R、份数 k、最低额 m；k=1 全取 R，否则均匀取 `[m,min(floor(2R/k),R-(k-1)m)]`。

成员变更由 Synapse 模块在接受 member event 前写 pending 屏障，与财务领取共享同一个权威账本事务锁；当前 join、无 pending 才能领取/赠送。实际接受后同步状态。重启只恢复本 writer 上一次启动未完成的屏障，不清除其他活 writer 的变更。拒绝后未持久化的异常成员操作保持 fail-closed，可由该 writer 安全重启后按 Synapse 当前状态恢复；不能超时猜测成功。

领取资格、随机数、原金额记录、余额/份数扣减、账号 uid/钱包唯一约束、outbox 同一事务提交；发送者账号/钱包均不能自领。并发最后一份只成功一次。预留后失败始终保留原金额。确认与退款使用同一写锁；退款只取未分配余额，不退待出金金额。

可靠后台任务每轮先对账。每个出金有固定服务端 reference，重试先查权威账本；未知或已记账但 outbox 不一致时停止并要求对账，不盲目再付。本系统账本和 outbox 在同一 SQLite 事务提交，进程中断可回滚/重试；这是当前本地账本能力，不能类推为外部链事务。

不变量：所有已分配领取本金 + 未分配本金 + 退款本金 = 实际入金本金；出金 gas/未用费用单独计算。最终领取+退款本金=入金本金。未领取的随机结果不会预生成或在详情泄露；best 只基于已分配结果。

YCard 赠送使用独立 API/幂等记录，复用真实 yc_instances 及其现有转让触发器。消息、预览和资产操作相互独立，不能从解密正文或一张消息卡片自动修改资产。

## 故障处理

```bash
sudo systemctl stop yichat.service  # 可先阻止新业务请求
sudo /root/yitiao/yichat/.venv/bin/python -m backend.manage reconcile
```

不得改余额让审计通过。完整保存 userdata.db、account.db、chat.db、密钥和配置；YIDT DB 回滚必须考虑其他业务的新交易，不能独立恢复财务旧副本。后台持久失败需要按原 reference 查链/对账后修复。无真实用户资产自动测试；所有测试账本是临时生成的隔离钱包。
