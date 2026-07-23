import React from 'react';

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
  identity_mode?: 'okabe' | 'self';
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
          {visible.slice(0, 8).map((conversation) => (
            <button
              type="button"
              key={conversation.id}
              className={`history-sidebar-icon ${conversation.id === activeId ? 'active' : ''}`}
              title={conversation.title}
              onClick={() => onSelect(conversation)}
            >
              {conversation.is_pinned ? '★' : conversation.title.slice(0, 1).toUpperCase()}
            </button>
          ))}
          <button type="button" className="history-sidebar-icon add" onClick={onNew} aria-label="新建会话">
            <span className="history-icon-plus">+</span>
          </button>
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
                <p className="history-empty-sub">新建一条世界线内的独立会话。</p>
              </div>
            ) : visible.map((conversation) => (
              <article
                key={conversation.id}
                className={`conversation-item ${conversation.id === activeId ? 'active' : ''}`}
              >
                <button
                  type="button"
                  className="conversation-select"
                  onClick={() => onSelect(conversation)}
                  disabled={busy}
                >
                  <span className="conversation-title">
                    {conversation.is_pinned && <span aria-hidden="true">★ </span>}
                    {conversation.title}
                  </span>
                  <span className="conversation-meta">
                    {(conversation.provider_id || 'deepseek').toUpperCase()} · {conversation.model_id || 'DEFAULT'}
                  </span>
                </button>
                <div className="conversation-actions">
                  <button type="button" onClick={() => onTogglePin(conversation)} aria-label={`${conversation.is_pinned ? '取消置顶' : '置顶'} ${conversation.title}`}>
                    {conversation.is_pinned ? '☆' : '★'}
                  </button>
                  <button type="button" onClick={() => onRename(conversation)} aria-label={`重命名 ${conversation.title}`}>✎</button>
                  <button type="button" onClick={() => onForget(conversation)} aria-label={`清空 ${conversation.title}`}>⌫</button>
                  {!conversation.is_default && (
                    <button type="button" onClick={() => onDelete(conversation)} aria-label={`删除 ${conversation.title}`}>×</button>
                  )}
                </div>
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
