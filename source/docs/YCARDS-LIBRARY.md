# YCards 名称、归档与保存优化

2026-10-10。本说明覆盖旧文档中模板删除、名称不可修改及完整保存响应的规则。

## 使用

- 创作列表的三个点菜单：修改名称、创建编辑副本、去生产、归档。名称限 1～20 个字符，允许重复。
- “已归档”页查看、改名、创建副本或恢复。正式模板和草稿均不再提供完全删除；旧删除 API 也拒绝删除。历史上软删除的正式模板可从已归档页恢复。
- 生产选择框只显示自己未归档的正式模板。归档后不能提交新的生产，已支付任务继续完成，已有卡片不受影响。
- 仓库卡片详情可修改名称；自己挂售或拍卖中的卡片也可改名。其他用户和已损毁卡片无此权限。
- 模板与每个实例名称独立。改模板名不会修改已有卡片的名称；新产出使用结算时的模板名称。卡片转让保留名称，新所有者可继续改名。名称同步用于交易、拍卖、筛选、历史、公开页和分享图片。
- TID、CID、实例 ID、设计、成本、成熟度与产量均不因改名变化。正式 `.card` 保留创建时设计；当前展示名称由数据库管理。
- 生产优惠码输入框只提示“键入优惠码”，不展示管理员测试口令。优惠算法及权限保持原规则。

## 数据与接口

- `ycards_library.py`：名称权限、归档、保存状态和可重复执行的数据迁移。
- `yc_card_names` 以实例 ID 关联当前名称，生产结算在同一事务中写入，既有卡片启动时补齐。
- `yc_templates.name` 是正式模板当前名称，`deleted` 兼容列作为归档标志。新增 `material` 摘要列。
- `yc_drafts` 新增 `name/material/cost/archived` 摘要与状态列。草稿改名会递增 revision，阻止另一个窗口旧版本覆盖。
- `yc_save_requests` 记录用户、请求编号、设计摘要和小回执。并发或断线重试同一请求只保存一次；相同编号不同内容拒绝。保存状态仅本人可查询。
- `POST /ycards/api/rename`：`name` 和 `tid / draft_id / instance_id` 三选一。
- `POST /ycards/api/archive`：`archived` 布尔值及 `tid / draft_id` 二选一。
- `GET /ycards/api/templates?summary=1&production=1`：只返回未归档正式模板摘要，无设计资源。
- `POST /ycards/api/templates?compact=1` 与 `/drafts?compact=1`：只回 TID 或草稿 ID／版本。接受 gzip JSON，请求解压后仍受 32 MB 上限和完整资源校验约束。
- `GET /ycards/api/save-status/<request_key>`：本人请求的保存结果。
- 管理员看板增加卡片名称表和保存请求记录，已有模板／草稿表自动显示新增列。

## 保存等待排查与修复

只读抽样现有最大设计，在临时数据库中对原实现计时：资源校验约 2 秒，保存约 2.1 秒，响应却再次携带约 8.24 MB 图片。旧前端还在 UI 线程重复序列化整份设计，保存后重新进入列表会再次下载图片，离开创作列表也不会取消已有下载。服务器 CPU 和内存正常不能排除客户端渲染或网络等待；未能据此断言用户数分钟延迟的唯一原因。

本次修复：

1. 用 Web Worker 序列化和压缩上传，不在 UI 线程执行大 JSON 打包；不支持 CompressionStream 时仍可在 Worker 打包后原样上传。
2. XHR 显示上传百分比、校验阶段和耗时。保存期间暂停编辑器绘制和编辑，避免重复点击。只在主动保存时提交。
3. 保存成功仅返回小回执；列表复用刚保存的本地设计，不下载同一图片。离开创作列表取消旧请求。
4. 摘要列表直接读取小字段，不再解析所有设计 JSON。相同资源在一次校验内复用结果；保存只发布当前模板／草稿文件，后台仍保留全量恢复机制。
5. 连接中断或超时后查询已提交结果；可使用相同请求编号安全重试。正式保存响应带 `Server-Timing`，便于区分服务器耗时与传输等待。

## 验证

- 99 项隔离服务端测试通过，覆盖改名权限及交易后权限转移、名称独立、归档恢复与在途任务、旧报价拒绝、草稿版本、不可删除、并发保存幂等、gzip 大小上限、资源去重、只发布当前文件和摘要不解析设计。
- 既有编辑器、效果渲染与分享浏览器回归通过，包括分享二维码解码校验。
- 新增 Chromium 桌面／390 px 移动端检查：改名、归档恢复、生产入口、优惠码提示及大模板保存。约 9.67 MB 设计在 1 MB/s 上传限速下约 9.2 秒完成，响应 21 字节，无重复下载刚保存设计，无 JS 异常，移动端无横向溢出。此为本机模拟结果，真实耗时受上行带宽、图片与浏览器影响。
- 测试命令：`/opt/vps-python/bin/python -m unittest discover -s tests -q`；`tests/run_ycards_library_checks.sh`（需设置 `YCARDS_CHROME`，可设置 `LD_LIBRARY_PATH`）。测试使用临时账户、积分和数据库，不写入实际用户数据。

主要修改：`ycards.py`、`ycards_library.py`、`ycards_auctions.py`、`admin_panel.py`、`templates/ycards.html`、`static/ycards.js`、`static/ycards-auctions.js`、`static/ycards-render.js`、`static/ycards-save.js`、`static/ycards-save-worker.js`、`static/ycards.css` 及对应测试。本次不需修改 `website.py`。
