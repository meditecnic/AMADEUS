# 案例：PR #107 —— 日语护栏重写器，¥0.051 真模型复跑与先写死判法

- **日期**：2026-11-24
- **当前状态**：截至 2026-11-24，[PR #107](https://github.com/meditecnic/AMADEUS/pull/107)（`[320]` 日语护栏重写器）仍为 **Draft，尚未合入 `main`**，待不开思考对照组复跑判过后请我批合。
- **公开记录**：[PR #107](https://github.com/meditecnic/AMADEUS/pull/107)｜作者 `Gemini 4 Neon`｜提交 [`748d206`](https://github.com/meditecnic/AMADEUS/commit/748d206985c3f2d39ba8f16f5c56386dd6d096d4) → [`0a42b5b`](https://github.com/meditecnic/AMADEUS/commit/0a42b5b6d346015863f6538e7209e50d87f00db7) → [`dd0d770`](https://github.com/meditecnic/AMADEUS/commit/dd0d770a40ac6c056d478aac984a4df561420d95)｜最新 [CI run](https://github.com/meditecnic/AMADEUS/actions/runs/45343664955)（6/6 绿）

## 现象

多轮人格真实模型评测第二阶段（私有协作仓 `[306]`/`[319]`）共出现 **11 次**原生中文漂移并触发日语重写器。对照原生草稿与最终输出，查出三处问题：

1. **复述用户的话**：`H06 T8`、`H09 T8`、`H11 T6`、`H14 T6` 四条里，草稿只是开头飘出两三个中文字（如「摸鱼……」「摆烂……」），重写器却把用户那整句中文翻成日语塞在她回复开头。
2. **口吻跑偏**：频繁蹦出「〜だよ」「〜だね」「〜だろ」等男性/中性句尾。
3. **引号中文进语音漏网**：3 处含假名或标点差异的引号中文绕过了 TTS 语种切分。

## 怎么查

导出全部 11 条原生草稿、用户句与重写正文（私有协作仓 `[319]`）再核源码：`chat_ws.py` 把 `_latest_real_user_request` 和被拒片段一起塞进重写器载荷，旧提示词又允许按用户原话补全短草稿；同时旧提示词没有口吻约束，思考档也锁死在最低档（`deepseek-flash` 即 `disabled`）。

## 怎么改

修改把**代码**和**日语口吻**拆线把关：

1. **首版**（[`748d206`](https://github.com/meditecnic/AMADEUS/commit/748d206985c3f2d39ba8f16f5c56386dd6d096d4)）：拿掉用户原话；加 `register`（常体/敬体）与 `partial`；支持 `low` 思考档的模型改用 `low`；补 3 处引号中文切分。
2. **口吻定稿**（`Designer` 读 + 我亲签 → [`0a42b5b`](https://github.com/meditecnic/AMADEUS/commit/0a42b5b6d346015863f6538e7209e50d87f00db7)）：4 句日文逐字按签字版落盘。
3. **异厂审查**（`Codex GPT-6.2 Sol`，私有协作仓 `[321]`，见 [审查报告样板](../templates/review-report-example.md)）：判 **`REWORK`**，抓出日文「所以」「充塡して」误切 `auto`、「職場摸鱼」错标「職場」、基线引文回归与死参数 `user_request`。返工 2 [`dd0d770`](https://github.com/meditecnic/AMADEUS/commit/dd0d770a40ac6c056d478aac984a4df561420d95) 用 `gb2312`/`euc_jis_2004` 差集收窄判定并补 7 个反例测试。

## 怎么验

离线假桩测不出真实输出。我批 **¥0.1 上限**，总调度发单前写死 4 条判法（见 [判法与判定表样板](../templates/rubric-and-verdict-example.md)），派 `Codex GPT-6.2 Sol` 走产品代码用真实 `deepseek-flash` 复跑 11 条原生草稿（2 条各加跑 2 次，共 15 次调用，私有仓 `[322]`）：

- **回声 4/4 过**、**禁用句尾 0 命中**、**「〜しよう」未复发**；
- **Designer 读意思**：9/11 过，抓出 `H09 T8`、`H11 T6` 短断片丢标点或被补成谓语——签字补句进 `dd0d770`，加排「关思考 vs `low`」对照权衡 1.2–8.5s 延迟。

## 代价

15 次真模型调用实花 **¥0.051**（`¥0.051216030`，峰价，上限 ¥0.1），加一轮异厂审、一轮 Designer 读与两次返工。五分钱换来真实硬证据，还提前抓出短断片标点和延迟权衡两个真问题。
