import React, { useState } from 'react';

import { ConfirmDialog } from '../workstation/ConfirmDialog';

interface ForgetConversationModalProps {
  title: string;
  busy: boolean;
  error?: string | null;
  onClose: () => void;
  onConfirm: (forgetLongTerm: boolean) => void;
}

export const ForgetConversationModal: React.FC<ForgetConversationModalProps> = ({
  title,
  busy,
  error,
  onClose,
  onConfirm,
}) => {
  const [forgetLongTerm, setForgetLongTerm] = useState(false);

  return (
    <ConfirmDialog
      title="清空聊天记录？"
      confirmLabel="清空会话内容"
      danger
      busy={busy}
      error={error}
      onCancel={onClose}
      onConfirm={() => onConfirm(forgetLongTerm)}
    >
      <p>将清空“{title}”中的消息与摘要，但保留会话本身。</p>
      <label className="ws2-dialog-check">
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
    </ConfirmDialog>
  );
};

export default ForgetConversationModal;
