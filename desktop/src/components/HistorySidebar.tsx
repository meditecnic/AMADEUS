import React, { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
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

/** Rename in place: Enter or leaving the field saves, Esc restores. A failed save keeps the field open with the error. */
function RenameField({ conversation, onCommit, onDone }: {
  conversation: ConversationSummary;
  onCommit: (title: string) => Promise<void> | void;
  onDone: () => void;
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
    if (!title || title === conversation.title) { settled.current = true; onDone(); return; }
    setBusy(true);
    setError(null);
    try {
      await onCommit(title);
      settled.current = true;
      onDone();
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
            else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); settled.current = true; onDone(); }
          }}
          onBlur={(event) => {
            // Its own text menu takes focus for a moment; that is not leaving the field.
            if ((event.relatedTarget as Element | null)?.closest?.('[data-ws2-overlay]')) return;
            void commit();
          }}
        />
      </TextFieldMenu>
      {error
        ? <span id={errorId} className="conversation-rename-error" role="alert">{error}</span>
        : <span className="conversation-meta" aria-hidden="true">Enter 保存 · Esc 取消</span>}
    </div>
  );
}

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
  const layoutKey = useId();
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const renameAfterClose = useRef<ConversationSummary | null>(null);
  const lastEdited = useRef<string | null>(null);
  // A menu asked for rename: start editing only after it has closed, so its focus return cannot steal the field.
  const actions: RowActions = {
    requestRename: (c) => { renameAfterClose.current = c; },
    flushRename: () => {
      const target = renameAfterClose.current;
      if (!target) return false;
      renameAfterClose.current = null;
      setEditingId(target.id);
      return true;
    },
    onTogglePin,
    onDelete,
    onForget,
  };
  useEffect(() => {
    if (editingId) { lastEdited.current = editingId; return; }
    const id = lastEdited.current;
    lastEdited.current = null;
    if (!id) return;
    const row = listRef.current?.querySelector<HTMLElement>(`[data-conversation-id="${CSS.escape(id)}"] .conversation-select`);
    if (row && !row.contains(document.activeElement)) row.focus({ preventScroll: true });
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
                className={`history-sidebar-icon ${conversation.id === activeId ? 'active' : ''}${conversation.is_pinned ? ' pinned' : ''}`}
                aria-label={conversation.title}
                onClick={() => onSelect(conversation)}
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
            ) : visible.map((conversation, index) => {
              const active = conversation.id === activeId;
              const editing = conversation.id === editingId;
              return (
                <RowContextMenu key={conversation.id} conversation={conversation} actions={actions}>
                  <motion.article
                    layout="position"
                    data-conversation-id={conversation.id}
                    style={{ '--i': index } as React.CSSProperties}
                    className={`conversation-item ${active ? 'active' : ''}${editing ? ' editing' : ''}`}
                  >
                    {/* The selection plate travels between rows instead of repainting in place. */}
                    {active && <motion.span layoutId={`${layoutKey}-active`} className="conversation-active-plate" aria-hidden="true" />}
                    {editing ? (
                      <RenameField
                        conversation={conversation}
                        onCommit={(title) => onRename(conversation, title)}
                        onDone={() => setEditingId(null)}
                      />
                    ) : (
                      <Tip label={conversation.title} side="right" align="start" truncated=".conversation-title">
                        <button
                          type="button"
                          className="conversation-select"
                          onClick={() => onSelect(conversation)}
                          disabled={busy}
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
            })}
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
