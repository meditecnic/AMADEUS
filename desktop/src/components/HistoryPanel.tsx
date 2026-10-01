import React from 'react';

interface HistoryItem {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  translation?: string;
}

interface HistoryPanelProps {
  isOpen: boolean;
  onClose: () => void;
  messages: HistoryItem[];
}

export const HistoryPanel: React.FC<HistoryPanelProps> = ({
  isOpen,
  onClose,
  messages,
}) => {
  if (!isOpen) return null;

  const historyItems = messages.filter((m) => m.role === 'user' || m.role === 'assistant');

  return (
    <div className="history-panel-overlay" onClick={onClose}>
      <div className="history-panel-sidebar" onClick={(e) => e.stopPropagation()}>
        <div className="history-panel-header">
          <span className="history-panel-title">SESSION HISTORY</span>
          <button className="history-panel-close" onClick={onClose}>×</button>
        </div>

        <div className="history-panel-body">
          {historyItems.length === 0 ? (
            <div className="history-empty-tip">
              <span className="blink-txt">&gt; 无历史记录</span>
              <p className="placeholder-note">当前会话尚未产生对话。</p>
            </div>
          ) : (
            <div className="history-list">
              {historyItems.map((item) => (
                <div key={item.id} className={`history-item ${item.role}`}>
                  <div className="history-item-role">
                    {item.role === 'user' ? 'OKABE' : 'KURISU'}
                  </div>
                  <div className="history-item-preview">
                    {item.translation || item.content}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default HistoryPanel;
