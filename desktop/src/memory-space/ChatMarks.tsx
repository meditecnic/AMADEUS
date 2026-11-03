import { useId, useState, type KeyboardEvent } from 'react';

import { md } from './chartFormat';
import { parseMs } from './chartTime';
import { recordedGroups, type MemoryChange, type MemoryRefs } from './chatMarkers';
import './ChatMarks.css';

/** What a mark asks the memory space to show. `origin` is the mark that gets focus back on the way out. */
export type MemoryOpenRequest = {
  origin: string;
  select: string[];
  mark: string[];
  hintAt: number | null;
};

type OpenHandler = (request: MemoryOpenRequest) => void;

export const markKey = (messageId: string, part: 'created' | 'updated' | 'refs') => `${messageId}:${part}`;

/** Under the user's line: what the ingestion job wrote from it. */
export function RecordedMarks({ messageId, changes, onOpen }: { messageId: string; changes: MemoryChange[] | undefined; onOpen: OpenHandler }) {
  const { created, updated } = recordedGroups(changes);
  if (!created.length && !updated.length) return null;
  const mark = (part: 'created' | 'updated', rows: MemoryChange[], verb: string) => {
    const ids = rows.map((row) => row.id);
    return (
      <button
        key={part}
        type="button"
        className="ws2-mm ws2-mm-recorded"
        data-testid={`memory-mark-${part}`}
        data-memory-mark={markKey(messageId, part)}
        aria-label={`这句话${verb}了 ${rows.length} 条记忆，在星图中看`}
        onClick={() => onOpen({ origin: markKey(messageId, part), select: ids, mark: ids.slice(1), hintAt: null })}
      >
        <i aria-hidden="true" />
        <span>{verb}了 {rows.length} 条 ↗</span>
      </button>
    );
  };
  return (
    <div className="ws2-mm-row" data-testid="memory-marks-recorded">
      {created.length ? mark('created', created, '记下') : null}
      {updated.length ? mark('updated', updated, '更新') : null}
    </div>
  );
}

const KIND_NAME = { fact: '认知', experience: '片段' } as const;

/** Under her reply: the records put in front of her this turn. "Referenced", never "used". */
export function ReferencedMarks({ messageId, refs, onOpen }: { messageId: string; refs: MemoryRefs; onOpen: OpenHandler }) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  const count = refs.items.length;
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key !== 'Escape' || !open) return;
    event.stopPropagation();
    setOpen(false);
  };
  return (
    <div className="ws2-mm-row ws2-mm-refs" data-testid="memory-marks-referenced" onKeyDown={onKeyDown}>
      <button
        type="button"
        className="ws2-mm ws2-mm-ref"
        data-testid="memory-mark-refs"
        data-memory-mark={markKey(messageId, 'refs')}
        aria-expanded={open}
        aria-controls={listId}
        aria-label={`这一轮参照了 ${count} 条记忆，${open ? '收起' : '展开'}列表`}
        onClick={() => setOpen((value) => !value)}
      >
        <i aria-hidden="true" />
        <span>参照 {count} 条 {open ? '▴' : '▾'}</span>
      </button>
      {open ? (
        <div id={listId} className="ws2-mm-list" data-testid="memory-refs-list">
          <ul>
            {refs.items.map((item) => (
              <li key={`${item.kind}:${item.id}`}>
                <span className="kind">{KIND_NAME[item.kind]}</span>
                <span className="label">{item.label}</span>
                <span className="date">{md(parseMs(item.createdAt))}</span>
              </li>
            ))}
          </ul>
          <button
            type="button"
            className="ws2-mm-open"
            data-testid="memory-refs-open"
            onClick={() => onOpen({ origin: markKey(messageId, 'refs'), select: [], mark: refs.items.map((item) => item.id), hintAt: refs.at })}
          >
            在星图中看这一轮参照的记忆 ↗
          </button>
        </div>
      ) : null}
    </div>
  );
}
