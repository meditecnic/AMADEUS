# [166] 全仓 rg 结果说明

命令（仓库根，排除 `inventory/`、`.git`、`node_modules`、`desktop/dist`）：

- `rg -n "AMADEUS_MEMORY_MODE|memory_mode|shadow"` → `rg-mode.txt`（221 行）
- `rg -n -i "legacy"` → `rg-legacy.txt`（466 行）

## AMADEUS_MEMORY_MODE / memory_mode / shadow

- `AMADEUS_MEMORY_MODE`：0 行。`memory_mode`：只剩 `test_persona_bible.py:166` 的 `overlay_memory_model`（子串误中）。
- `shadow` 全部和记忆模式无关，逐类留：
  - CSS 的 `box-shadow`/`text-shadow`/`drop-shadow`/`--shadow-tone`、WebGL 着色器注释、`DESIGN.md` 配色表：视觉样式。
  - `memory_v11/candidate.py`、`test_memory_candidate_builder.py`：SQLite FTS5/vec0 的 shadow tables（虚表的影子表）。
  - `memory_v11/contracts.py:11`「shadow default」：v11 自动激活阈值的注释，派单不让动 `memory_v11/`。
  - 测试里的 `shadowing`/`shadows`：「实例方法遮住类补丁」的注释。

## legacy（按类）

1. **旧表导入 v11（迁移还要用）**：`memory_v11/migration.py`、`candidate.py`、`repository.create_stable_fact_from_legacy`、`recall.py`/`prompt_compiler.py:221` 的 `has_legacy_import`、`contracts.py`/`db.py` 的 `legacy_import` 取值、`db.py`「legacy core_facts retained」、`scripts/legacy_memory_migrate.py`、`scripts/legacy_core_facts_rehearsal.py`、`build_memory_candidate.py`、`readingDetails.ts:552`（`legacy_import: '历史导入'` 标签）；对应测试 `test_legacy_core_facts_rehearsal.py`、`test_memory_candidate_builder.py`、`test_memory_v11_migration.py`、`test_legacy_memory_live_migration.py`、`test_memory_v11_schema.py`、`test_shared_user_facts.py`（v7→v8 迁移）、`test_conversations.py`（identity_mode 回填旧行）、`test_data_isolation_guard.py`。
2. **旧表数据还在、删除传播**：`conversation_erasure.py:190`（`legacy = forget_exclusive_sources(...)`）、`memory.py` 模块说明、测试辅助 `tests/legacy_memory_rows.py` 及用它造数据的 `test_worldline_architecture.py`、`test_memory_v11_chat_entry.py`（断言旧表行不进 v11 prompt）、`test_memory_v11_prompt.py::test_r5_v11_no_legacy_episodic_leak`、`test_memory_v11_default.py`。
3. **v11 内部用词**：`memory_v11/jobs.py`、`reconciler.py` 的「legacy observation receipt / legacy CREATE ops」、`retrieval.py` 注释、`contracts.py:23`；测试 `test_memory_v11_jobs.py`、`test_memory_v11_reconciliation.py`。派单不让动 `memory_v11/`。
4. **prompt_compiler 的 `resident_profile=None` 渲染路径**：`prompt_compiler.py:314/321/478/738/769/788` 注释，`test_memory_v11_resident_profile.py::test_no_facts_means_no_section_v11_but_legacy_keeps_none`。`compile_for_session` 不再走到，但 `PromptCompiler` 是跨模块接口，没动（报告「拿不准」）。
5. **已删接口的守卫测试**：`test_memory_v11_api.py` 的 `LEGACY_ROUTE_TEMPLATES` / `test_s0_legacy_product_routes_must_exit`（去掉 xfail，断言旧路由不在）、`test_s3f2c_..._legacy_defaults_preserved`（名字没改）；`test_memory_recall_b5b_read_gate.py::test_legacy_web_only_request_never_carries_read_gate_rule`（`plan_first_round_tools(v11=False)` 仍存在）。
6. **和记忆无关的其他兼容层**：`search.py`（旧同步搜索）、`providers.py`/`provider_runtime.py`/`model_catalog.py`（旧 custom 提供方）、`main.py:44`（旧 `DEEPSEEK_API_KEY` 搬进凭据库）、`security/prompt.py`、`persona_bible.py`、`soul_engine.py`、`deepseek.py`（okabe 默认）、`working_summary.py`（无版本旧摘要）、`db.py`（`AMADEUS_DB_PATH` 覆盖、旧 `amadeus.db` 备份）、README 两处（`AMADEUS_DB_PATH`）、前端 `Workstation.tsx`（旧 localStorage key、旧搜索字段）、`AvatarViewer.tsx`、`SpriteAnimator.tsx`、`motion.css`、`workstation.css`、`SettingsModal.tsx`、`useAmadeusWS.ts`（v1 协议）及其测试；后端测试 `test_custom_profiles.py`、`test_credential_store_selection.py`、`test_diagnostics_storage.py`、`test_chat_core_exception_handling.py`、`test_provider_http_errors.py`、`test_tts_regression.py`、`test_persona_bible.py`、`test_provider_credentials.py`、`test_model_catalog.py`、`test_identity_acknowledged.py`、`test_e2e_contract.py`、`test_working_summary.py`、`test_language_publication.py:1441`。
