import React, { memo, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import * as ContextMenu from '@radix-ui/react-context-menu';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { Ellipsis, X } from 'lucide-react';
import { motion } from 'motion/react';
import { Tip, useOverlayRoot } from '../workstation/Tip';
import { SessionMenuItems, TextFieldMenu, keepImeEscape } from '../workstation/menus';
import {
  type IdentityMode,
  normalizeIdentityMode,
} from './SettingsModal';

export interface ConversationSummary {
  id: string;
  title: string;
  title_source: 'auto' | 'manual';
  is_default: boolean;
  is_pinned: boolean;
  is_selected?: boolean;
  provider_id?: string | null;
  model_id?: string | null;
  /** Q20-A / Q21: set at create; immutable for the conversation lifetime. */
  identity_mode?: IdentityMode;
  last_active_at: string;
}

type RowProps = {
  conversation: ConversationSummary;
  index: number;
  active: boolean;
  editing: boolean;
  actions: RowActions;
  onSelect: (conversation: ConversationSummary) => void;
  onRename: (conversation: ConversationSummary, title: string) => Promise<void> | void;
  onRenameDone: (refocus: boolean) => void;
};

const sameRow = (a: ConversationSummary, b: ConversationSummary) => a === b || (
  a.id === b.id && a.title === b.title && a.title_source === b.title_source && a.is_default === b.is_default
  && a.is_pinned === b.is_pinned && a.identity_mode === b.identity_mode && a.last_active_at === b.last_active_at
  && a.model_id === b.model_id && a.provider_id === b.provider_id
);

interface HistorySidebarProps {
  isCollapsed: boolean;
  onToggle: () => void;
  conversations: ConversationSummary[];
  activeId: string | null;
  searchQuery: string;
  onSearchChange: (query: string) => void;
  onSelect: (conversation: ConversationSummary) => void;
  onNew: () => void;
  /** Inline rename commits here; a rejection keeps the row in edit with an error. */
  onRename: (conversation: ConversationSummary, title: string) => Promise<void> | void;
  onTogglePin: (conversation: ConversationSummary) => void;
  onDelete: (conversation: ConversationSummary) => void;
  onForget: (conversation: ConversationSummary) => void;
  busy?: boolean;
  loadFailed?: boolean;
  refreshFailed?: boolean;
  onRetry?: () => void;
}

/** Today → 21:04, this year → 9/26, older → 2025/9/26. */
function shortWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  if (d.toDateString() === now.toDateString()) return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return d.getFullYear() === now.getFullYear() ? `${d.getMonth() + 1}/${d.getDate()}` : `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}

function modeBadgeLabel(mode: IdentityMode | undefined): string {
  return normalizeIdentityMode(mode) === 'self' ? 'SELF' : 'OKABE';
}

type RowActions = {
  /** Remember the row; editing starts once the menu has closed (see `flushRename`). */
  requestRename: (c: ConversationSummary) => void;
  /** On menu close: true when a rename was waiting and the row is now editing (focus belongs to its field). */
  flushRename: () => boolean;
  onTogglePin: (c: ConversationSummary) => void;
  onDelete: (c: ConversationSummary) => void;
  onForget: (c: ConversationSummary) => void;
};

/** The "…" button: pin, rename, then the two erasing actions below a divider (Radix DropdownMenu). */
function ConversationMenu({ conversation, actions }: {
  conversation: ConversationSummary;
  actions: RowActions;
}) {
  const container = useOverlayRoot();
  const triggerRef = useRef<HTMLButtonElement>(null);
  return (
    <div className="conversation-actions">
      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild>
          <button ref={triggerRef} type="button" className="conversation-more" aria-label={`会话操作 ${conversation.title}`}>
            <Ellipsis size={14} strokeWidth={2} aria-hidden="true" />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal container={container}>
          <DropdownMenu.Content
            className="ws2-menu conversation-menu"
            data-ws2-overlay=""
            align="end"
            sideOffset={4}
            onEscapeKeyDown={keepImeEscape}
            onCloseAutoFocus={(event) => {
              // Back to "…" without scrolling the list — unless the row went into rename, whose field has focus.
              event.preventDefault();
              if (!actions.flushRename()) triggerRef.current?.focus({ preventScroll: true });
            }}
          >
            <SessionMenuItems
              M={DropdownMenu}
              conversation={conversation}
              onTogglePin={actions.onTogglePin}
              onRename={actions.requestRename}
              onForget={actions.onForget}
              onDelete={actions.onDelete}
            />
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    </div>
  );
}

/** Right-click on a row opens the same list at the pointer, at once and without animation. While it is open the
 *  row keeps its hover look (data-state=open) and the rest of the list ignores the pointer (modal). */
function RowContextMenu({ conversation, actions, children }: {
  conversation: ConversationSummary;
  actions: RowActions;
  children: React.ReactElement;
}) {
  const container = useOverlayRoot();
  return (
    <ContextMenu.Root>
      <ContextMenu.Trigger asChild>{children}</ContextMenu.Trigger>
      <ContextMenu.Portal container={container}>
        <ContextMenu.Content
          className="ws2-menu conversation-menu is-pointer"
          data-ws2-overlay=""
          onEscapeKeyDown={keepImeEscape}
          onCloseAutoFocus={(event) => { if (actions.flushRename()) event.preventDefault(); }}
        >
          <SessionMenuItems
            M={ContextMenu}
            conversation={conversation}
            onTogglePin={actions.onTogglePin}
            onRename={actions.requestRename}
            onForget={actions.onForget}
            onDelete={actions.onDelete}
          />
        </ContextMenu.Content>
      </ContextMenu.Portal>
    </ContextMenu.Root>
  );
}

/** Rename in place: only Enter saves. Esc or clicking elsewhere restores the old title. A failed save keeps the
 *  field open with the error. `onDone(true)` hands focus back to the row; after a click elsewhere it stays there. */
function RenameField({ conversation, onCommit, onDone }: {
  conversation: ConversationSummary;
  onCommit: (title: string) => Promise<void> | void;
  onDone: (refocus: boolean) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const errorId = useId();
  const [value, setValue] = useState(conversation.title);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const settled = useRef(false);
  useLayoutEffect(() => {
    ref.current?.focus({ preventScroll: true });
    ref.current?.select();
  }, []);
  const commit = async () => {
    if (busy || settled.current) return;
    const title = value.trim();
    if (!title || title === conversation.title) { settled.current = true; onDone(true); return; }
    setBusy(true);
    setError(null);
    try {
      await onCommit(title);
      settled.current = true;
      onDone(true);
    } catch {
      setBusy(false);
      setError('重命名失败，请重试。');
    }
  };
  return (
    <div className="conversation-select is-editing">
      <TextFieldMenu fieldRef={ref}>
        <input
          ref={ref}
          className="conversation-rename"
          aria-label="会话标题"
          value={value}
          maxLength={80}
          readOnly={busy}
          spellCheck={false}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          onChange={(event) => { setValue(event.target.value); setError(null); }}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing || event.keyCode === 229) {
              if (event.key === 'Enter' || event.key === 'Escape') event.stopPropagation();
              return;
            }
            if (event.key === 'Enter') { event.preventDefault(); void commit(); }
            else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); settled.current = true; onDone(true); }
          }}
          onBlur={(event) => {
            // Its own text menu takes focus for a moment, and switching windows comes back here: neither is leaving.
            if ((event.relatedTarget as Element | null)?.closest?.('[data-ws2-overlay]') || !document.hasFocus()) return;
            // A save already on its way finishes; otherwise clicking elsewhere restores the old title.
            if (busy || settled.current) return;
            settled.current = true;
            onDone(false);
          }}
        />
      </TextFieldMenu>
      {error
        ? <span id={errorId} className="conversation-rename-error" role="alert">{error}</span>
        : <span className="conversation-meta" aria-hidden="true">Enter 保存 · Esc 取消</span>}
    </div>
  );
}

/** One row. Memoised on its own data, so the many renders of a conversation switch leave the list alone. */
const ConversationRow = memo(function ConversationRow({
  conversation, index, active, editing, actions, onSelect, onRename, onRenameDone,
}: RowProps) {
  return (
    <RowContextMenu conversation={conversation} actions={actions}>
      <motion.article
        layout="position"
        data-conversation-id={conversation.id}
        style={{ '--i': index } as React.CSSProperties}
        className={`conversation-item${active ? ' active' : ''}${conversation.is_pinned ? ' pinned' : ''}${editing ? ' editing' : ''}`}
      >
        {/* The selection plate travels between rows instead of repainting in place (see the sidebar's travel effect). */}
        {active && <span className="conversation-active-plate" aria-hidden="true" />}
        {editing ? (
          <RenameField
            conversation={conversation}
            onCommit={(title) => onRename(conversation, title)}
            onDone={onRenameDone}
          />
        ) : (
          <Tip label={conversation.title} side="right" align="start" truncated=".conversation-title">
            <button
              type="button"
              className="conversation-select"
              onClick={() => onSelect(conversation)}
            >
              <span className="conversation-title">
                {conversation.is_pinned && <span className="conversation-pin" aria-label="已置顶">◆ </span>}
                {conversation.title}
              </span>
              <span className="conversation-meta">
                <span
                  className={`identity-mode-badge mode-${normalizeIdentityMode(conversation.identity_mode)}`}
                  data-testid={`identity-badge-${conversation.id}`}
                >
                  {modeBadgeLabel(conversation.identity_mode)}
                </span>
                {' · '}
                {/* When and with which model — never the raw provider id (custom ids are opaque UUIDs). */}
                <time dateTime={conversation.last_active_at}>{shortWhen(conversation.last_active_at)}</time>
                {conversation.model_id ? ` · ${conversation.model_id}` : ''}
              </span>
            </button>
          </Tip>
        )}
        {!editing && <ConversationMenu conversation={conversation} actions={actions} />}
      </motion.article>
    </RowContextMenu>
  );
}, (a, b) => a.index === b.index && a.active === b.active && a.editing === b.editing
  && a.actions === b.actions && a.onSelect === b.onSelect && a.onRename === b.onRename && a.onRenameDone === b.onRenameDone
  && sameRow(a.conversation, b.conversation));

export const HistorySidebar: React.FC<HistorySidebarProps> = ({
  isCollapsed,
  onToggle,
  conversations,
  activeId,
  searchQuery,
  onSearchChange,
  onSelect,
  onNew,
  onRename,
  onTogglePin,
  onDelete,
  onForget,
  busy = false,
  loadFailed = false,
  refreshFailed = false,
  onRetry,
}) => {
  const visible = conversations.filter((conversation) =>
    conversation.title.toLocaleLowerCase().includes(searchQuery.trim().toLocaleLowerCase())
  );
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const renameAfterClose = useRef<ConversationSummary | null>(null);
  const lastEdited = useRef<string | null>(null);
  const refocusAfterEdit = useRef(false);
  // The row a click asked for. The plate goes there at once; the transcript follows when the switch lands.
  const [pendingId, setPendingId] = useState<string | null>(null);
  const shownActiveId = pendingId ?? activeId;
  const latest = useRef({ onSelect, onRename, onTogglePin, onDelete, onForget, busy, activeId });
  latest.current = { onSelect, onRename, onTogglePin, onDelete, onForget, busy, activeId };
  // Stable across renders, so memoised rows stay put while the workstation works through a switch.
  const actions = useMemo<RowActions>(() => ({
    // A menu asked for rename: start editing only after it has closed, so its focus return cannot steal the field.
    requestRename: (c) => { renameAfterClose.current = c; },
    flushRename: () => {
      const target = renameAfterClose.current;
      if (!target) return false;
      renameAfterClose.current = null;
      setEditingId(target.id);
      return true;
    },
    onTogglePin: (c) => latest.current.onTogglePin(c),
    onDelete: (c) => latest.current.onDelete(c),
    onForget: (c) => latest.current.onForget(c),
  }), []);
  // A click lets the plate start moving in the next frame; the switch, whose first workstation render is the heavy
  // one, is asked for right after that frame has painted.
  const queuedSelect = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const select = useCallback((conversation: ConversationSummary) => {
    const { busy: switching, activeId: current, onSelect: request } = latest.current;
    if (switching || queuedSelect.current) return;
    if (conversation.id === current) { request(conversation); return; }
    setPendingId(conversation.id);
    queuedSelect.current = true;
    requestAnimationFrame(() => {
      window.setTimeout(() => {
        queuedSelect.current = false;
        if (mounted.current) latest.current.onSelect(conversation);
      }, 0);
    });
  }, []);
  const rename = useCallback((conversation: ConversationSummary, title: string) => latest.current.onRename(conversation, title), []);
  const renameDone = useCallback((refocus: boolean) => { refocusAfterEdit.current = refocus; setEditingId(null); }, []);
  // The plate travels from the row it was on as a compositor animation, so the switch's renders cannot stall it.
  const plateWasOn = useRef(shownActiveId);
  useLayoutEffect(() => {
    const from = plateWasOn.current;
    plateWasOn.current = shownActiveId;
    const list = listRef.current;
    if (!list || !from || !shownActiveId || from === shownActiveId) return;
    const rows = Array.from(list.querySelectorAll<HTMLElement>('[data-conversation-id]'));
    const fromRow = rows.find((row) => row.dataset.conversationId === from);
    const toRow = rows.find((row) => row.dataset.conversationId === shownActiveId);
    const plate = toRow?.querySelector<HTMLElement>(':scope > .conversation-active-plate');
    if (!fromRow || !toRow || !plate || typeof plate.animate !== 'function') return;
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    const dy = fromRow.offsetTop - toRow.offsetTop;
    if (!dy) return;
    const beta = list.closest('[data-wl]')?.getAttribute('data-wl') === 'beta';
    plate.animate(
      [{ transform: `translateY(${dy}px)` }, { transform: 'translateY(0)' }],
      { duration: beta ? 260 : 200, easing: beta ? 'cubic-bezier(.5, 0, .15, 1.12)' : 'cubic-bezier(.2, 0, 0, 1)' },
    );
  }, [shownActiveId]);
  // The switch landed (activeId moved) or ended without moving (busy dropped): the plate follows the truth again.
  const wasBusy = useRef(busy);
  useEffect(() => { setPendingId(null); }, [activeId]);
  useEffect(() => {
    if (wasBusy.current && !busy) setPendingId(null);
    wasBusy.current = busy;
  }, [busy]);
  useEffect(() => {
    if (editingId) { lastEdited.current = editingId; return; }
    const id = lastEdited.current;
    lastEdited.current = null;
    const refocus = refocusAfterEdit.current;
    refocusAfterEdit.current = false;
    // Enter / Esc return focus to the row; a click elsewhere keeps it where it went.
    if (!id || !refocus) return;
    const row = listRef.current?.querySelector<HTMLElement>(`[data-conversation-id="${CSS.escape(id)}"] .conversation-select`);
    const focused = document.activeElement;
    if (row && (!focused || focused === document.body)) row.focus({ preventScroll: true });
  }, [editingId]);

  return (
    <aside className={`grid-history ${isCollapsed ? 'collapsed' : ''}`}>
      <div className="history-sidebar-header">
        {!isCollapsed && <span className="history-sidebar-title">CONVERSATIONS</span>}
        <button
          type="button"
          className="history-sidebar-toggle"
          onClick={onToggle}
          aria-label={isCollapsed ? '展开会话列表' : '收起会话列表'}
        >
          {isCollapsed ? '▶' : '◀'}
        </button>
      </div>

      {isCollapsed ? (
        <div className="history-sidebar-icons">
          <Tip label="新建会话" side="right">
            <button type="button" className="history-sidebar-icon add" onClick={onNew} aria-label="新建会话" disabled={busy}>
              <span className="history-icon-plus">+</span>
            </button>
          </Tip>
          {visible.slice(0, 8).map((conversation, index) => (
            <Tip key={conversation.id} label={conversation.title} side="right">
              <button
                type="button"
                style={{ '--i': index } as React.CSSProperties}
                className={`history-sidebar-icon ${conversation.id === shownActiveId ? 'active' : ''}${conversation.is_pinned ? ' pinned' : ''}`}
                aria-label={conversation.title}
                onClick={() => select(conversation)}
              >
                {conversation.title.slice(0, 1).toUpperCase()}
              </button>
            </Tip>
          ))}
        </div>
      ) : (
        <>
          <div className={`conversation-search-wrap${searchQuery ? ' has-query' : ''}`}>
            <TextFieldMenu fieldRef={searchRef}>
              <input
                ref={searchRef}
                className="conversation-search"
                value={searchQuery}
                onChange={(event) => onSearchChange(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key !== 'Escape' || !searchQuery || event.nativeEvent.isComposing || event.keyCode === 229) return;
                  event.preventDefault();
                  event.stopPropagation();
                  onSearchChange('');
                }}
                placeholder="搜索会话"
                aria-label="搜索会话"
                spellCheck={false}
              />
            </TextFieldMenu>
            {searchQuery && (
              <button
                type="button"
                className="conversation-search-clear"
                aria-label="清除搜索"
                onClick={() => { onSearchChange(''); searchRef.current?.focus({ preventScroll: true }); }}
              >
                <X size={13} strokeWidth={1.75} aria-hidden="true" />
              </button>
            )}
          </div>
          <motion.div className="history-session-list" ref={listRef} layoutScroll>
            {refreshFailed && (
              <div className="list-refresh-error" role="status">
                <span>刷新失败</span>
                <button type="button" className="history-retry" onClick={onRetry} disabled={busy}>重试</button>
              </div>
            )}
            {loadFailed ? (
              <div className="history-empty-tip" role="status">
                <span>&gt; 会话列表加载失败</span>
                <button type="button" className="history-retry" onClick={onRetry} disabled={busy}>重试</button>
              </div>
            ) : visible.length === 0 ? (
              <div className="history-empty-tip">
                <span>&gt; {searchQuery.trim() ? '没有匹配的会话' : '暂无会话'}</span>
                {!searchQuery.trim() && <p className="history-empty-sub">下一次发言会在这条世界线开始新会话。</p>}
              </div>
            ) : visible.map((conversation, index) => (
                <ConversationRow
                  key={conversation.id}
                  conversation={conversation}
                  index={index}
                  active={conversation.id === shownActiveId}
                  editing={conversation.id === editingId}
                  actions={actions}
                  onSelect={select}
                  onRename={rename}
                  onRenameDone={renameDone}
                />
            ))}
          </motion.div>
          <div className="history-sidebar-footer">
            <button type="button" className="history-new-btn" onClick={onNew} disabled={busy}>
              <span className="history-new-btn-icon">+</span>
              <span className="history-new-btn-text">新建会话</span>
            </button>
          </div>
        </>
      )}
    </aside>
  );
};
