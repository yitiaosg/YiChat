# YCards 使用与实现说明

最新名称修改、模板归档／恢复和保存性能优化见 [YCARDS-LIBRARY.md](YCARDS-LIBRARY.md)。模板不再支持完全删除。

仓库损毁、主页展示及同时拍卖 10 张的限制见 [YCARDS-COLLECTION.md](YCARDS-COLLECTION.md)。

最新客户端缓存、分页和发布手续费规则见 [YCARDS-CACHE-FEES.md](YCARDS-CACHE-FEES.md)：成功发布普通挂售或拍卖，每单收取卖家 10 point。改价免费；下架、流拍不退发布费；买家成交手续费不变。

入口：`/ycards/`。首页导航和首页整屏栏目均位于 YiHub 前。

最新实体参数、双遮罩、效果与优惠规则见 [YCARDS-V2.md](YCARDS-V2.md)；下方 v1 格式作为历史兼容说明保留，冲突处以 v2 说明为准。

## 复用主站

- `website.py` 注册 `ycards.init_app`，使用 `_current_user()`、`_user_avatar_url()` 和原登录会话；头像／用户名点击进入原 `/account/`。
- 匿名用户可浏览市场及拍卖；市场卡片交易、出价或访问个人栏目跳转 `/login/?next=...`。
- guest 限制相同，跳转 `/account/?err=当前为guest用户，升级user等级以继续`，由原账户页显示提示。
- point 全部通过现有 `points.py` 操作 `userdata.db` 的 `points_accounts` 和 `points_ledger`。内部单位仍是 0.01 point。没有创建新的账户或积分体系。
- YCards 请求读取数据库中的实时 role，不依赖旧会话中的 role。

## 操作

1. **创作 → +**：创建空白草稿。选择材质、尺寸、边角和颜色，添加五类图层。选中图层后可拖动位置、滚轮缩放、数值调整尺寸及变换。列表箭头调整同深度叠放顺序。
2. 图片只接收 PNG；效果遮罩和流光重复图案可导入 PNG / SVG。文件选择、遮罩二值化、拖动、参数修改和预览均在浏览器本地完成。
3. **存草稿**上传当前设计，继续编辑；**保存**输入 TID 并确认“保存后不可再次编辑，是否继续？”，登记正式模板。保存不扣生产费。
4. 模板三个点菜单支持改名、创建编辑副本、去生产及归档。已归档模板可查看、改名、创建副本或恢复，生产列表中不显示。正式模板和草稿均不支持完全删除。查看正式模板进入可交互预览。
5. **生产 → +**只列当前用户正式模板。个人生产一次一张；工厂每版 16 张。计算报价后查看逐版成熟度、费用和时间，点击支付开始。创作的“去生产”自动选中模板。
6. **我的**显示实际成品，按获得时间或模板排序，按 TID、名称及损耗组合筛选。查看详情可挂售或全屏检视。挂售中的卡片进入历史交易管理。
7. **交易**显示实际售价。买家另付 `ceil(P × 0.005)` **整 point** 手续费；例如 P=200.01，手续费 2 point，总付 202.01，卖家收 200.01。
8. **历史交易**分为当前挂售、买入、卖出、已下架。当前挂售可改价、下架。
9. 全屏检视可拖动旋转，陀螺仪由用户点击授权。无传感器或拒绝权限时保留触摸操作。全屏 API 不可用时使用全屏覆盖层。
10. **拍卖**支持公开／密封竞价、第一／第二价格成交。在仓库详情点击“拍卖”创建，右上角“拍卖历史”管理查看。完整规则见 [YCARDS-AUCTIONS.md](YCARDS-AUCTIONS.md)。

## 稳定设计格式 v1

正式文件 `cards/[TID].card`，损耗文件 `cards/[TID]_[编号].card`，草稿 `cards/draft_[独立UUID].card`。正式 TID 的比较使用 SQLite BINARY，大小写敏感，最多 12 个 ASCII 字母或数字。

设计字段包含 `format_version, tid / draft_id, name, creator, material, orientation, width_mm, height_mm, corners, color, layers`。`cost` 是服务端重算的派生原始成本。大于 JavaScript 安全整数的成本以十进制字符串存储，允许保存全部 320 层设计。

每层包含唯一 `id`、`type`、`effect`、`side`、`order`、`position`、`distance_mm`、`canvas_width_mm`、`canvas_height_mm`、中心偏移 `x,y`、内容 `width,height`（均为 mm）、`rotation`（度）、`scope`、`image / mask` 及效果参数。图片以 data URI 嵌入 JSON，副本与原模板没有可变图片引用。

稳定效果名称，不使用不透明的数字编号：

| type | effect | 含义 |
|---|---|---|
| image | image | PNG 图像，保留原 alpha |
| foil | text / pattern | 英文重复文字、PNG / SVG 重复图案 |
| glow | pulse / star / circle / bar | 两条对向射线、四条射线、圆形、移动条形光晕 |
| filter | negative | 反转已经渲染在后方的颜色，保留 alpha |
| material | gold / silver / vinyl | 镀金、镀银、黑胶 |

`position` 为 `front / surface / behind`；`side` 为 `front / back`。每类上限独立为 64，玻璃两面共用。距离存绝对数，空间符号由 position 决定。画布宽高为实际卡片宽高加 `2 × distance_mm`。

非图片遮罩使用 alpha=0 与 alpha>0 二值规则。PNG 服务端再次规范化。SVG 仅接受基础几何标签和属性；拒绝脚本、样式、声明、实体、链接、动画及外部资源。显示 SVG 遮罩时也在本地二值化。每资源上限 4 MB，静态 PNG 最大 2048×2048；客户端可在本机缩小更大 PNG。单次保存资源总请求上限 32 MB。

## 圆角与 5×5 边角料模型

正常圆角半径 3.18 mm。整版 4×4 格，节点 `(gx, gy)` 为 0..4，共 25 个边角料位置。

横三刀、竖三刀各贯穿整版。有效裁线既分割材料，也移除经过节点的圆角边角料；漏裁不切断对应整条接缝，也不应用同次记录的裁歪偏移。漏一条线会产生四个双联成品，漏多条可形成更大的连体卡。

四次额外圆角操作只覆盖整版四角：`tl=(0,0)`、`tr=(4,0)`、`bl=(0,4)`、`br=(4,4)`，对应格子 1、4、13、16 的一个外角。漏掉其中一刀，只让这一张角落卡的对应角保留为直角。裁歪以有符号切入量改变该角半径 `max(0, 3.18 + offset)`，不影响其他格子。

内部节点由经过它的任意有效横刀或竖刀清除角料。漏裁接缝在整版两端的角料保留；与另一有效裁线的交点会留下去除角料后的凹口。轮廓和 0.76 mm 侧面都采用相同的几何路径，不画虚构的内部分割线。

新成品 `geometry.cut_model=2`，`offcuts` 记录覆盖格的各节点扇区及移除结果，`cells` 记录全部覆盖格，CID 使用最小格子号。已存在的实例不迁移身份或交易数据；客户端读取原损耗记录后按上述规则计算实际轮廓。直线裁歪仍改变 bounds，画面会按实际位置裁取相邻格的印刷。

## 生产、存储与事务

YCards 在 `userdata.db` 增加 `yc_templates, yc_drafts, yc_stats, yc_sequences, yc_jobs, yc_sheets, yc_defects, yc_instances, yc_listings, yc_trades`。动态所有权、积分、挂售、数量和成熟度均只在数据库中。

- 正式设计不可更新。未使用模板的删除是永久保留 TID 的墓碑；列表移除未引用模板，已有引用模板拒绝删除且不隐藏。保留原 `.card`，防止历史文件名再用造成覆盖。
- 数据库保存不可变 `.card` 的完整发布载荷作为恢复 outbox；数据库提交后以 fsync + 原子硬链接写入，不覆盖正式／损耗文件。定时恢复缺失文件。草稿文件可原子替换，使用 revision 防止多窗口互相覆盖。
- 扣费、分配每张／每版生产编号、保存每版损耗、费用及截止时间在一个 `BEGIN IMMEDIATE` 事务中。个人与工厂共用用户序列，损毁不回收编号。
- 同一用户最多一个活跃批次。工厂报价逐版加 3 成熟度，最高 64；报价 token 包含逐版明细，启动在锁内重算，过期报价拒绝。
- 每个批次接受幂等 request_key；重试不重复扣费或分配编号。
- 损耗在任务创建时由服务端 SystemRandom 抽取并永久持久化。关闭页面、刷新、进程重启都不会重抽。
- 每秒后台结算到期版；读取相关页面也触发恢复。每版入库、成熟度、理论产量和结算标记在同一事务中。SQLite 唯一约束防止并发重复入库，支持多个网站进程同时运行结算器。
- `(TID, sha256(规范损耗JSON))` 唯一约束去重。全部漏印、偏移、漏裁、裁歪、损毁参数都参与；生产者、时间、编号不参与。无损耗用 0，无 `_0.card`。
- 交易实例使用 UUID 主键及生产者／生产编号／版内编号唯一约束，展示 CID 不作为全局唯一键。转手不改 CID 或原生产编号。
- 买卖双方积分、买家手续费独立流水、卡片归属和挂售成交在同一个 SQLite 写事务。不足以支付手续费时整笔回滚。成交挂售唯一关联一条交易。
- 当前支付实现限制单批次至 100 版、改善投入至 100000 point（1000 整数倍）、单笔生产总额至 10^12 point。这些是请求／支付边界，不减少图层上限。设计即便超过支付上限也可保存。

## 渲染

2026-10-10 最新修正及验证见 [YCARDS-OPTICS.md](YCARDS-OPTICS.md)。

所有页面共用 `static/ycards-render.js`。Canvas 分层渲染空间视差，CSS 单独实现整体旋转；不会仅旋转一张静态图片。按空间深度再按图层顺序遮挡。视差角独立钳制 ±45°。

塑料的两个整体旋转分量钳制 ±90°，其面法向 z 分量为 `cos(rx)cos(ry) >= 0`，组合旋转也不会翻到背面。玻璃的图片层两面相通，翻面后按实际深度反向遮挡。卡面效果双面可见；远离观察者一侧的卡前效果不渲染。背面设计使用镜像后的局部坐标，透过玻璃的图片遵守物理镜像关系。陀螺仪模式取消整体 CSS 位移／倾斜，仍使用输入驱动层间视差和反光。

IntersectionObserver 控制可见卡片，离屏不更新；只在输入、尺寸或资源变化时重画，没有永久逐帧动画。市场／仓库资源按可见区域加载，24 条分页。复杂图层自动降低预览像素密度，保留所有图层。生产状态每 4 秒更新，页面隐藏时暂停查询。

## 文件与运行

- `website.py`：注册模块、首页入口。
- `ycards.py`：校验、数据模型、经济事务、生产和 API。
- `ycards_auctions.py`、`static/ycards-auctions.js`：持久化拍卖、冻结／结算、拍卖页面与历史。
- `templates/ycards.html`、`static/ycards.css`、`static/ycards.js`：页面及客户端编辑器。
- `static/ycards-render.js`：统一渲染与旋转输入。
- `static/home-ycards.svg`：首页矢量背景。
- `tests/test_ycards.py`：隔离数据库回归测试。
- `cards/`：运行时 JSON `.card`。

由现有 website 服务运行，不需要另起 YCards 服务或安装新依赖。应用启动自动创建新表和恢复任务。部署前保留 `website.py.pre-ycards`。

测试命令：

```sh
cd /root/yitiao
/opt/vps-python/bin/python -m unittest discover -s tests -p 'test_ycards*.py' -v
node --check static/ycards.js
node --check static/ycards-render.js
```

测试数据使用 TemporaryDirectory，不修改真实账户／积分。浏览器集成测试用独立 Flask fixture 和临时 userdata.db，验证本地编辑无上传、正式保存、生产、挂售、跨用户购买、历史交易及全屏覆盖模式。真实手机传感器授权与硬件响应仍需在设备上体验验证；无传感器回退路径已实现。

## 本次验证结果（2026-10-10）

- 21 项 Python 回归测试通过：精确成本／手续费、320 层保存、PNG alpha、SVG 安全、权限、草稿版本、TID 唯一性、原子购买、并发购买与结算、报价失效、成熟度上限、理论数量、生产恢复、损耗去重、改善概率、裁切连接与实例 ID。
- 12 项真实 Chromium 渲染断言通过：玻璃透明度、深度遮挡、图层视差、超过 45° 不继续增加视差、只渲染朝向面、塑料不翻背、陀螺仪固定位置、二值遮罩、连接轮廓、漏圆角裁切、同时漏印／歪印、无损工厂成品与个人成品渲染一致。重复负片对采用等价消除，64 个同范围负片在该测试环境的渲染约 2 ms；这一结果不代表所有设备和所有复杂设计。
- 浏览器完整流程通过：创建草稿、纯本地调整无上传、存草稿、保存正式模板、支付生产、成品入库、挂售、另一用户购买、历史交易、全屏覆盖检视。触摸位置驱动倾斜已通过 Chromium 原生触摸事件检查。
- 桌面和 390px 手机布局检查通过，手机文档宽度为 390px，无横向溢出。浏览器 JavaScript 异常列表为空。
- 现有 `website.service` 已重新加载；公网 `https://yitiao.org/ycards/` HTTP 200。首页栏目位置及匿名访问控制已检查。
- 所有测试交易、账户和积分操作只写入临时数据库；正式市场没有写入样例卡片或测试交易。
- 待实机验证：iOS / Android 传感器权限提示、真实陀螺仪数据和重图层下的设备性能。拍卖已按后续需求实现，验证结果见拍卖说明。

浏览器测试在 `tests/ycards_browser_fixture.py`、`tests/ycards_browser_check.mjs`、`tests/ycards_render_check.mjs`。后两者通过 Node 的原生 WebSocket 调用 Chromium DevTools，不需要 Playwright 依赖。指定可用浏览器后运行：

```sh
YCARDS_CHROME=/path/to/chrome-headless-shell bash tests/run_ycards_browser_checks.sh
```

默认测试端口为本机 5079（fixture）和 9225（浏览器调试），测试结束会关闭其创建的进程。可设置 `YCARDS_PYTHON` 和 `YCARDS_ARTIFACTS`。浏览器仍需其系统动态库。`tests/ycards-artifacts/` 保存桌面／手机截图、浏览器结果和渲染结果。


最新的仓库多选、批量挂单、公开分享及成交买家可见性规则见 [YCARDS-SHARING.md](YCARDS-SHARING.md)。保护卡片可勾选用于公开，损毁操作仍禁止；密封拍卖成交后仅卖家可查看买家身份，报价表继续匿名。
