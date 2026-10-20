# 实际验证记录（2026-10-19）

最终代码验证：YiChat 后端 **81 passed**，主站/原业务回归 **75 tests OK**，账户 Chromium 浏览器 **11 checks passed / 0 page errors**。后端套件内另外运行了真实浏览器归档下载与 WebCrypto 本地验证，不上传文件。

环境：Debian 13，Python 3.11，未修改核心的 Synapse 1.163.0，真实 PostgreSQL 17.11，Chromium/Playwright 1.58.2。PostgreSQL 测试使用独立 `yichat_archive_test` 和 yc_test_* 角色；SQLite、用户、钱包、消息及财务操作均为临时隔离数据。没有用真实用户资产试跑。

## 覆盖与证据

| 检查 | 实际结果 |
|---|---|
| 主站账户/OIDC/退出 | 5 项；旧 YIDT sub 保持，YiChat sub 用不可变 uid，主站退出撤销关联 sid/token |
| 原 YIDT、账户 PIN、Passkey | 45+8+7+10 项；沿用原真实实现和隔离账本 |
| 原生 Matrix 绕过 | 独立启动真实 Synapse，多次重启；直接用 raw access token 请求被模块拒绝，私聊拉黑/后续消息/反应、群加密状态均验证 |
| 私聊/群聊 | 私聊明文拒绝，原始不透明密文被实际接受并恢复采集；新群无 encryption 状态，2000 emoji 接受、2001 拒绝。未声称测试了未来 SDK 的真实解密客户端 |
| 登出设备与密钥 | 撤销 raw access token，保留 device；不调用删除设备接口 |
| 多设备/进程限流 | 同 uid 多令牌、Matrix/业务接口共享窗口；4 个 spawn 进程共30请求仅10成功，429/Retry-After/retry_after_ms |
| 流式上传 | 实际100000001字节、无 Content-Length，业务/原生入口均413；真实字节吞吐与并发租约，原生媒体仓库关闭 |
| 附件关联 | actual receipt SHA；跨日按实际接受事件日归档；草稿仅本人可取；接受引用后授权成员可取；早期日清理保留后续引用实体 |
| 确定性归档 | 多线程12次读取字节一致；从源数据独立重建字节一致；两个规格摘要不同；ZIP/UTF-8/LF/固定元数据和密文保留 |
| UTC/编辑/撤回/恢复 | 接收日边界、去重、午夜未完成 fence、未用流号恢复、空日期、晚到封存日隔离；rename/摘要提交前以及删除后注入崩溃可恢复 |
| 历史权限 | 中途加入、退出再加入、陌生人不能补取完整日；源 allocation fence/历史水位不足503；下载、摘要和附件均鉴权 |
| 存证/清理 | PG 最小权限及不可变触发器实际拒绝更新；缺附件/失败/心跳过期/摘要不符停止清理；真实 Synapse admin purge 提交并完成；清理后摘要仍可查 |
| 财务 | 重复幂等键、独立费用、抢最后一份、账号/钱包唯一、钱包变更、成员 pending、出金未知/失败、超时与退款竞争、本金守恒；全部临时真实原账本事务 |
| 资产/预览 | YCard 所有权/挂售/销毁/拍卖/并发幂等与真实触发器；域名/路由白名单、私有订单和无外部 HTTP/HTML |
| 账户页面 | 实际 Chromium 点击关注/隐私/黑名单/消息提示/二维码；390px无横向溢出；0脚本异常 |

## 可复现命令

从源码根目录运行。测试数据库初始化需 sudo 调用 PostgreSQL 管理命令；凭据仅0600本地保存，不提交。

```bash
yichat/.venv/bin/python yichat/deploy/provision_archive.py --test
yichat/.venv/bin/python -m pytest -q yichat/tests
/opt/vps-python/bin/python -m unittest discover -s tests -p test_social_pages.py -q
/opt/vps-python/bin/python -m unittest discover -s tests -p test_yidt.py -q
/opt/vps-python/bin/python -m unittest discover -s tests -p test_yidt_account.py -q
/opt/vps-python/bin/python -m unittest discover -s tests -p test_passkey_flows.py -q
/opt/vps-python/bin/python -m unittest discover -s tests -p test_passkeys.py -q
```

原主站测试 fixture 在导入时修改临时模块全局路径，因此每个测试文件用独立 unittest 进程运行；不要将这些文件混在同一 pytest 收集进程。后端必须使用装有 Synapse 和 pytest-asyncio 的 YiChat venv；通用 `/opt/vps-python` 没有这两项。最终后端81项无跳过，仅有现有 Starlette TestClient/httpx 弃用警告。

账户浏览器：在测试专用目录 `yichat/tests/browser` 执行 `npm ci`，用主站 Python 启动 `main_fixture.py`（127.0.0.1:5091），再 `node account.mjs`。fixture 只创建临时数据，不能部署为生产路由。截图和 JSON 输出在该目录 artifacts，Git 忽略。归档浏览器由后端 pytest 自行启动临时 Uvicorn。

`docs/samples` 是显式 synthetic 格式向量，包括期望摘要和两个ZIP；其中的密文是占位测试字符串，不是可恢复的真实私聊。生产健康/迁移记录另见 DEPLOYMENT-RECORD.md，不把隔离测试冒充生产账号登录或真实资产结算。
