# YCards 仓库损毁与个人主页展示

## 使用

仓库“我的”中的卡片可逐张勾选，也可全选本页可损毁卡片，点击“损毁所选”并确认永久损毁。单张卡片详情也有“损毁卡片”按钮。换页或筛选会清空本页选择，避免误删未显示卡片。

正在挂售、正在拍卖、正在主页展示的卡片禁用损毁选择，并显示原因。请求提交时服务端再次校验；批量中只要有任何卡片不属于当前用户或已进入受保护状态，整个请求回滚。确认前可取消。损毁不可撤销、不退点数、不改变成熟度和理论生产数量。

每位用户最多同时拥有 10 个进行中的拍卖，公开与密封合计。创建前结算已到期拍卖，然后在创建及扣费的同一写事务内校验数量。第 11 个请求不会创建拍卖或扣发布费；重复请求不额外占位。部署前已存在的拍卖不会被强行取消；达到或超过限制时须等待数量下降后再创建。

在 `/account/changeprofile/` 自我介绍下方管理卡片展示：点击展示框“选择卡片”或“更换”，从当前用户仓库筛选、翻页选择；“取消展示”会清空该位置。展示设置即时保存，与昵称、介绍原有“确认”按钮分开。

默认免费 3 个展示框，每次可支付 10,000 point 永久购买 1 个，最多购买 3 个，总计 6 个。购买前有金额确认，余额不足不扣款也不增加位置；并发和重复请求不会误购多个。点数仍来自主站原账户，并记入原 `points_ledger`。

别人访问 `/@用户名`，在自我介绍下方看到卡片。页面宽度小于 1000px 时每行 3 个展示框，大屏每行 6 个。卡片复用客户端模板缓存和渲染器，支持原视差效果，但点击不打开详情、交易或其他页面，也不会授予编辑／复制／生产权限。

展示不禁止正常挂售或拍卖；一旦出售、拍卖成交导致所有权转移，同一数据库事务的触发器自动撤下原主人的展示。原主人保留已购买的展示框。guest 用户继续遵守 YCards 的原角色权限，不能操作仓库、损毁或购买展示框。

## 数据与接口

不物理删除 `yc_instances`，新增 `yc_destroyed` 保存永久损毁标记、操作者及时间。仓库和普通预览过滤损毁卡片；禁止重新挂售、拍卖和展示。历史成交和历史拍卖仍保留引用及预览，不破坏生产记录或正式模板。已损毁实例继续占用原全局 ID、CID 和生产编号。

新增 `yc_showcase(user,slot,instance,updated)` 和 `yc_showcase_purchases(user,slot,request_key,created)`。相同卡片不能重复展示，展示位置有 1～6 的数据库边界，购买位置有 4～6 的边界和幂等唯一约束。`yc_showcase_transfer` 在实例所有者变化时删除展示引用。三张新表接入 `/admin-panel/` 概览及数据查看。

- `POST /ycards/api/destroy`：`instance_ids`（1～500 个实例 ID）、`confirmed:true`；服务端整批校验、事务提交。相同损毁重试不重复写入。
- `GET /ycards/api/showcase`：自己的展示位置、卡片和余额。
- `POST /ycards/api/showcase`：`slot`、`instance_id`；实例 ID 为 `null` 时取消展示。
- `POST /ycards/api/showcase/purchase`：`expected_slots`、`request_key`；原位置数校验防止并发误购，10,000 point 扣款及扩容同一事务。
- 写接口复用现有登录、实时角色校验、JSON 校验和 `X-YCards-CSRF`。

## 修改文件

- 新增 `ycards_collection.py`：损毁、展示、购买、表和所有权转移触发器。
- `ycards.py`：混入仓库能力、损毁过滤、展示预览授权及 API。
- `ycards_auctions.py`：10 张上限、禁止损毁实例拍卖。
- `website.py`：原编辑主页和公开主页内嵌展示组件，提供会话／展示数据。
- `static/ycards.js`、`static/ycards.css`：仓库多选、单张／批量损毁及状态提示。
- `static/ycards-auctions.js`：10 张限制提示。
- 新增 `templates/ycards_showcase.html`、`static/ycards-showcase.js`、`static/ycards-showcase.css`：展示、仓库选择、购买和响应式布局。
- `admin_panel.py`、`templates/admin_panel.html`：新表中文显示。
- `tests/test_ycards_collection.py`、`tests/ycards_collection_fixture.py`、`tests/ycards_collection_check.mjs`、`tests/run_ycards_collection_checks.sh`：新验证。
- `tests/ycards_cache_fixture.py`：市场 32 件拍品分配给不同测试卖家，遵守新上限，保留原多页缓存测试。

## 验证与部署

71 项服务端测试通过，其中新增 14 项覆盖：损毁权限与确认、保护状态整批回滚、幂等、永久不可再交易、历史引用、展示权限、重复展示、位置上限、精确点数扣费、余额不足、重复／并发购买、成交自动撤展、拍卖上限与名额并发、损毁对挂售／展示并发、CSRF 和 guest 权限。

Chromium 流程通过：取消损毁、批量与单张损毁、三种受保护卡片禁选、购买三个展示框、展示六张卡片、取消展示、匿名公开预览、点击无跳转、原昵称／自我介绍保存。截图验证 1440px 六列与 390px 三列，无横向溢出、无浏览器脚本异常。原模板缓存／下载取消／分页和公开／密封拍卖浏览器回归通过。

所有账户、点数及损毁测试仅在临时数据库执行。主页浏览器 fixture 通过 AST 提取实际 `website.py` 的路由及模板，避免执行生产启动和触碰真实账户。

```sh
/opt/vps-python/bin/python -m unittest discover -s tests -p 'test_ycards*.py'
YCARDS_CHROME=/path/to/chrome bash tests/run_ycards_collection_checks.sh
```

服务启动自动新增表及触发器，不重建原用户、积分或卡片表。本次需求无已知未完成项。


最新的仓库多选、批量挂单、公开分享及成交买家可见性规则见 [YCARDS-SHARING.md](YCARDS-SHARING.md)。保护卡片可勾选用于公开，损毁操作仍禁止；密封拍卖成交后仅卖家可查看买家身份，报价表继续匿名。
