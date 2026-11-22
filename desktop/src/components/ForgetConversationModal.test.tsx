import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ForgetConversationModal } from './ForgetConversationModal';

afterEach(cleanup);

describe('ForgetConversationModal', () => {
  it('keeps long-term memory by default and requires an explicit opt-in', () => {
    const onConfirm = vi.fn();
    const { rerender } = render(
      <ForgetConversationModal
        title="时间机器实验"
        busy={false}
        onClose={vi.fn()}
        onConfirm={onConfirm}
      />
    );

    expect(screen.getByTestId('forget-diary-note')).toHaveTextContent(
      '她已写下的手记会保留；若勾选上方选项，这段对话的手记也会一并删除。',
    );
    fireEvent.click(screen.getByRole('button', { name: '清空会话内容' }));
    expect(onConfirm).toHaveBeenLastCalledWith(false);

    rerender(
      <ForgetConversationModal
        title="时间机器实验"
        busy={false}
        onClose={vi.fn()}
        onConfirm={onConfirm}
      />
    );
    expect(screen.getByText('你手动确认、修改或置顶的记忆，以及其他对话也提过的事会保留。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: /^同时忘掉只在这段对话里提过的事/ }));
    expect(screen.getByTestId('forget-diary-note')).toHaveTextContent(
      '这段对话对应的手记也会一并删除，无法恢复。',
    );
    fireEvent.click(screen.getByRole('button', { name: '清空会话内容' }));
    expect(onConfirm).toHaveBeenLastCalledWith(true);
  });
});
