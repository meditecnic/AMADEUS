import React, { useEffect, useRef, useState } from 'react';
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
  onRename: (conversation: ConversationSummary) => void;
  onTogglePin: (conversation: ConversationSummary) => void;
  onDelete: (conversation: ConversationSummary) => void;
  onForget: (conversation: ConversationSummary) => void;
  busy?: boolean;
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

/** One entry per conversation: pin, rename, then the two erasing actions below a divider. */
function ConversationMenu({ conversation, onRename, onTogglePin, onDelete, onForget }: {
  conversation: ConversationSummary;
  onRename: (c: ConversationSummary) => void;
  onTogglePin: (c: ConversationSummary) => void;
  onDelete: (c: ConversationSummary) => void;
  onForget: (c: ConversationSummary) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.isComposing) return;
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey, true);
    };
  }, [open]);
  const run = (action: (c: ConversationSummary) => void) => () => {
    setOpen(false);
    action(conversation);
  };
  return (
    <div className="conversation-actions" ref={rootRef}>
      <button
        type="button"
        ref={triggerRef}
        className="conversation-more"
        aria-label={`会话操作 ${conversation.title}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
          <circle cx="3.5" cy="8" r="1.2" fill="currentColor" /><circle cx="8" cy="8" r="1.2" fill="currentColor" /><circle cx="12.5" cy="8" r="1.2" fill="currentColor" />
        </svg>
      </button>
      {open && (
        <div className="conversation-menu" role="menu">
          <button type="button" role="menuitem" onClick={run(onTogglePin)} aria-label={`${conversation.is_pinned ? '取消置顶' : '置顶'} ${conversation.title}`}>
            {conversation.is_pinned ? '取消置顶' : '置顶'}
          </button>
          <button type="button" role="menuitem" onClick={run(onRename)} aria-label={`重命名 ${conversation.title}`}>重命名</button>
          <div className="conversation-menu-sep" role="separator" />
          <button type="button" role="menuitem" onClick={run(onForget)} aria-label={`清空 ${conversation.title}`}>清空聊天记录…</button>
          <button type="button" role="menuitem" className="danger" onClick={run(onDelete)} aria-label={`删除 ${conversation.title}`}>删除会话…</button>
        </div>
      )}
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
}) => {
  const visible = conversations.filter((conversation) =>
    conversation.title.toLocaleLowerCase().includes(searchQuery.trim().toLocaleLowerCase())
  );

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
          <button type="button" className="history-sidebar-icon add" onClick={onNew} aria-label="新建会话" disabled={busy}>
            <span className="history-icon-plus">+</span>
          </button>
          {visible.slice(0, 8).map((conversation, index) => (
            <button
              type="button"
              key={conversation.id}
              style={{ '--i': index } as React.CSSProperties}
              className={`history-sidebar-icon ${conversation.id === activeId ? 'active' : ''}${conversation.is_pinned ? ' pinned' : ''}`}
              title={conversation.title}
              aria-label={conversation.title}
              onClick={() => onSelect(conversation)}
            >
              {conversation.title.slice(0, 1).toUpperCase()}
            </button>
          ))}
        </div>
      ) : (
        <>
          <div className="conversation-search-wrap">
            <input
              className="conversation-search"
              value={searchQuery}
              onChange={(event) => onSearchChange(event.target.value)}
              placeholder="搜索会话"
              aria-label="搜索会话"
            />
          </div>
          <div className="history-session-list">
            {visible.length === 0 ? (
              <div className="history-empty-tip">
                <span>&gt; 暂无会话</span>
                <p className="history-empty-sub">下一次发言会在这条世界线开始新会话。</p>
              </div>
            ) : visible.map((conversation, index) => (
              <article
                key={conversation.id}
                style={{ '--i': index } as React.CSSProperties}
                className={`conversation-item ${conversation.id === activeId ? 'active' : ''}`}
              >
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
                <ConversationMenu
                  conversation={conversation}
                  onRename={onRename}
                  onTogglePin={onTogglePin}
                  onDelete={onDelete}
                  onForget={onForget}
                />
              </article>
            ))}
          </div>
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

export default HistorySidebar;
