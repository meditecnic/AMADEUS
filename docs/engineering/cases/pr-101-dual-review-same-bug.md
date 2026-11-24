# 案例：PR #101 —— CI 全绿后，两家异厂审查盲审撞出同一个 bug

- **日期**：2026-11-22
- **公开记录**：[PR #101](https://github.com/meditecnic/AMADEUS/pull/101)（`[276]` 会话截断与准入锁）｜作者 `Codex GPT-6.2 Sol`｜返工 [`60ae9fa`](https://github.com/meditecnic/AMADEUS/commit/60ae9fa62bd461041ef43e4e9ffdd6793e14db9b)｜合并 [`a00f177`](https://github.com/meditecnic/AMADEUS/commit/a00f177a7a93725cdddd49fd46ea15bb15b69f27)

## 现象

PR #101 实现对话截断、长期记忆重算与会话准入锁。头提交 [`875c9aa`](https://github.com/meditecnic/AMADEUS/commit/875c9aa3331bf413cb0c4a1411927416d4174cee) 的 Windows [CI run](https://github.com/meditecnic/AMADEUS/actions/runs/45030051460) 已经 6/6 全绿。按高风险包规矩，总调度派了两位不同厂商审查人做独立盲审（完稿前互不看报告，记录在私有协作仓 `[283]`）：

- 审查人 A（Google）：`Gemini 4 Neon`
- 审查人 B（Anthropic）：`Claude Opus 5.5`

两份报告交回，**两家各自独立判了 `REWORK`，而且打中同一个静默丢回复 + 前端卡死的回归**。

## 怎么查

单测全绿，两位审查人顺着 `chat_ws.py` 回复落库校验 `expected_epoch=content_epoch` 往下追，各自写了临时 WebSocket 探针：

- **复现场景**：v2 流式对话进行到一半，调 `DELETE /api/memory/facts/{id}` 删一条记忆事实。
- **审查人 B 对照**：候选 `875c9aa` 上代次 0→1，收不到 `turn.completed`，库里只剩两条 `user` 消息、回复丢失；基线 `918cbbf` 同探针正常落库，确认新回归。
- **审查人 A 追深一层**：同步测出 `SegmentPipeline._heartbeat_task` 未关而持续发 `turn.heartbeat`，不断重置前端 30 秒看门狗，界面永久卡在「正在回复」。

两家还同时指出 `GET /api/conversations/{id}/messages` 没返回消息 `id`，下游 PR #104 拿不到截断起点。

## 根因

1. **状态代次混用**：`conversation_content_epochs` 不仅被截断/擦除推进，删单条记忆走 `clear_working_summaries` → `invalidate_memory_summaries` 也会把该身份下所有会话的代次 +1。删记忆既不拿准入锁也不取消在途回复，结果把正常流完的回复错判成过期写并丢弃。
2. **被拒后裸 `return`**：`save_message` 返回 `None` 后 `chat_ws.py` 直接 `return`，跳过 `status: done`、`segment_pipeline.complete(...)`、心跳停机和记忆抽取。

## 怎么改

作者 `Codex GPT-6.2 Sol` 提交返工 [`60ae9fa`](https://github.com/meditecnic/AMADEUS/commit/60ae9fa62bd461041ef43e4e9ffdd6793e14db9b)（`[285]`，6 文件，+201/−46）：

1. 把回复落库与自动标题的校验从「删记忆清摘要」解耦，改核真实擦除/截断游标；
2. 确需拒写时不再裸 `return`，保证管线收尾与终态帧下发；
3. 采纳两家共同建议：`get_session_messages` 补返回 `id` 列，并捕获截断后的标题排程异常，防止已成功的截断返回 500；
4. 在 `test_conversation_truncate.py` 补齐「回复流中途删记忆仍落库并收 `turn.completed`」回归测试。

## 怎么验

- 返工 diff 由总调度（Anthropic）逐行复核（记录在私有协作仓）；
- `60ae9fa` 的 Windows [CI run](https://github.com/meditecnic/AMADEUS/actions/runs/45036873519) 6/6 全绿；
- 经我批合，merge commit [`a00f177`](https://github.com/meditecnic/AMADEUS/commit/a00f177a7a93725cdddd49fd46ea15bb15b69f27) 进 `main`。

## 代价

多付两份异厂审查和一轮 155 行净增返工；挡下了一个单测和 CI 均绿、但用户边聊边删记忆就会吞回复并卡死界面的严重回归。
