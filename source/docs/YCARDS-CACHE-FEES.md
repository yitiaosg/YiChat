# YCards 客户端缓存、分页与发布费

## 使用与行为

交易、拍卖和仓库每页显示 24 张卡片，页底支持输入整数页码跳转。超出实际页数的服务端请求归到最后一页。筛选、重置和换页时取消上一轮列表及模板下载，新结果优先加载；过时响应不会覆盖当前结果。

三个页面、详情及拍卖历史共用不可变正式模板缓存。列表只传实例信息、损耗和轮廓，不反复传图片及完整设计；浏览器将这些信息与缓存模板组合，由原客户端渲染器计算各实例的效果、视差和轮廓。所有权、价格、余额、竞价、成交和生产仍由服务端权威校验。

模板以当前登录身份与区分大小写的 TID 为键；同模板并发请求合并。缓存包含内存 LRU（约 32 MiB）及 IndexedDB（约 128 MiB、最多 80 个模板），持久缓存可跨刷新复用。为支持大型合法设计，两层均至少保留最新一个模板。浏览器存储不可用时回退内存；主动清理或缓存淘汰后会重新下载。缓存不授予编辑、复制或生产他人模板的权限，渲染数据不包含模板成本。

每页主动预取全部不同模板，最多同时下载 3 个；卡片预览提前 700px 初始化，离屏高频动画继续由原渲染器暂停。拍卖详情定时刷新不再携带卡片设计。

## 发布手续费

- 成功创建普通挂售或拍卖，卖家固定支付 10 point（原账户内部 1000 cents）。
- 前端发布按钮／表单和确认框说明费用。余额不足、权限／参数不合法、实例已挂售／拍卖时，不创建挂单也不收取发布费。
- 扣费、积分流水和创建挂单在同一个 SQLite 事务中完成，失败完整回滚。
- 挂售请求编号保存在新增 `yc_listing_requests` 表；拍卖复用原请求编号。相同请求重试或并发提交只扣一次；同一编号不能换参数复用。旧客户端未带挂售编号时仍由活跃挂售唯一性防止重复创建及扣费。
- 修改售价免费。主动下架、到期流拍不退发布费；之后重新发布新单再收 10 point。
- 买家仍另付 `ceil(P × 0.005)` 整 point；卖家成交时收到完整成交价 P。
- 不追收更新前已存在挂单的发布费。
- 费用记录复用 `points_ledger`，关联挂售／拍卖 ID。新增请求表已接入 `/admin-panel/` 数据查看和 YCards 概览。

## 接口与文件

`market`、`mine`、`auctions`、`preview` 支持 `compact=1`，旧默认响应继续兼容。新 `GET /ycards/api/render-template/<TID>?instance_id=<实例ID>` 必须通过可查看实例校验；历史拍卖使用附加 `auction_id`。接口 HTTP 缓存禁用，由前端显式管理不可变设计缓存。拍卖详情 `card=0` 可仅刷新报价与时间。

修改：`ycards.py`、`ycards_auctions.py`、`admin_panel.py`、`static/ycards.js`、`static/ycards-auctions.js`、`static/ycards.css`、`templates/ycards.html`、`templates/admin_panel.html`，新增 `static/ycards-cache.js`。文档更新 `YCARDS.md`、`YCARDS-AUCTIONS.md`。

## 验证

57 项服务端单元／事务测试通过，其中新增 8 项覆盖发布费、余额不足回滚、幂等／并发、重新挂售、流拍、模板权限、精简响应和分页边界。全部测试使用临时数据库，没有真实用户扣费。

Chromium 缓存专项验证：同模板在交易换页、仓库、拍卖、详情、轮询及刷新全过程只下载 1 次；旧模板请求确实被取消；慢筛选不覆盖新结果；交易和拍卖数字跳页正常；390px 手机布局无横向溢出，浏览器异常为空。原创作／生产／挂售／购买／检视流程与公开／密封拍卖流程另做浏览器回归。

新增验证文件：`tests/test_ycards_cache_fees.py`、`tests/ycards_cache_fixture.py`、`tests/ycards_cache_check.mjs`、`tests/run_ycards_cache_checks.sh`。浏览器测试 fixture 不在生产 Flask 中注册。现有浏览器脚本补充等待浏览器退出再删除临时目录，避免退出清理竞争。

```sh
/opt/vps-python/bin/python -m unittest discover -s tests -p 'test_ycards*.py'
YCARDS_CHROME=/path/to/chrome bash tests/run_ycards_cache_checks.sh
```

本次需求无已知未完成项。移动设备的实际显卡性能与存储配额仍受浏览器环境影响。
