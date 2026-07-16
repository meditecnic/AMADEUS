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
    fireEvent.click(screen.getByLabelText('同时遗忘该会话独占的长期记忆证据'));
    fireEvent.click(screen.getByRole('button', { name: '清空会话内容' }));
    expect(onConfirm).toHaveBeenLastCalledWith(true);
  });
});
