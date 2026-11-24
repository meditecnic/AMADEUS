> 摘录自私有协作仓，已脱敏。删节处以「（略）」标明，未改写原意。

# [321] 审查 PR #107 日语重写器

- **REWORK**。引号语音误判日文，并漏掉既有中文引文。对照返工 1 `0a42b5b`，下列问题仍适用。
- Windows Codex GPT-6.2 Sol｜2026-11-24｜`748d206985c3f2d39ba8f16f5c56386dd6d096d4`，基线 `07940c9`。
- 本机产物：（略）

## 必须改

1. **P2 日文误切**。`tts_queue.py:97–150` 复用正文拒绝判据，把 CP932 编码失败和「所以」视为中文。离线对照中，「没頭」「発表」仍为 `ja`，日文「所以」「充塡して」从 `ja` 变 `auto`。混写的「職場摸鱼」还会把「職場」一并加进标记。将语音切换限于可定位的高置信中文片段，补日文和混写相邻汉字反例。
2. **P2 引文来源回归**。`tts_queue.py:116–141` 剥掉来源引号后才看汉字和假名。用户只发「老板」时，同引文由 `auto` 退回 `ja`。用户说「我刚才说『了解』，不是『いいよ』」时，回复引「了解」由 `ja` 变 `auto`。歧义词沿用基线来源匹配，新高置信规则覆盖 H06 T12 等混写场景，分别补两例。
3. **收掉无调用用途的接口**。`chat_ws.py:1097,1231,1271` 与 `japanese_response_renderer.py:64` 的 `user_request` 在生产路径恒空且被丢弃，`_latest_real_user_request` 零调用。删除两层参数和旧函数，更新直调测试，保留仅含 `assistant_draft` 的载荷断言。

## 核查

- 拼接顺序通过。开头为重写→后缀，中间为前缀→重写→后缀，整段只发重写。两次重写失败仍走基线退路，保留已推前缀，缓冲轮仅发退路文案。v2 比较两份 `(text, lang)` 能识别同为 `auto` 时额外标记的引文。屏幕和历史保留原字。
- v1、v2 和语音流进同一 `processor_loop`，先编译本轮 `inputs`。#106 `6760e3a` 试合无冲突，后台快照取目标会话身份并在生成前重新编译。缺 `inputs` 时已知 `self` 走敬体，身份未知走常体，与初见默认一致。
- 变档为 DeepSeek Flash／V4 Flash／V4 Pro（含直连自定义）`disabled→low`、GPT-5.6 Luna（含直连）及 GPT-6 Luna（直连）`none→low`、Gemini 3.6／3.5 Flash `minimal→low`。GLM 5.2 保持关闭，其余已是 `low` 或无控件。假 HTTP 验证隔离载荷，DeepSeek 无工具，OpenAI Responses／直连分别用 `reasoning.effort`／`reasoning_effort`，Gemini 用 `thinkingLevel`。官方均支持该档。[DeepSeek](https://api-docs.deepseek.com/guides/thinking_mode/) · [OpenAI 5.6](https://developers.openai.com/api/docs/models/gpt-5.6-luna) · [OpenAI 6](https://developers.openai.com/api/docs/models/gpt-6-luna) · [Gemini](https://aistudio.google.com/docs/thinking)
- 按 6/257、每次多 200–500 输出 token、评测汇率 7.25，百轮峰时 ¥0.0041–0.0102，谷时减半。估算只计单次增量输出，提示词输入差和重试另计。¥0.28 是含模拟用户的评测口径。[DeepSeek 价格](https://api-docs.deepseek.com/quick_start/pricing/)
- 四个老测试文件的改动与新载荷和档位一致，模拟器、上下文隔离及协议字段断言保留。生产净 +103（渲染器 +29、路由 +28、TTS +46），规模与需求相称。

## 建议

- 改档位矩阵测试名以反映优先 `low`，补 `self` 初见的编译到重写断言。

## Windows 验证

- `python -m pytest -q -rfEsx --timeout 120` → `3929 passed, 4 skipped, 2 xfailed, 1 warning in 3018.88s (0:50:18)`。墙钟 3020.87s。
- 相关组 101 passed（58.51s，含两项 ONNX），七项 Windows 专用用例通过。补跑 Pi 1.5.0 委派通过。Pi 护栏和两项诊断用例受符号链接权限限制。
- 后端 CI 口径 `ruff check` 通过。根目录有基线已有的 71 项 CI／打包告警。
- [319] 旧重写耗时 1.09–2.35s。新档按新增 200–500 token 除实际吞吐量估算，排队和重试另计。模型输出与实际发音待验。
