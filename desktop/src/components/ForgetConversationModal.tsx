import React, { useState } from 'react';

import { ConfirmDialog } from '../workstation/ConfirmDialog';
import { Stamp } from '../workstation/Stamp';

interface ForgetConversationModalProps {
  title: string;
  /** The conversation's number in the list (No.07), for the stamp. */
  no?: string;
  busy: boolean;
  error?: string | null;
  onClose: () => void;
  onConfirm: (forgetLongTerm: boolean) => void;
}

export const ForgetConversationModal: React.FC<ForgetConversationModalProps> = ({
  title,
  no,
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
      stamp={<Stamp sub={no}>不可撤回</Stamp>}
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
        />
        <span>
          {/* Same wording as the delete dialog's option in Workstation. */}
          同时忘掉只在这段对话里提过的事
          <small>你手动确认、修改或置顶的记忆，以及其他对话也提过的事会保留。</small>
        </span>
      </label>
      <p className="ws2-dialog-hint" data-testid="forget-diary-note">
        {forgetLongTerm
          ? '这段对话对应的日记也会一并删除，无法恢复。'
          : '她已写下的日记会保留；若勾选上方选项，这段对话的日记也会一并删除。'}
      </p>
    </ConfirmDialog>
  );
};

export default ForgetConversationModal;
