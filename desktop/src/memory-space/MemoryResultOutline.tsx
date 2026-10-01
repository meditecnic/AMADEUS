import { useEffect, useId, useMemo, useRef, useState, type Ref } from 'react';
import { motion, useReducedMotion } from 'motion/react';

import { neighborId } from './semanticNavigator';
import {
  UNGROUPED_GROUP_ID,
  accessibleNameById,
  visibleOutlineIds,
  type OverviewDisclosurePlan,
  type PrimaryResultRow,
  type SupportingRow,
} from './overviewPresentation';

const KIND_ZH: Record<string, string> = {
  topic: '主题',
  fact: '事实',
  experience: '经历',
  continuity_hub: 'SOUL',
};

export function MemoryResultOutline(props: {
  plan: OverviewDisclosurePlan;
  selectedId: string | null;
  focusedId: string | null;
  onSelect: (id: string) => void;
  onRove: (nextId: string) => void;
  onOwnsFocus?: (owns: boolean) => void;
  listRef?: Ref<HTMLDivElement>;
  reducedMotion?: boolean;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const selectionId = useId();
  const systemReducedMotion = useReducedMotion();
  const reducedMotion = props.reducedMotion ?? systemReducedMotion;
  const dense = props.plan.primaryResults.length >= 8;
  const [browse, setBrowse] = useState<'ranked' | 'grouped'>(dense ? 'grouped' : 'ranked');
  const groupedPrimary = new Set(props.plan.topicGroups.flatMap((group) => group.childResultIds));
  const ungroupedPrimary = props.plan.primaryResults.filter((row) => !groupedPrimary.has(row.id));
  const [openGroups, setOpenGroups] = useState<string[]>(() => {
    const open = props.plan.topicGroups
      .filter((group) => group.childResultIds.length > 0 && (group.expanded || group.childResultIds.length <= 3))
      .map((group) => group.topicProjectionId);
    if (ungroupedPrimary.length) open.push(UNGROUPED_GROUP_ID);
    return open;
  });
  const visibleIds = useMemo(
    () => visibleOutlineIds(props.plan, browse, openGroups),
    [browse, openGroups, props.plan],
  );
  const names = accessibleNameById(props.plan);
  const ids = visibleIds;
  const roving = (props.focusedId && ids.includes(props.focusedId))
    ? props.focusedId
    : (props.selectedId && ids.includes(props.selectedId) ? props.selectedId : (ids[0] ?? null));

  useEffect(() => {
    setBrowse(props.plan.primaryResults.length >= 8 ? 'grouped' : 'ranked');
  }, [props.plan.primaryResults.length]);

  useEffect(() => {
    if (!roving) return;
    const root = rootRef.current;
    const active = document.activeElement;
    if (root && active && root.contains(active) && active.getAttribute('role') === 'option') {
      root.querySelector<HTMLElement>(`[data-outline-id="${roving}"]`)?.focus();
    }
  }, [roving]);

  const moveVisible = (fromId: string, key: 'ArrowDown' | 'ArrowUp') => {
    const next = neighborId(visibleIds, fromId, key === 'ArrowDown' ? 1 : -1);
    if (!next || next === fromId) return;
    props.onRove(next);
  };

  const renderPrimary = (row: PrimaryResultRow) => {
    const selected = props.selectedId === row.id;
    return (
      <li key={row.id}>
        <button
          type="button"
          role="option"
          data-result-id={row.id}
          data-outline-id={row.id}
          data-rank={row.rank}
          data-kind={row.kind}
          aria-label={names.get(row.id) ?? row.label}
          aria-selected={selected}
          className={selected ? 'is-selected' : undefined}
          tabIndex={roving === row.id ? 0 : -1}
          onFocus={() => props.onOwnsFocus?.(true)}
          onClick={() => props.onSelect(row.id)}
          onKeyDown={(event) => {
            if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
            event.preventDefault();
            moveVisible(row.id, event.key);
          }}
        >
          <span className="memory-outline-meta" aria-hidden="true">
            {selected ? <motion.span className="memory-result-highlight" aria-hidden="true" layoutId={selectionId}
              transition={reducedMotion ? { duration: 0 } : { type: 'spring', stiffness: 350, damping: 35 }} /> : null}
            <span className="memory-result-number">{String(row.rank).padStart(2, '0')}</span>
            <span>{KIND_ZH[row.kind] ?? row.kind}</span>
          </span>
          <span className="memory-outline-label">{row.label}</span>
        </button>
      </li>
    );
  };

  const renderSupport = (row: SupportingRow) => {
    const selected = props.selectedId === row.id;
    return (
      <li key={row.id}>
        <button
          type="button"
          role="option"
          data-support-id={row.id}
          data-outline-id={row.id}
          data-context="true"
          aria-label={names.get(row.id) ?? `上下文 ${row.label}`}
          aria-selected={selected}
          className={selected ? 'is-selected' : undefined}
          tabIndex={roving === row.id ? 0 : -1}
          onFocus={() => props.onOwnsFocus?.(true)}
          onClick={() => props.onSelect(row.id)}
          onKeyDown={(event) => {
            if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
            event.preventDefault();
            moveVisible(row.id, event.key);
          }}
        >
          <span className="memory-outline-meta" aria-hidden="true">上下文</span>
          <span className="memory-outline-label">{row.label}</span>
        </button>
      </li>
    );
  };

  const byId = new Map(props.plan.primaryResults.map((row) => [row.id, row]));

  const toggleGroup = (id: string, open: boolean) => {
    setOpenGroups((current) => {
      if (open) return current.includes(id) ? current : [...current, id];
      return current.filter((item) => item !== id);
    });
  };

  return (
    <div
      className="memory-result-outline"
      data-testid="memory-list"
      data-browse={browse}
      ref={(node) => {
        rootRef.current = node;
        if (typeof props.listRef === 'function') props.listRef(node);
        else if (props.listRef) (props.listRef as { current: HTMLDivElement | null }).current = node;
      }}
      onFocus={() => props.onOwnsFocus?.(true)}
    >
      <div className="memory-outline-browse">
        <button type="button" aria-pressed={browse === 'ranked'} onClick={() => setBrowse('ranked')}>按排序</button>
        <button type="button" aria-pressed={browse === 'grouped'} onClick={() => setBrowse('grouped')}>按主题</button>
      </div>
      {browse === 'grouped' ? <p className="memory-outline-meta">按主题分组，组内保留结果顺序</p> : null}
      <section data-region="primary">
        <h3>结果 {props.plan.backendTruncation.shown.results}</h3>
        {browse === 'ranked' ? (
          <ol role="listbox" aria-label="记忆结果" tabIndex={-1}>
            {props.plan.primaryResults.map(renderPrimary)}
          </ol>
        ) : (
          <div>
            {props.plan.topicGroups.filter((group) => group.childResultIds.length > 0).map((group) => (
              <details
                key={group.topicProjectionId}
                data-region="group"
                open={openGroups.includes(group.topicProjectionId)}
                onToggle={(event) => toggleGroup(group.topicProjectionId, event.currentTarget.open)}
              >
                <summary>
                  {group.label} · {group.childResultIds.length} 条
                </summary>
                <ol role="listbox" aria-label={`${group.label} 结果`}>
                  {group.childResultIds.map((id) => {
                    const row = byId.get(id);
                    return row ? renderPrimary(row) : null;
                  })}
                </ol>
              </details>
            ))}
            {ungroupedPrimary.length ? (
              <details
                data-region="group"
                open={openGroups.includes(UNGROUPED_GROUP_ID)}
                onToggle={(event) => toggleGroup(UNGROUPED_GROUP_ID, event.currentTarget.open)}
              >
                <summary>未归组 · {ungroupedPrimary.length} 条</summary>
                <ol role="listbox" aria-label="未归组结果">
                  {ungroupedPrimary.map(renderPrimary)}
                </ol>
              </details>
            ) : null}
          </div>
        )}
      </section>
      <section data-region="support">
        <h3>上下文</h3>
        <ol role="listbox" aria-label="记忆上下文" tabIndex={-1}>
          {props.plan.supportingContext.map(renderSupport)}
        </ol>
      </section>
    </div>
  );
}
