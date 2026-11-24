# 案例：PR #106 —— CI 偶发 `CancelledError`，从「复现不了」到公开更正

- **日期**：2026-11-24
- **公开记录**：[PR #106](https://github.com/meditecnic/AMADEUS/pull/106)（`[310]` 后台续跑与队列）｜作者 `Codex GPT-6.2 Sol`｜修复 [`b3a3ce9`](https://github.com/meditecnic/AMADEUS/commit/b3a3ce9c4ce2026c27a9b2cdcad64d892e55ed3f)｜合并 [`715b9a3`](https://github.com/meditecnic/AMADEUS/commit/715b9a3fa0bca16f3116645db6e61b9a3ce9e50f)

## 现象

PR #106 过完三方审查和返工，在 `7f6b56e` 的 [首次 CI 尝试](https://github.com/meditecnic/AMADEUS/actions/runs/45324049714/attempts/1) 上，`backend-py313` 挂了 1 项：`test_chat_deprecated_message_rejected` 在退出 `websocket_connect` 时抛出 `concurrent.futures.CancelledError`。

总调度第一时间在 [PR 评论 1](https://github.com/meditecnic/AMADEUS/pull/106#issuecomment-7121955694) 写下初判：Linux 上 py3.11 跑 5 次、py3.13 跑 10 次全绿，作者 Windows py3.11 全量也绿，看起来像「只在 Windows py3.13 出现的偶发时序问题」。半小时后合入新 `main` 到 `6760e3a`，[下一次 CI 的 py3.13 job](https://github.com/meditecnic/AMADEUS/actions/runs/45327590026/job/138220791269) 同错误暴涨到 **7 项**（1 failed、6 errors），而期间后端只改了一行日志。`backend-py313` 设了 `continue-on-error`，红了本来就不挡合并，但我们照样当真追到底，没有放过去。

## 怎么查

既然空载跑太快撞不上窗口，就刻意抢占 CPU。总调度在 Linux 用 `taskset -c 0` 绑单核、再开两个空转进程占满 CPU，连跑 `test_chat_message_length.py` 和 `test_e2e_contract.py`，稳定复现后在 [PR 评论 2](https://github.com/meditecnic/AMADEUS/pull/106#issuecomment-7122486706) 公开更正：

| 被测代码 | Python 3.13 | Python 3.11 |
|---|---|---|
| PR #106（`6760e3a`） | 6 次 5 错（每次 3–9 项） | 4 次全错 |
| `main` 基线（`ad447e5`） | 6 次 0 错 | — |
| PR #106 删去 `finally` 的 `await turn_queue.disconnect` | 6 次 0 错 | — |

实锤是本 PR 引入的回归，py3.11 一样会挂，不靠重跑 CI 放行，打回返工。

## 根因

`Starlette` 测试客户端退出 `websocket_connect` 时，发出断开紧接着取消服务端 `anyio` cancel scope。本 PR 在 `chat_ws.py` 的 `finally` 里新加了 `await turn_queue.disconnect(connection_id)`；CPU 紧张时取消正好打在这个挂起点上，既漏出 `CancelledError`，还可能跳过后面的子任务收尾和 `session_coordinator.release_lease(...)`。

## 怎么改

作者 `Codex GPT-6.2 Sol` 提交 [`b3a3ce9`](https://github.com/meditecnic/AMADEUS/commit/b3a3ce9c4ce2026c27a9b2cdcad64d892e55ed3f)（3 文件，+72/−16）：

1. 把连接收尾放进独立任务 `cleanup`，先 `asyncio.shield`，再在 `finally` 里用 `anyio.CancelScope(shield=True)` 等收尾做完才传播取消；
2. 改用 `asyncio.wait`，把 `is_busy = False` 与 `release_lease` 放进内层 `finally`；
3. `disconnect()` 无归属轮次时直接返回；新增不靠压 CPU 即可稳定触发的单测 `test_scope_cancellation_during_writer_shutdown_releases_chat_lease`。

## 怎么验

- **前后对照**（私有仓记录）：同单核 + 双空转负载，改后 `b3a3ce9` py3.13 **10 次 0 错**、py3.11 **6 次 0 错**；对照组 `6760e3a` 3 次 2 错；
- **确定性单测**：改前稳定报 `CancelledError`，改后通过；
- **Windows CI**：`b3a3ce9` [PR CI](https://github.com/meditecnic/AMADEUS/actions/runs/45337593157) 与合入后 [`715b9a3` main CI](https://github.com/meditecnic/AMADEUS/actions/runs/45341281793) 均 6/6 绿。

## 代价

多一轮返工与约两小时等待，挡下间歇红灯进 `main`，也把「初判误判 → 公开更正 → 根治」完整留在 PR 里。
