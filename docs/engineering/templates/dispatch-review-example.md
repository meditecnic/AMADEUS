> 摘录自私有协作仓，已脱敏。删节处以「（略）」标明，未改写原意。

# [321] 审查：PR #107（`[320]` 日语护栏重写器）

- 级别：L2，跨厂审。被审 Draft [PR #107](https://github.com/meditecnic/AMADEUS/pull/107)，分支 `neon/320-renderer-voice`，**头 `748d206985c3f2d39ba8f16f5c56386dd6d096d4`**，基线 `07940c9`。
  - 8 个文件，+464/−34，其中生产代码净增 103 行（3 个文件）。
  - 作者是 Google 的 `Gemini 4 Neon`（Ubuntu Antigravity），作者报告 `reports/320-renderer-echo-and-voice.md`。
- 审：**Windows 本机 Codex `GPT-6.2 Sol`，新会话**。不用 `[310]`、`[304]`/`[317]` 那几个会话。
- 日文提示词的口吻不归你管，由 designer 读、U 签。你看代码和行为。
- 写于：2026-11-24（总调度，（略））
- 发法：（略）

## 派单正文

[321]【语言护栏 · 审查】PR #107：重写器不再复述用户的话、守住口吻、引号中文进语音

**要读的**
- 派单 `dispatch/320-renderer-echo-and-voice.md`，范围以它为准；
- 作者报告 `reports/320-renderer-echo-and-voice.md`；
- 现象出处 `reports/319-stage2-drift-export.md`（11 次原生草稿和最终正文）。

**请重点看**（供你核对，怎么查你定）
1. **拼接**：`chat_ws.py:1088` 起，`partial` 取 `published_parts or suffix`。开头一段、中间一段、整段被拒三种情况下，最终正文和推给前端的顺序都对不对。重写失败时的退路和原来一样不一样。
2. **口吻取值**：`_renderer_register` 从 `turn_compiled_prompt.inputs` 读 `identity_mode` 和熟悉度。所有会走到重写器的路径（v1、v2、语音流）都拿得到吗？拿不到时默认 `plain` 对不对？PR #106（3-D 后台续跑）会让后台轮次用 `snapshot` 出来的状态，合进去以后还读得到吗？`git merge-tree` 和 #106 头 `6760e3a` 试合没有冲突，这里只问语义。
3. **思考档**：模型给了 `low` 就用 `low`。看 `model_catalog` 里哪些模型会因此变档。隔离调用带 `low` 会不会被哪家拒掉。被重写的那一轮会慢多少，能估就估。作者算的是每百轮多 ¥0.004–0.010，你核一下口径。
4. **引号中文进语音**：`tts_speech_surface` 判「中文独有字」的办法会不会误伤纯日文引号。比如日文里也用、和简体同形的字，像「没頭」「発表」这类。`processor_loop` 改成拿有无 `quoted_source` 两次结果相比，意思和原来一样不一样。
5. **多余的东西**：`user_request` 参数现在永远传空串，`_latest_real_user_request` 还有没有人用。派单说过不留没人要的保护，该删的就删。
6. **改了期望的 4 处老测试**：理由成不成立，有没有借机放松。
7. 规模超了派单估计（103 行对 30–80 行），多出来的值不值。

**跑**
- Windows 本机后端全量 `python -m pytest -q -rfEsx --timeout 120`，贴汇总行。作者在 Linux 上跳过的 9 项、缺 `onnxruntime` 的 2 项，你这里都要跑到。`ruff check`。
- 不调真实模型，不花钱。真实模型复跑等你审过以后，总调度另派。

**交付**
- 判 `PASS` 或 `REWORK`。必须改的逐条编号，写出处和最小改法；建议另列，不挡合并。
- 报告 `reports/321-review-320-codex.md`，4 KB 以内。提交标题 `review: [321] <简述> (Codex GPT-6.2 Sol)`。
- 先跑 `tools/check-staged.sh`，只暂存你的报告，`git pull --rebase` 后推 `codex/ops-bootstrap-20261013`。不改 AMADEUS 的分支，不在 GitHub 上评论。退出前删掉 worktree。
