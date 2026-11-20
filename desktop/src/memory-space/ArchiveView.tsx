import { MemorySelect } from './MemorySelect';
import { motion, useReducedMotion } from 'motion/react';
import { useState, useSyncExternalStore } from 'react';

import type { ArchiveScope, DiaryBrowseRow } from './archiveApi';
import {
  scopeLabel,
  type ArchiveModule,
  type ArchiveState,
} from './archiveState';
import {
  deriveExperienceReading,
  deriveFactReadingArchive,
  formatTimeHuman,
  type ExperienceReadingModel,
  type FactReadingModel,
  type SourceLine,
} from './readingDetails';

// S4-ARCHIVE Gate 2 view (§5): a restrained record list plus one selected
// record reading pane. Facts and Experiences keep their own lifecycle and
// capabilities; confidence is never rendered; pin/expiry are text-announced;
// every destructive control is explicit and backend-authorized.

export type ArchiveViewProps = {
  module: ArchiveModule;
  scope?: ArchiveScope;
  width?: number;
  reducedMotion?: boolean;
};

const NARROW_WIDTH = 900;

const PROVIDER_DISCLOSURE =
  '已记住的内容会在对话时发给当前所选的模型服务商；保存在本机不代表对话时不外发。';

function useArchiveState(module: ArchiveModule): ArchiveState {
  return useSyncExternalStore(module.subscribe, module.snapshot);
}

function SourceLines({ lines }: { lines: SourceLine[] }) {
  if (!lines.length) {
    return <p className="archive-sources-empty">暂无可显示的原话摘录</p>;
  }
  return (
    <ul className="archive-sources">
      {lines.map((line, index) => (
        <li key={`${index}-${line.sourceErased ? 'erased' : 'present'}`}>
          {line.sourceErased ? (
            <span>原始消息已删除，仅保留来源记录</span>
          ) : line.excerpt ? (
            <>
              <blockquote>{line.excerpt}</blockquote>
              {line.sourceCreatedHuman ? <span>{line.sourceCreatedHuman}</span> : null}
            </>
          ) : (
            <><p>这条来源记录暂未提供摘录</p>{line.sourceCreatedHuman ? <span>{line.sourceCreatedHuman}</span> : null}</>
          )}
        </li>
      ))}
    </ul>
  );
}

function FactReading({ model }: { model: FactReadingModel }) {
  return (
    <div className="archive-reading">
      {model.revisionSummary ? <p data-testid="archive-revision-summary">{model.revisionSummary}</p> : null}
      <SourceLines lines={model.activeSources} />
      {model.versionCount > 1 ? (
        <details data-testid="archive-versions">
          <summary>版本历史（{model.versionCount}）</summary>
          <ol>
            {model.historyVersions.map((version) => (
              <li key={version.versionNo} data-active={version.isActive ? 'true' : 'false'}>
                <span>{version.changeKindHuman}</span>
                {version.validFromHuman ? <span> · {version.validFromHuman}</span> : null}
                {version.isActive ? <span> · 当前版本</span> : null}
                <p>{version.displayText}</p>
              </li>
            ))}
          </ol>
        </details>
      ) : null}
      <details data-testid="archive-diagnostics">
        <summary>诊断</summary>
        <dl>
          <div><dt>fact_id</dt><dd>{model.diagnostics.factId}</dd></div>
          {model.diagnostics.topicIdRaw ? (
            <div><dt>topic_id</dt><dd>{model.diagnostics.topicIdRaw}</dd></div>
          ) : null}
        </dl>
      </details>
    </div>
  );
}

function ExperienceReading({ model }: { model: ExperienceReadingModel }) {
  return (
    <div className="archive-reading">
      {model.createdHuman ? <p>发生于 {model.createdHuman}</p> : null}
      {model.expiry ? (
        model.expiry.expired ? (
          <p data-testid="archive-reading-expired">
            已过期{model.expiry.expiresHuman ? `（${model.expiry.expiresHuman}）` : ''}
          </p>
        ) : (
          <p>{model.expiry.expiresHuman ? `将于 ${model.expiry.expiresHuman} 过期` : ''}</p>
        )
      ) : null}
      <SourceLines lines={model.sources} />
      <details data-testid="archive-diagnostics">
        <summary>诊断</summary>
        <dl>
          <div><dt>experience_id</dt><dd>{model.diagnostics.experienceId}</dd></div>
          <div><dt>observation_id</dt><dd>{model.diagnostics.observationId}</dd></div>
        </dl>
      </details>
    </div>
  );
}

function diarySourceLabel(conversationId: string, titles: Record<string, string>): string {
  const title = titles[conversationId];
  return title ? `来自对话「${title}」` : '来自一段早先的对话';
}

function DiaryReading({ row, sourceLabel }: { row: DiaryBrowseRow; sourceLabel: string }) {
  const writtenHuman = formatTimeHuman(row.writtenAt);
  return (
    <div className="archive-reading archive-diary-reading">
      <p className="archive-diary-aside">她以为这里只有自己看得见。</p>
      <p className="archive-diary-meta" data-testid="archive-diary-meta">
        {[writtenHuman, sourceLabel].filter(Boolean).join(' · ')}
      </p>
      <div className="archive-diary-sheet">
        <p className="archive-diary-ja" lang="ja" data-testid="archive-diary-ja">
          {row.bodyJa}
        </p>
        <div className="archive-diary-zh-block">
          <span className="archive-diary-zh-label">中文译文</span>
          <p className="archive-diary-zh" lang="zh-CN" data-testid="archive-diary-zh">
            {row.bodyZh}
          </p>
        </div>
      </div>
      <details data-testid="archive-diagnostics">
        <summary>诊断</summary>
        <dl>
          <div><dt>diary_id</dt><dd>{row.diaryId}</dd></div>
          <div><dt>conversation_id</dt><dd>{row.conversationId}</dd></div>
        </dl>
      </details>
    </div>
  );
}

export function ArchiveView({ module, scope, width, reducedMotion: motionPreference }: ArchiveViewProps) {
  const systemReducedMotion = useReducedMotion();
  const reducedMotion = motionPreference ?? systemReducedMotion;
  const state = useArchiveState(module);
  const viewWidth = width ?? (typeof window === 'undefined' ? 1280 : window.innerWidth);
  const narrow = viewWidth < NARROW_WIDTH;
  // The module owns the exact scope; the prop is a fallback for first paint.
  const effectiveScope: ArchiveScope | null = state.scope ?? scope ?? null;

  const selectedFactRow =
    state.selection?.kind === 'fact'
      ? state.facts.rows.find((row) => row.factId === state.selection?.recordId) ?? null
      : null;
  const selectedExperienceRow =
    state.selection?.kind === 'experience'
      ? state.experiences.rows.find((row) => row.experienceId === state.selection?.recordId) ?? null
      : null;
  const selectedDiaryRow =
    state.selection?.kind === 'diary'
      ? state.diaries.rows.find((row) => row.diaryId === state.selection?.recordId) ?? null
      : null;

  const readingModel: FactReadingModel | ExperienceReadingModel | null = (() => {
    if (state.selection?.kind === 'diary') return null;
    if (state.details.status !== 'ready' || !state.details.payload) return null;
    const payload = state.details.payload;
    if ('fact' in payload) {
      return deriveFactReadingArchive(
        payload,
        state.topics.items.map((topic) => ({ topicId: topic.topicId, displayLabel: topic.displayLabel })),
      );
    }
    return deriveExperienceReading(payload);
  })();

  const detailOpen = state.selection !== null || state.details.status !== 'idle';
  const activeList = state.activeKind === 'fact' ? state.facts : state.experiences;
  const filtered = Boolean(state.filters.query || state.filters.topicId || state.filters.pinnedOnly || state.filters.within);
  const topicOnlyFilter = Boolean(state.filters.topicId && !state.filters.query && !state.filters.pinnedOnly && !state.filters.within);
  const selectedTopicId = (selectedFactRow ? selectedFactRow.topicId : readingModel?.kind === 'fact' ? readingModel.diagnostics.topicIdRaw : null) ?? '';
  const showList = !narrow || !detailOpen;
  const showDetail = !narrow || detailOpen;
  const [pendingOverride, setPendingOverride] = useState<boolean | null>(null);
  const candidateObservations = state.pending.observations.filter((item) => item.status === 'candidate');
  const pendingCount = candidateObservations.length + state.pending.failedJobs.length;
  const pendingHasAlert = state.pending.observationsFailed || state.pending.statusFailed;
  const pendingHasContent = pendingCount > 0 || pendingHasAlert;
  const pendingOpen = pendingOverride ?? pendingHasContent;

  return (
    <motion.section className="archive-view" data-testid="archive-view" aria-label="记忆档案"
      initial={reducedMotion ? false : { opacity: 0, y: 10, scale: 0.985 }} animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: 'spring', stiffness: 240, damping: 28 }}>
      <div className="sr-only" role="status" aria-live="polite" data-testid="archive-announce">
        {state.announcement}
      </div>

      <header className="archive-heading">
        <h2>记忆档案</h2>
        {effectiveScope ? <span className="archive-scope-mark">{scopeLabel(effectiveScope)}</span> : null}
      </header>
      <div className="archive-toolbar">
        <div className="archive-kind-tabs" role="tablist" aria-label="记录种类">
          <button
            type="button"
            role="tab"
            id="archive-tab-fact"
            data-testid="archive-tab-fact"
            aria-selected={state.activeKind === 'fact'}
            aria-controls="archive-panel-fact"
            onClick={() => module.dispatch({ type: 'set-kind', kind: 'fact' })}
          >
            认知（{state.facts.total}）
          </button>
          <button
            type="button"
            role="tab"
            id="archive-tab-experience"
            data-testid="archive-tab-experience"
            aria-selected={state.activeKind === 'experience'}
            aria-controls="archive-panel-experience"
            onClick={() => module.dispatch({ type: 'set-kind', kind: 'experience' })}
          >
            片段（{state.experiences.total}）
          </button>
          <button
            type="button"
            role="tab"
            id="archive-tab-diary"
            data-testid="archive-tab-diary"
            aria-selected={state.activeKind === 'diary'}
            aria-controls="archive-panel-diary"
            onClick={() => module.dispatch({ type: 'set-kind', kind: 'diary' })}
          >
            日记（{state.diaries.rows.length}）
          </button>
        </div>
        {state.activeKind !== 'diary' ? (
          <>
            <label className="archive-search">
              <span className="sr-only">搜索档案</span>
              <input
                data-testid="archive-query"
                value={state.filters.query}
                placeholder="搜索记忆内容"
                aria-label="搜索档案"
                onChange={(event) => module.dispatch({ type: 'set-query', query: event.target.value })}
              />
            </label>
            <MemorySelect
              aria-label="主题筛选"
              data-testid="archive-topic-select"
              value={state.filters.topicId ?? ''}
              onValueChange={(value) =>
                module.dispatch({ type: 'set-topic', topicId: value || null })
              }
            >
              <option value="">全部主题</option>
              {state.topics.items.map((topic) => (
                <option key={topic.topicId} value={topic.topicId}>
                  {topic.displayLabel}（{topic.activeFactCount}）
                </option>
              ))}
            </MemorySelect>
            {state.topicCreate ? (
              <span className="archive-topic-create" data-testid="archive-topic-create-form">
                <input
                  aria-label="新主题名称"
                  data-testid="archive-topic-create-input"
                  placeholder="输入主题名称"
                  value={state.topicCreate.draft}
                  disabled={state.topicCreate.saving}
                  onChange={(event) =>
                    module.dispatch({ type: 'create-topic-input', text: event.target.value })
                  }
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault();
                      module.dispatch({ type: 'create-topic-save' });
                    } else if (event.key === 'Escape') {
                      event.preventDefault();
                      module.dispatch({ type: 'create-topic-cancel' });
                    }
                  }}
                />
                <button
                  type="button"
                  data-testid="archive-topic-create-save"
                  disabled={state.topicCreate.saving}
                  onClick={() => module.dispatch({ type: 'create-topic-save' })}
                >
                  创建
                </button>
                <button
                  type="button"
                  data-testid="archive-topic-create-cancel"
                  onClick={() => module.dispatch({ type: 'create-topic-cancel' })}
                >
                  取消
                </button>
              </span>
            ) : state.topicRename ? (
              <span className="archive-topic-rename">
                <input
                  aria-label="主题新名称"
                  data-testid="archive-topic-rename-input"
                  value={state.topicRename.draft}
                  onChange={(event) =>
                    module.dispatch({ type: 'rename-topic-input', text: event.target.value })
                  }
                />
                <button
                  type="button"
                  data-testid="archive-topic-rename-save"
                  disabled={state.topicRename.saving}
                  onClick={() => module.dispatch({ type: 'rename-topic-save' })}
                >
                  保存名称
                </button>
                <button
                  type="button"
                  onClick={() => module.dispatch({ type: 'rename-topic-cancel' })}
                >
                  取消
                </button>
              </span>
            ) : (
              <>
                {state.activeKind === 'fact' ? (
                  <button
                    type="button"
                    data-testid="archive-topic-create"
                    onClick={() => module.dispatch({ type: 'create-topic-start' })}
                  >
                    + 新建主题
                  </button>
                ) : null}
                {state.filters.topicId ? (
                  <>
                    <button
                      type="button"
                      data-testid="archive-topic-rename"
                      onClick={() =>
                        module.dispatch({ type: 'rename-topic-start', topicId: state.filters.topicId as string })
                      }
                    >
                      重命名该主题
                    </button>
                    <button
                      type="button"
                      data-testid="archive-topic-delete"
                      disabled={state.topicDeleting}
                      onClick={() =>
                        module.dispatch({ type: 'delete-topic', topicId: state.filters.topicId as string })
                      }
                    >
                      删除该主题
                    </button>
                  </>
                ) : null}
              </>
            )}
            <label>
              <input
                type="checkbox"
                data-testid="archive-pinned-only"
                checked={state.filters.pinnedOnly}
                onChange={(event) =>
                  module.dispatch({ type: 'set-pinned-only', pinnedOnly: event.target.checked })
                }
              />
              仅置顶
            </label>
          </>
        ) : null}
      </div>

      {state.topicNotice ? (
        <p
          className={`archive-topic-notice is-${state.topicNotice.kind}`}
          role={state.topicNotice.kind === 'error' ? 'alert' : 'status'}
          data-testid="archive-topic-notice"
        >
          <span>{state.topicNotice.text}</span>
          <button
            type="button"
            data-testid="archive-topic-notice-dismiss"
            onClick={() => module.dispatch({ type: 'dismiss-topic-notice' })}
          >
            知道了
          </button>
        </p>
      ) : null}

      {state.filters.within ? (
        <p className="archive-within" data-testid="archive-within">
          <span>只看：{state.filters.within.label} · {state.filters.within.ids.length} 条</span>
          <button type="button" data-testid="archive-within-clear" onClick={() => module.dispatch({ type: 'set-within', within: null })}>清除</button>
        </p>
      ) : null}

      {state.focusFallback ? (
        <p className="archive-fallback" role="status" data-testid="archive-focus-fallback">
          {state.focusFallback}
        </p>
      ) : null}

      {state.discardConfirm ? (
        <div
          className="archive-discard-confirm"
          role="alertdialog"
          aria-label="放弃未保存草稿"
          data-testid="archive-discard-confirm"
        >
          <p>有未保存的草稿。放弃草稿后无法恢复。</p>
          <button
            type="button"
            data-testid="archive-discard-continue"
            onClick={() => module.dispatch({ type: 'cancel-discard' })}
          >
            继续编辑
          </button>
          <button
            type="button"
            data-testid="archive-discard-confirm-button"
            onClick={() => module.dispatch({ type: 'confirm-discard' })}
          >
            放弃草稿并继续
          </button>
        </div>
      ) : null}

      <div className={`archive-body${narrow ? ' is-narrow' : ''}`}>
        {showList ? (
          <div className="archive-lists">
            {state.activeKind === 'fact' ? (
            <section
              role="tabpanel"
              id="archive-panel-fact"
              aria-labelledby="archive-tab-fact"
              aria-label="认知档案"
              data-testid="archive-facts-section"
            >
              <h3>认知</h3>
              {state.facts.status === 'loading' ? <p>正在读取认知…</p> : null}
              {state.facts.status === 'failed' ? (
                <p role="alert">认知列表暂时不可用。</p>
              ) : null}
              <ul>
                {state.facts.rows.map((row) => (
                  <li key={row.factId}>
                    <button
                      type="button"
                      data-testid="archive-row"
                      data-kind="fact"
                      data-selected={
                        state.selection?.kind === 'fact' && state.selection.recordId === row.factId
                          ? 'true'
                          : 'false'
                      }
                      onClick={() =>
                        module.dispatch({ type: 'select', kind: 'fact', recordId: row.factId })
                      }
                    >
                      <span className="archive-row-kind">认知</span>
                      <span className="archive-row-text">{row.displayText}</span>
                      {row.isPinned ? <span className="archive-row-marker">置顶</span> : null}
                      {row.updatedAt ? (
                        <span className="archive-row-time">{formatTimeHuman(row.updatedAt)}</span>
                      ) : null}
                    </button>
                  </li>
                ))}
              </ul>
              {state.facts.status === 'ready' && !state.facts.rows.length ? (
                <p data-testid="archive-facts-empty">
                  {topicOnlyFilter
                    ? '这个主题里还没有认知。可以把已有认知归到这里，或直接删除这个空主题。'
                    : '此范围内没有可显示的认知。'}
                </p>
              ) : null}
              <div className="archive-pager">
                <span data-testid="archive-facts-total">共 {state.facts.total} 条</span>
                <button
                  type="button"
                  disabled={state.facts.offset === 0}
                  onClick={() =>
                    module.dispatch({
                      type: 'page',
                      kind: 'fact',
                      offset: Math.max(0, state.facts.offset - 20),
                    })
                  }
                >
                  上一页
                </button>
                <button
                  type="button"
                  disabled={!state.facts.hasMore}
                  onClick={() =>
                    module.dispatch({ type: 'page', kind: 'fact', offset: state.facts.offset + 20 })
                  }
                >
                  下一页
                </button>
              </div>
            </section>
            ) : null}

            {state.activeKind === 'experience' ? (
            <section
              role="tabpanel"
              id="archive-panel-experience"
              aria-labelledby="archive-tab-experience"
              aria-label="片段档案"
              data-testid="archive-experiences-section"
            >
              <h3>片段</h3>
              {state.experiences.status === 'loading' ? <p>正在读取片段…</p> : null}
              {state.experiences.status === 'failed' ? (
                <p role="alert">片段列表暂时不可用。</p>
              ) : null}
              <ul>
                {state.experiences.rows.map((row) => (
                  <li key={row.experienceId}>
                    <button
                      type="button"
                      data-testid="archive-row"
                      data-kind="experience"
                      data-selected={
                        state.selection?.kind === 'experience'
                        && state.selection.recordId === row.experienceId
                          ? 'true'
                          : 'false'
                      }
                      onClick={() =>
                        module.dispatch({
                          type: 'select',
                          kind: 'experience',
                          recordId: row.experienceId,
                        })
                      }
                    >
                      <span className="archive-row-kind">片段</span>
                      <span className="archive-row-text">{row.displayText}</span>
                      {row.isPinned ? <span className="archive-row-marker">置顶</span> : null}
                      {row.isExpired ? <span className="archive-row-marker">已过期</span> : null}
                    </button>
                  </li>
                ))}
              </ul>
              {state.experiences.status === 'ready' && !state.experiences.rows.length ? (
                <p data-testid="archive-experiences-empty">此范围内没有可显示的片段。</p>
              ) : null}
              <div className="archive-pager">
                <span data-testid="archive-experiences-total">共 {state.experiences.total} 条</span>
                <button
                  type="button"
                  disabled={state.experiences.offset === 0}
                  onClick={() =>
                    module.dispatch({
                      type: 'page',
                      kind: 'experience',
                      offset: Math.max(0, state.experiences.offset - 20),
                    })
                  }
                >
                  上一页
                </button>
                <button
                  type="button"
                  disabled={!state.experiences.hasMore}
                  onClick={() =>
                    module.dispatch({
                      type: 'page',
                      kind: 'experience',
                      offset: state.experiences.offset + 20,
                    })
                  }
                >
                  下一页
                </button>
              </div>
            </section>
            ) : null}

            {state.activeKind === 'diary' ? (
            <section
              role="tabpanel"
              id="archive-panel-diary"
              aria-labelledby="archive-tab-diary"
              aria-label="日记档案"
              data-testid="archive-diaries-section"
            >
              <div className="archive-diary-list-head">
                <h3>日记</h3>
                <p className="archive-diary-whisper">她以为只有自己能看到的随手记。</p>
              </div>
              {state.diaries.status === 'loading' ? <p>正在读取日记…</p> : null}
              {state.diaries.status === 'failed' ? (
                <p role="alert">日记列表暂时不可用。</p>
              ) : null}
              <ul>
                {state.diaries.rows.map((row) => {
                  const meta = [
                    formatTimeHuman(row.writtenAt),
                    diarySourceLabel(row.conversationId, state.conversationTitles),
                  ].filter(Boolean).join(' · ');
                  return (
                    <li key={row.diaryId}>
                      <button
                        type="button"
                        data-testid="archive-row"
                        data-kind="diary"
                        data-selected={
                          state.selection?.kind === 'diary' && state.selection.recordId === row.diaryId
                            ? 'true'
                            : 'false'
                        }
                        onClick={() =>
                          module.dispatch({ type: 'select', kind: 'diary', recordId: row.diaryId })
                        }
                      >
                        <span className="archive-row-kind is-diary">日记</span>
                        <span className="archive-row-text">{row.bodyZh || row.bodyJa}</span>
                        {meta ? <span className="archive-row-time">{meta}</span> : null}
                      </button>
                    </li>
                  );
                })}
              </ul>
              {state.diaries.status === 'ready' && !state.diaries.rows.length ? (
                <p className="archive-diaries-empty" data-testid="archive-diaries-empty">
                  她还没有写下日记。同一段对话聊够几轮，或停下来过一会儿后，她会在后台记下一小段。
                </p>
              ) : null}
              <p className="archive-diary-total" data-testid="archive-diaries-total">
                共 {state.diaries.rows.length} 篇
              </p>
            </section>
            ) : null}
          </div>
        ) : null}

        {showDetail ? (
          <aside
            className="archive-detail"
            data-testid="archive-detail"
            aria-label="选中记录"
            data-empty={!state.selection && state.details.status === 'idle'}
            data-reading-kind={state.selection?.kind === 'diary' && selectedDiaryRow ? 'diary' : undefined}
          >
            {narrow ? (
              <button type="button" data-testid="archive-back" onClick={() => module.dispatch({ type: 'back' })}>
                返回列表
              </button>
            ) : null}
            {!state.selection && state.details.status === 'idle' ? (
              state.activeKind === 'diary' ? (
                <div className="archive-detail-empty is-diary">
                  <span className="archive-empty-caption" aria-hidden="true">私密手记 / 日记</span>
                  <p>
                    {state.diaries.status === 'loading'
                      ? '正在翻找日记'
                      : state.diaries.status === 'failed'
                        ? '日记暂时不可用'
                        : state.diaries.rows.length === 0
                          ? '这里还没有她的日记'
                          : '翻开一条她的日记'}
                  </p>
                  <span>
                    {state.diaries.status === 'failed'
                      ? '目录读取失败，请稍后重试。'
                      : state.diaries.rows.length === 0
                        ? '同一段对话聊够几轮，或停下来过一会儿后，她会在后台悄悄写下一小段。'
                        : '她在对话之后写给自己的随手记，只有这里能看到。'}
                  </span>
                </div>
              ) : (
                <div className="archive-detail-empty">
                  <span className="archive-empty-caption" aria-hidden="true">记忆 / 读取区</span>
                  <p>{activeList.status === 'loading' ? '正在读取记忆' : activeList.status === 'failed' ? '记忆暂时不可用' : activeList.total === 0 ? (state.activeKind === 'fact' && topicOnlyFilter ? '这个主题里还没有认知' : filtered ? '没有符合条件的记忆' : '这里还没有记忆') : '选择一条记忆，展开它的来处'}</p>
                  <span>{activeList.status === 'failed' ? '目录读取失败，请稍后重试。' : state.activeKind === 'fact' && topicOnlyFilter && activeList.total === 0 ? '可以把已有认知归到这里，或直接删除这个空主题。' : filtered ? '可以调整搜索内容或筛选范围。' : '认知、片段与来源，会在这里连成线索。'}</span>
                </div>
              )
            ) : null}
            {state.selection?.kind === 'diary' ? (
              selectedDiaryRow ? (
                <motion.div
                  className="archive-reading-page is-diary"
                  key={`diary:${selectedDiaryRow.diaryId}`}
                  initial={reducedMotion ? false : { opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: reducedMotion ? 0 : 0.3 }}
                >
                  <p className="archive-detail-kind is-diary">
                    她的日记{effectiveScope ? ` · ${scopeLabel(effectiveScope)}` : ''}
                  </p>
                  <DiaryReading
                    row={selectedDiaryRow}
                    sourceLabel={diarySourceLabel(selectedDiaryRow.conversationId, state.conversationTitles)}
                  />
                  <div className="archive-governance">
                    <button
                      type="button"
                      data-testid="archive-delete-record"
                      onClick={() => module.dispatch({ type: 'request-delete' })}
                    >
                      删除
                    </button>
                  </div>
                </motion.div>
              ) : state.diaries.status === 'loading' ? (
                <p>正在读取日记…</p>
              ) : (
                <p role="status">该日记在此范围内不再可用</p>
              )
            ) : null}
            {state.selection?.kind !== 'diary' && state.details.status === 'loading' ? <p>正在读取记录…</p> : null}
            {state.selection?.kind !== 'diary' && state.details.status === 'not-found' ? (
              <p role="status">该记忆在此范围内不再可用</p>
            ) : null}
            {state.selection?.kind !== 'diary' && state.details.status === 'failed' ? (
              <p role="alert">记录暂时不可用。</p>
            ) : null}
            {state.selection?.kind !== 'diary' && state.details.status === 'ready' && readingModel ? (
              <motion.div className="archive-reading-page" key={`${state.selection?.kind}:${state.selection?.recordId}`}
                initial={reducedMotion ? false : { opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
                transition={{ duration: reducedMotion ? 0 : 0.3 }}>
                <p className="archive-detail-kind">
                  {readingModel.kind === 'fact' ? '认知' : '片段'}
                  {effectiveScope ? ` · ${scopeLabel(effectiveScope)}` : ''}
                </p>
                {!selectedFactRow && !selectedExperienceRow ? (
                  <p className="archive-location-note">这条记忆不在当前页，已读取完整记录。</p>
                ) : null}
                <h2 data-testid="archive-detail-title">{readingModel.primaryText}</h2>
                {readingModel.kind === 'fact' ? (
                  <FactReading model={readingModel} />
                ) : (
                  <ExperienceReading model={readingModel} />
                )}
                <div className="archive-governance">
                  {state.selection?.kind === 'fact' ? (
                    <>
                      <button
                        type="button"
                        data-testid="archive-edit-fact"
                        onClick={() => module.dispatch({ type: 'open-editor' })}
                      >
                        纠正文本
                      </button>
                      <MemorySelect
                        aria-label="主题归属"
                        data-testid="archive-assign-topic"
                        value={selectedTopicId}
                        onValueChange={(value) =>
                          module.dispatch({
                            type: 'assign-topic',
                            topicId: value || null,
                          })
                        }
                      >
                        <option value="">未归组</option>
                        {selectedTopicId && !state.topics.items.some((topic) => topic.topicId === selectedTopicId) ? (
                          <option value={selectedTopicId}>当前主题暂不可见</option>
                        ) : null}
                        {state.topics.items.map((topic) => (
                          <option key={topic.topicId} value={topic.topicId}>
                            {topic.displayLabel}
                          </option>
                        ))}
                      </MemorySelect>
                    </>
                  ) : null}
                  <button
                    type="button"
                    data-testid="archive-toggle-pin"
                    disabled={state.selection?.kind === 'experience' && !selectedExperienceRow}
                    title={state.selection?.kind === 'experience' && !selectedExperienceRow ? '需要在列表中确认这条片段的置顶状态' : undefined}
                    onClick={() => {
                      if (!state.selection || state.selection.kind === 'diary') return;
                      module.dispatch({
                        type: 'toggle-pin',
                        kind: state.selection.kind,
                        recordId: state.selection.recordId,
                      });
                    }}
                  >
                    {state.selection?.kind === 'experience' && !selectedExperienceRow ? '置顶状态未知' : (state.selection?.kind === 'fact'
                      ? selectedFactRow?.isPinned ?? (readingModel.kind === 'fact' && readingModel.isPinned)
                      : selectedExperienceRow?.isPinned)
                      ? '取消置顶'
                      : '置顶'}
                  </button>
                  <button
                    type="button"
                    data-testid="archive-delete-record"
                    onClick={() => module.dispatch({ type: 'request-delete' })}
                  >
                    删除
                  </button>
                </div>
              </motion.div>
            ) : null}

            {state.editor ? (
              <div className="archive-editor" data-testid="archive-editor">
                <label className="sr-only" htmlFor="archive-editor-input">纠正后的文本</label>
                <textarea
                  id="archive-editor-input"
                  data-testid="archive-editor-input"
                  value={state.editor.draft}
                  disabled={state.editor.status !== 'editing'}
                  onChange={(event) =>
                    module.dispatch({ type: 'edit-input', text: event.target.value })
                  }
                />
                {state.editor.status === 'conflict' && state.editor.conflict ? (
                  <div className="archive-conflict" role="alert" data-testid="archive-conflict">
                    <p>后端已有新版本（v{state.editor.conflict.serverVersion}）：</p>
                    <blockquote>{state.editor.conflict.serverText}</blockquote>
                    <p>你的草稿仍保留在本地。只能放弃草稿，或在查看新版本后重新应用。</p>
                    <button
                      type="button"
                      data-testid="archive-reapply-edit"
                      onClick={() => module.dispatch({ type: 'reapply-draft' })}
                    >
                      以新版本重新应用草稿
                    </button>
                  </div>
                ) : null}
                <div className="archive-editor-actions">
                  {state.editor.status === 'editing' ? (
                    <button
                      type="button"
                      data-testid="archive-save-edit"
                      onClick={() => module.dispatch({ type: 'save-edit' })}
                    >
                      保存修订
                    </button>
                  ) : null}
                  {state.editor.status === 'saving' ? <span>正在保存…</span> : null}
                  <button
                    type="button"
                    data-testid="archive-discard-edit"
                    onClick={() => module.dispatch({ type: 'discard-draft' })}
                  >
                    放弃草稿
                  </button>
                </div>
              </div>
            ) : null}

            {state.deleteFlow ? (
              <div
                ref={(node) => { node?.scrollIntoView?.({ block: 'nearest' }); }}
                className="archive-delete-confirm"
                role="alertdialog"
                aria-label="删除确认"
                data-testid="archive-delete-confirm"
              >
                {state.deleteFlow.status === 'confirming' ? (
                  <>
                    <p>
                      确认删除这条{state.deleteFlow.kind === 'fact' ? '认知' : state.deleteFlow.kind === 'experience' ? '片段' : '日记'}？
                    </p>
                    <p>「{state.deleteFlow.summary}」</p>
                    <p>范围：{state.deleteFlow.scopeLabel}</p>
                    <p>
                      {state.deleteFlow.kind === 'diary'
                        ? '正文会永久删除，无法撤销；她从下一轮起不会再想起这件事，也不会为这段对话重新写一篇。'
                        : '删除由后端执行内容清除，没有撤销。'}
                    </p>
                    <button
                      type="button"
                      data-testid="archive-delete-confirm-button"
                      onClick={() => module.dispatch({ type: 'confirm-delete' })}
                    >
                      确认删除
                    </button>
                    <button
                      type="button"
                      data-testid="archive-delete-cancel"
                      onClick={() => module.dispatch({ type: 'cancel-delete' })}
                    >
                      取消
                    </button>
                  </>
                ) : null}
                {state.deleteFlow.status === 'deleting' ? <p>正在删除…</p> : null}
                {state.deleteFlow.status === 'failed' ? (
                  <p role="alert">
                    删除失败，记录未变更。
                    <button
                      type="button"
                      data-testid="archive-delete-cancel"
                      onClick={() => module.dispatch({ type: 'cancel-delete' })}
                    >
                      关闭
                    </button>
                  </p>
                ) : null}
              </div>
            ) : null}
          </aside>
        ) : null}
      </div>

      <section
        className={`archive-pending${pendingOpen ? ' is-open' : ' is-collapsed'}`}
        data-testid="archive-pending"
        aria-label="待处理"
      >
        <div className="archive-pending-head">
          <h3>
            待处理
            <span className="archive-pending-count" data-testid="archive-pending-count">（{pendingCount}）</span>
          </h3>
          <button
            type="button"
            className="archive-pending-toggle"
            data-testid="archive-pending-toggle"
            aria-expanded={pendingOpen}
            aria-controls="archive-pending-body"
            onClick={() => setPendingOverride((prev) => !(prev ?? pendingHasContent))}
          >
            {pendingOpen ? '收起' : '展开'}
          </button>
        </div>
        {pendingOpen ? (
          <div className="archive-pending-body" id="archive-pending-body">
            <p className="archive-pending-hint">确认后会记入「认知」，忽略则不再保留。</p>
            {pendingHasAlert ? (
              <p role="alert" data-testid="archive-pending-partial">
                部分内容暂时不可用。
              </p>
            ) : null}
            {candidateObservations.length ? (
              <ul>
                {candidateObservations.map((item) => (
                  <li key={item.observationId}>
                    <span>候选观察：{item.displayText ?? '（内容不可显示）'}</span>
                    <button
                      type="button"
                      data-testid={`archive-confirm-${item.observationId}`}
                      onClick={() =>
                        module.dispatch({ type: 'confirm-observation', observationId: item.observationId })
                      }
                    >
                      确认
                    </button>
                    <button
                      type="button"
                      data-testid={`archive-ignore-${item.observationId}`}
                      onClick={() =>
                        module.dispatch({ type: 'ignore-observation', observationId: item.observationId })
                      }
                    >
                      忽略
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
            {state.pending.failedJobs.length ? (
              <ul>
                {state.pending.failedJobs.map((job) => (
                  <li key={job.jobId}>
                    <span>
                      失败任务（尝试 {job.attemptCount} 次）
                      {job.retryable ? '' : ' · 不可重试'}
                    </span>
                    {job.retryable ? (
                      <button
                        type="button"
                        data-testid={`archive-retry-${job.jobId}`}
                        onClick={() => module.dispatch({ type: 'retry-job', jobId: job.jobId })}
                      >
                        重试
                      </button>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : null}
            {!pendingHasAlert && !candidateObservations.length && !state.pending.failedJobs.length ? (
              <p>当前范围内没有待处理项。</p>
            ) : null}
          </div>
        ) : null}
      </section>

      <p className="archive-disclosure" data-testid="archive-disclosure">
        {PROVIDER_DISCLOSURE}
      </p>
    </motion.section>
  );
}
