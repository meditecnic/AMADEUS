import React, { useState } from 'react';

interface ForgetConversationModalProps {
  title: string;
  busy: boolean;
  onClose: () => void;
  onConfirm: (forgetLongTerm: boolean) => void;
}

export const ForgetConversationModal: React.FC<ForgetConversationModalProps> = ({
  title,
  busy,
  onClose,
  onConfirm,
}) => {
  const [forgetLongTerm, setForgetLongTerm] = useState(false);

  return (
    <div className="salieri-modal-overlay conversation-forget-overlay">
      <div className="conversation-forget-modal" role="dialog" aria-modal="true" aria-labelledby="forget-title">
        <div className="modal-header">
          <h2 id="forget-title">FORGET CONVERSATION</h2>
          <button type="button" className="close-btn" onClick={onClose} disabled={busy}>×</button>
        </div>
        <p>将清空“{title}”中的消息与摘要，但保留会话本身。</p>
        <label className="forget-memory-option">
          <input
            type="checkbox"
            checked={forgetLongTerm}
            disabled={busy}
            onChange={(event) => setForgetLongTerm(event.target.checked)}
            aria-label="同时遗忘该会话独占的长期记忆证据"
          />
          <span>
            同时遗忘该会话独占的长期记忆证据
            <small>被其他会话共同引用的证据不会删除。</small>
          </span>
        </label>
        <div className="modal-footer">
          <button type="button" className="salieri-btn cancel-btn" onClick={onClose} disabled={busy}>取消</button>
          <button
            type="button"
            className="salieri-btn save-btn"
            disabled={busy}
            onClick={() => onConfirm(forgetLongTerm)}
          >
            清空会话内容
          </button>
        </div>
      </div>
    </div>
  );
};

export default ForgetConversationModal;
