# 案例：PR #107 —— 日语护栏重写器，¥0.072 真模型复跑与先写死判法

- **日期**：2026-11-24
- **公开记录**：[PR #107](https://github.com/meditecnic/AMADEUS/pull/107)（`[320]` 日语护栏重写器）｜作者 `Gemini 4 Neon`｜提交 [`748d206`](https://github.com/meditecnic/AMADEUS/commit/748d206985c3f2d39ba8f16f5c56386dd6d096d4) → [`0a42b5b`](https://github.com/meditecnic/AMADEUS/commit/0a42b5b6d346015863f6538e7209e50d87f00db7) → [`dd0d770`](https://github.com/meditecnic/AMADEUS/commit/dd0d770a40ac6c056d478aac984a4df561420d95)｜合并 [`c27b2ab`](https://github.com/meditecnic/AMADEUS/commit/c27b2ab8965ce04766b23e43256cca0cff00ed8d)｜[CI run](https://github.com/meditecnic/AMADEUS/actions/runs/45343664955)（6/6 绿）

## 现象

多轮人格真模型评测第二阶段（私有仓 `[306]`/`[319]`）出现 **11 次**原生中文漂移并触发重写器，查出三处问题：

1. **复述用户**：`H06 T8`、`H09 T8`、`H11 T6`、`H14 T6` 草稿只是开头飘出两三个中文字（如「摸鱼……」「摆烂……」），重写器却把用户整句中文翻成日语塞在她开头。
2. **口吻跑偏**：频繁出现「〜だよ」「〜だね」「〜だろ」等男性/中性句尾。
3. **引号中文进语音漏网**：3 处含假名或标点差异的引号中文绕过 TTS 语种切分。

## 怎么查

导出 11 条原生草稿、用户句与重写正文（私有仓 `[319]`）并核源码：`chat_ws.py` 把 `_latest_real_user_request` 与被拒片段一起传给重写器，旧提示词又允许按用户原话补全短草稿；同时旧提示词无口吻约束，思考档锁死最低档（`deepseek-flash` 为 `disabled`）。

## 怎么改

把**代码**和**日语口吻**拆线把关：

1. **首版**（[`748d206`](https://github.com/meditecnic/AMADEUS/commit/748d206985c3f2d39ba8f16f5c56386dd6d096d4)）：拿掉用户原话；加 `register`（常体/敬体）与 `partial`；支持 `low` 思考档改用 `low`；补 3 处引号中文切分。
2. **口吻定稿**（`Designer` 读 + 我签字 → [`0a42b5b`](https://github.com/meditecnic/AMADEUS/commit/0a42b5b6d346015863f6538e7209e50d87f00db7)）：4 句日文按签字版落盘。
3. **异厂审查**（`Codex GPT-6.2 Sol`，私有仓 `[321]`，[样板](../templates/review-report-example.md)）：判 **`REWORK`**，抓出日文「所以」「充塡して」误切 `auto`、「職場摸鱼」错标「職場」、基线引文回归与死参数 `user_request`。返工 2 [`dd0d770`](https://github.com/meditecnic/AMADEUS/commit/dd0d770a40ac6c056d478aac984a4df561420d95) 用 `gb2312`/`euc_jis_2004` 差集收窄判定并补 7 个反例。

## 怎么验

我批 **¥0.1 上限**，发单前写死 4 条判法（[样板](../templates/rubric-and-verdict-example.md)），派 `Codex GPT-6.2 Sol` 走产品代码调真实 `deepseek-flash` 复跑 11 条原生草稿（私有仓 `[322]`）：

- **首跑（¥0.051，15 次）**：回声 4/4 过、禁用句尾 0 命中、「〜しよう」未复发；Designer 读意思 9/11 过，抓出 `H09 T8`、`H11 T6` 短断片丢标点或被补谓语，签字补句进 `dd0d770`。
- **加跑对照（¥0.021）**：一验短断片，二比不开思考。不开思考组出 3 处客观毛病：单个词被改成谓语、留中文词「准入」、有一句不是日语；因此留 `low`（耗时中位数 4.1 s 对 2.2 s，只在草稿被拒时跑）。
- 经我批合，merge commit [`c27b2ab`](https://github.com/meditecnic/AMADEUS/commit/c27b2ab8965ce04766b23e43256cca0cff00ed8d) 进 `main`。

## 代价

真实复跑两轮合计 **¥0.072**（首跑 ¥0.051 + 加跑 ¥0.021，上限 ¥0.1），加一轮异厂审、一轮 Designer 读与两次返工。花 ¥0.072 拿到真实模型的实测结果，还顺带抓出短断片标点和延迟权衡两个真问题。
