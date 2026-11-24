# 案例：PR #104 —— 单测和 CI 全绿后，Windows 真机实跑抓出 409 竞态

- **日期**：2026-11-23
- **公开记录**：[PR #104](https://github.com/meditecnic/AMADEUS/pull/104)（`[288]` 消息菜单与原声重播）｜作者 `Gemini 4 Neon`（`[303]` 返工由 `Codex GPT-6.2 Sol` 完成）｜修复 [`855e74f`](https://github.com/meditecnic/AMADEUS/commit/855e74f3ce0108af288547f894133516189f1d06)｜合并 [`ad447e5`](https://github.com/meditecnic/AMADEUS/commit/ad447e5807408c3835fa5f604fe62f60ea859cd9)

## 现象

PR #104 新增消息菜单（重播、复制、回退、改句重发）和原声 WAV 归档缓存。到 [`c9cbf95`](https://github.com/meditecnic/AMADEUS/commit/c9cbf95f6cb6dc4d01000ef2195d8534dc879acc) 已过两家异厂审查（OpenAI + Anthropic）、返工和打磨，前端 1,030 条 Vitest、后端单测及 Windows [CI run](https://github.com/meditecnic/AMADEUS/actions/runs/45197686782) 均 6/6 全绿。

因涉及真实浏览器播音时序与 NTFS 独占锁 / junction，合前在 **Windows 11 真机**跑了 9 项端到端验收（`Codex GPT-6.2 Sol`，Edge + Playwright + 回环假服务，私有协作仓 `[311]`）。NTFS 独占锁（`FileShare.None` 触发错误 32 后延至启动清理）、junction 与 10 轮归档压力全过，却在日常交互翻车：

- **2a 播放中回退（2/2 失败）**：第 4 轮首段在播时点「回到这里」并确认，停播后 `POST /truncate` 报 `409 conversation_changed`，消息和缓存全没删，需重来一次。
- **2b 播放中改句重发（1/1 失败）**：第 4 轮首段在播时改第 2 轮并确认，同样 `409`，**弹窗关掉、改好的字全清空**。

## 怎么查

对照后端日志与 Web Audio 时间线（私有协作仓 `[311]`）：点确认先出 `[TTS Cancelled]`，截断带旧 ID `6` / `12` 被 409 拒掉；重选带 `8` / `14` 立刻 200。打开弹窗时第 4 轮尚未落库，一停播后端立刻落库，前端没等补齐 ID 就发截断，撞上防误删锁。

## 根因

1. `desktop/src/components/Workstation.tsx` 的 `handleConfirmTruncate` 停播后直接用弹窗打开时快照的 `target.expectedLastMessageId` 调接口，未等前台轮次落库刷新消息 ID。
2. 收到 `409 conversation_changed` 直接关窗清 `truncateTarget` 丢掉 `editedText`；其他失败取消时同样丢稿。

## 怎么改

作者 `Gemini 4 Neon` 提交 [`855e74f`](https://github.com/meditecnic/AMADEUS/commit/855e74f3ce0108af288547f894133516189f1d06)（`[315]`，4 文件，+288/−24）：

1. 扩展 `fetchMessageIds` 唤醒截断等待器（带单调 `readId` 与 15 秒超时）：确认时若有活跃前台轮次，先停播等落库补齐 ID，再只发一次 `POST /truncate`；
2. 仅按已上屏轮次匹配 ID（`resolveExpectedLastMessageId`），不采纳未上屏新消息，409 保护不放松；
3. 抽出 `preserveEditDraft`，`409` 或失败取消时将改稿合并回输入框；`voice_cache.py` 失败日志补打 `exc.winerror`。

## 怎么验

- 新增 3 条组件/后端测试并做逐条改坏验证，Windows [CI run](https://github.com/meditecnic/AMADEUS/actions/runs/45317025471) 6/6 全绿；
- 同台 Windows 11 真机定向复跑（私有协作仓 `[311]` 复跑节，333.69 秒）：**2a 3/3 通过、2b 3/3 通过**，6 次首击均只发一次 `POST /truncate` 且直接 HTTP 200（563–672 ms），0 控制台错误，重开后数据与 WAV 归档 SHA-256 一致；
- 经我批合，merge commit [`ad447e5`](https://github.com/meditecnic/AMADEUS/commit/ad447e5807408c3835fa5f604fe62f60ea859cd9) 进 `main`。

## 代价

首跑 514.56 秒 + 复跑 333.69 秒（约 14 分钟真机自动化）加一次 4 文件小修，把单测同步桩撞不出来的百毫秒级实机竞态拦在了合并前。
