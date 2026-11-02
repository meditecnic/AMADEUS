# [166] 旧版记忆调用追踪（动手前先盘）

起点：`claude/code-inventory-20261101` 的 `inventory/out/candidates.csv` 里类别「旧版记忆」的 48 行，加「待定（脚本）」`legacy_memory_migrate.py`、「评测/实验脚本」`_path_guards.py`。基线 `15c19fbe7c90bafed1a4d7e13c46e6b07c06f814`，按名字在 `backend/app`、`backend/scripts`、`packaging`、`desktop/src` 里 grep 调用方（测试另算）。

| 文件 | 符号 | 判定 | 理由（追到的调用方） |
|---|---|---|---|
| chat_ws.py | processor_loop 旧版抽取分支（`schedule_turn_extraction`） | 删 | 只在 `memory_mode()` 非 shadow/v11 时走；同时删掉只为旧版的 `force_direct_search` 分支和非 v11 工具执行分支 |
| prompt_compiler.py | compile_for_session 的 else 分支（`select_core_facts`+`lexical_search`） | 删 | 只在非 v11 时走 |
| system_api.py | memory_status 无 identity_mode 分支（`memory_service.status`） | 改 | 分支删掉，`identity_mode`/`session_id` 必填；`runtime_mode` 字段去掉 |
| system_api.py | forget_memory（`POST /api/memory/forget`） | 删 | 前端没有调用方；唯一调用 `MemoryService.forget` |
| system_api.py | memory_facts_ledger 无 identity_mode 分支 | 改 | 只为旧账本，前端 `fetchLegacyFacts` 一处调用（一并删） |
| system_api.py | promote / reclassify / dismiss / dismiss-batch / delete-local / shared-facts delete | 删 | 前端只有 `deleteLegacyFact` 调后三个（一并删）；promote、reclassify 前端无调用 |
| system_api.py | `_require_v11_memory_write` | 删 | 清单写「不动」，派单改判：不再有非 v11；7 处调用一并去掉 |
| system_api.py | `ForgetRequest`、`DismissBatchRequest` | 删 | 只被上面两个路由用 |
| memory_v11/jobs.py | `memory_mode` | 删 | 清单写「不动」，派单改判；调用方 main/prompt_compiler/chat_ws/system_api 全部改掉 |
| memory.py | `_has_memory_intent`、`_MEMORY_INTENT_KEYWORDS`、`should_skip_core_extraction` | 删 | app 内无调用（只测试） |
| memory.py | `is_durable_profile_core`、`strip_history_emo_prefix`、`build_extraction_instruction`、`parse_extraction_payload`、`_EMO_HISTORY_PREFIX`、`_BLOCKED_/_EPHEMERAL_PROFILE_KEY_MARKERS`、`_HOLLOW_FACT_VALUES`、`_CONTEXTUAL_TIME_MARKERS`、质量门常量 | 删 | 只在抽取链 `_extract_turn`→`apply_extraction` 里用 |
| memory.py | `add_episode`、`upsert_core_fact`、`apply_extraction`、`_current_core_value`、`schedule_turn_extraction`、`_extract_turn`、`_extraction_tasks` | 删 | 入口只有 chat_ws 旧分支 |
| memory.py | `SharedFactPromotionError`、`ReclassifyError`、`DismissError`、`MemoryDeleteError` | 删 | 只被旧路由和旧方法引用 |
| memory.py | `_evaluate_promotion_evidence`、`list_memory_facts_for_ledger`、`promote_local_self_fact`、`reclassify_okabe_fact_to_self`、`dismiss_okabe_fact(s_bulk)`、`delete_local_self_fact`、`delete_shared_fact`、`_validate_reclassify_evidence`、`_strict_parse_source_ids`、`_reclassify_row_matches` | 删 | 唯一调用方是删掉的旧路由 |
| memory.py | `select_core_facts`、`hybrid_search`、`lexical_search`、`status`、`forget` | 删 | 调用方只有删掉的分支/路由；hybrid_search app 内无调用 |
| memory.py | `_write_shared_fact_current`、`upsert_shared_user_fact`、`_current_shared_fact_rows`、`_verify_shared_provenance`、`_shared_row_matches`（清单「待定」） | 删 | 调用方只有 promote、select_core_facts、ledger、upsert_shared_user_fact（后者 app 内无调用）；memory_v11 不引用 |
| memory.py | `EpisodicCandidate`、`CoreFactCandidate`、`HorizonFactCandidate`、`ExtractionResult`（「待定」） | 删 | 只在旧抽取/写入/共享写里用；memory_v11、迁移代码不引用 |
| memory.py | `_fts_search`、`_vector_search_sync`、`reciprocal_rank_fusion`（「待定」） | 删 | 只被 lexical_search / hybrid_search 用 |
| memory.py | `_try_index`、`_index_sync`（「待定」） | 删 | 只被 add_episode / upsert_core_fact 用；v11 自己的索引在 `memory_v11/indexing.py` |
| memory.py | `_delete_vectors_sync`（「待定」） | 删 | 只被 forget 用；`forget_exclusive_sources` 自己在事务里删向量 |
| memory.py | `_SHARED_PROFILE_RESERVED_SEATS`、`_N_TURNS_CORE_THROTTLE` | 删 | 只被 select_core_facts / 无人用 |
| memory.py | `RetrievedMemory` | 留 | prompt_compiler v11 路径 |
| memory.py | `EmbeddingAdapter`、`E5_REVISION` | 留 | memory_v11 indexing/retrieval、system_api readiness、packaging |
| memory.py | `MemoryService.__init__`/`embedder`、`close()` | 留 | v11 取 `memory_service.embedder`；`close()` 由 main 关停调用，无后台任务后为空操作 |
| memory.py | `forget_exclusive_sources` | 留 | conversation_erasure 删对话时抹旧表 |
| memory.py | `MEMORY_INGEST_WINDOW_N` | 留 | chat_ws `recent_messages` 和压缩 keep_count |
| archiveApi.ts | `deleteLegacyFact`（以及 `fetchLegacyFacts`、`LegacyFact`） | 删 | 只被 `LegacyFactsView.tsx` 用（整个删） |
| scripts | `legacy_memory_migrate.py` | 留 | 派单「不动」；不用被删符号 |
| scripts | `_path_guards.py` | 留 | `legacy_core_facts_rehearsal.py`、`build_memory_candidate.py` 在用 |

## 拿不准（先留着）

- `chat_ws.build_web_search_evidence_context`：唯一产品调用是删掉的 `force_direct_search` 分支，现在只有测试用。
- `SessionState.recent_messages()`：唯一产品调用是删掉的旧抽取窗口，现在只有测试用。
- `turn_tool_orchestrator.plan_first_round_tools(v11=...)`：`v11=False` 分支已没有产品调用方（chat_ws 和 `provider_tool_capable` 都传 True）。没动：不在派单范围。
- `PromptCompiler.compile` 在 `resident_profile=None` 时的旧 CORE FACTS 渲染（含 `CORE FACTS\n(none)`）：`compile_for_session` 不再走到，`prompt_compiler` 是跨模块接口，没动。
