# YCards 批量挂单、公开与分享

## 使用方式

仓库卡片均可勾选，也可全选当前页。选中后，底部浮出损毁、批量挂单、公开/取消公开和取消选择按钮；清空选择后操作栏隐藏，翻页或筛选会清空选择。

- **批量挂单**：填写一个统一的每张售价。发布费每张 10 point，窗口展示总发布费。整批成功才扣费和发布；余额不足、任一实例已挂售/拍卖、损毁或不属于当前用户时整批回滚。重复提交同一请求不重复扣费。
- **损毁**：仍需二次确认，挂售、拍卖和主页展示品不可损毁。保护卡片现在也能选中用于公开；选择中包含保护卡片时，损毁按钮禁用。公开本身不阻止损毁，损毁同时关闭公开。
- **公开**：批量设为公开；全部所选卡片已公开时，按钮变为“取消公开”。卡片详情也能单独切换。主页展示与链接公开是两项独立设置。
- **分享**：在仓库、交易或拍卖的具体卡片中点击“分享”，自动将卡片公开。拥有者可分享自己的卡片；已登录且非 guest 的市场查看者可分享当前仍挂售/竞拍的卡片。历史拍品已转手时，旧持有者不能再将其公开。
- 分享弹窗可复制/原生分享 URL，也可分享或下载 PNG 海报。海报在客户端生成，包含实际成品的正面图、名称、CID、二维码；原生文件分享不可用时下载图片。使用既有本地二维码组件，不上传海报或图片资源。

公开 URL 为 `/ycards/card/<全局实例ID>/<CID>/`。加入实例 ID 避免不同生产者产生相同展示 CID 时冲突；二维码指向这个 URL。匿名用户可打开只读互动预览。取消公开、损毁、成交或拍卖转手后，链接返回不可用提示。公开访问不会授予模板编辑、复制或生产权限。

交易列表及卡片详情显示卖家头像、昵称、用户名，点击进入 `/@用户名` 主页，复用现有用户资料与头像。卖家在“历史交易 → 卖出记录”看到买家资料。拍卖成交后卖家也可看到买家；密封拍卖进行时及其报价表仍不暴露竞拍者身份，密封成交买家仅向卖家披露（购买者可查看自己的成交）。

## 数据与可靠性

继续使用 `userdata.db`、现有 point 账户及流水。

- `yc_public_cards`：实例当前公开状态和对应所有者。
- `yc_batch_list_requests`：批量挂单请求内容及返回结果，用于幂等重试。
- 所有批量经济操作在 `BEGIN IMMEDIATE` 中完成；每张的 10 point 发布费单独保留流水。
- 实例所有者改变时，SQLite 触发器在成交的同一事务中删除公开状态；损毁也由触发器撤销公开。
- 分享请求校验查看时的所有者和当前挂售/拍卖状态，防止旧页面在转手后重新公开卡片。
- 公开页面以同一读事务校验公开权限并读取卡片；每次访问重新校验，使用 `Cache-Control: private, no-store`。不可变设计资源继续复用原客户端模板缓存。
- 新表已加入管理员 SQLite 表名适配。

接口新增 `POST /ycards/api/list-batch`、`POST /ycards/api/public`、`POST /ycards/api/share`、`GET /ycards/api/public/<id>/<cid>`；写接口继续校验登录、role 和 CSRF。

## 修改文件

- `ycards_collection.py`：公开数据、触发器、批量挂单和分享权限。
- `ycards.py`：公开页面/API、只读快照、卡片公开字段、用户资料关联。
- `ycards_auctions.py`：成交卖家的买家可见性，保留密封报价匿名。
- `website.py`：向 YCards 提供既有昵称资料查询。
- `admin_panel.py`：新表中文说明。
- `static/ycards.js`、`static/ycards-auctions.js`、`static/ycards.css`：多选浮动操作栏、批量操作、分享入口、卖家和买家资料。
- `static/ycards-share.js`、`static/ycards-public.js`：本地海报/二维码、链接分享与公开预览。
- `static/ycards-render.js`：暴露资源就绪 Promise，保证导出前已加载图片。
- `templates/ycards.html`、`templates/ycards_public.html`：脚本接入与公开页面。
- 新增 `tests/test_ycards_sharing.py`、`tests/ycards_sharing_fixture.py`、`tests/ycards_sharing_check.mjs`、`tests/run_ycards_sharing_checks.sh`；更新密封拍卖及仓库选择的原测试预期。

## 验证

90 项服务端测试通过，覆盖整批费用/余额回滚、相交批次并发、幂等重试、非法实例、保护状态、公开权限、相同 CID 不同实例、分享/成交竞争、自动撤销、密封买家隐私、头像昵称与 CSRF。

浏览器分享、仓库/主页和拍卖回归通过：批量公开与同价挂单、每张发布费、三处分享、PNG 生成、匿名公开预览、成交后失效、卖家主页链接、卖家买家记录、390px 浮动栏和弹窗无溢出。OpenCV 从实际生成的 PNG 解码二维码，得到的 URL 与该卡公开地址一致。测试全部使用临时数据库。

```sh
/opt/vps-python/bin/python -m unittest discover -s tests -p 'test_ycards*.py'
YCARDS_CHROME=/path/to/chromium bash tests/run_ycards_sharing_checks.sh
YCARDS_CHROME=/path/to/chromium bash tests/run_ycards_collection_checks.sh
YCARDS_CHROME=/path/to/chromium bash tests/run_ycards_auction_checks.sh
```

实际系统分享面板取决于浏览器支持；已实现复制和下载回退。没有未完成的功能项。
