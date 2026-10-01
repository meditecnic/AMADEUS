import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { HistorySidebar, type ConversationSummary } from './HistorySidebar';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const conversations: ConversationSummary[] = [
  {
    id: 'one', title: '时间机器实验', title_source: 'auto', is_default: true,
    is_pinned: false, provider_id: 'deepseek', model_id: 'deepseek-v4-flash',
    identity_mode: 'okabe', last_active_at: '1',
  },
  {
    id: 'two', title: 'β 世界线', title_source: 'manual', is_default: false,
    is_pinned: true, provider_id: 'openai', model_id: 'gpt-5.6-luna',
    identity_mode: 'self', last_active_at: '2',
  },
];

describe('HistorySidebar conversations', () => {
  it('filters and routes conversation actions', () => {
    const onSearchChange = vi.fn();
    const onSelect = vi.fn();
    const onNew = vi.fn();
    const onRename = vi.fn();
    const onTogglePin = vi.fn();
    const onDelete = vi.fn();
    const onForget = vi.fn();
    const { rerender } = render(
      <HistorySidebar
        isCollapsed={false} onToggle={vi.fn()} conversations={conversations}
        activeId="one" searchQuery="" onSearchChange={onSearchChange}
        onSelect={onSelect} onNew={onNew} onRename={onRename}
        onTogglePin={onTogglePin} onDelete={onDelete}
        onForget={onForget}
      />
    );

    fireEvent.change(screen.getByLabelText('搜索会话'), { target: { value: 'β' } });
    expect(onSearchChange).toHaveBeenCalledWith('β');
    rerender(
      <HistorySidebar
        isCollapsed={false} onToggle={vi.fn()} conversations={conversations}
        activeId="one" searchQuery="β" onSearchChange={onSearchChange}
        onSelect={onSelect} onNew={onNew} onRename={onRename}
        onTogglePin={onTogglePin} onDelete={onDelete}
        onForget={onForget}
      />
    );
    fireEvent.click(screen.getByText('β 世界线'));
    const menuAction = (label: string) => {
      fireEvent.click(screen.getByLabelText('会话操作 β 世界线'));
      fireEvent.click(screen.getByLabelText(label));
    };
    menuAction('取消置顶 β 世界线');
    menuAction('重命名 β 世界线');
    menuAction('删除 β 世界线');
    menuAction('清空 β 世界线');
    fireEvent.click(screen.getByText('新建会话'));

    expect(onSelect).toHaveBeenCalledWith(conversations[1]);
    expect(onTogglePin).toHaveBeenCalledWith(conversations[1]);
    expect(onRename).toHaveBeenCalledWith(conversations[1]);
    expect(onDelete).toHaveBeenCalledWith(conversations[1]);
    expect(onForget).toHaveBeenCalledWith(conversations[1]);
    expect(onNew).toHaveBeenCalled();
    expect(screen.queryByText('时间机器实验')).not.toBeInTheDocument();
  });

  it('offers delete for the initial conversation through the same menu', () => {
    const onDelete = vi.fn();
    render(
      <HistorySidebar
        isCollapsed={false} onToggle={vi.fn()} conversations={conversations}
        activeId="one" searchQuery="" onSearchChange={vi.fn()}
        onSelect={vi.fn()} onNew={vi.fn()} onRename={vi.fn()}
        onTogglePin={vi.fn()} onDelete={onDelete} onForget={vi.fn()}
      />,
    );
    expect(screen.queryByLabelText('删除 时间机器实验')).toBeNull();
    fireEvent.click(screen.getByLabelText('会话操作 时间机器实验'));
    fireEvent.click(screen.getByLabelText('删除 时间机器实验'));
    expect(onDelete).toHaveBeenCalledWith(conversations[0]);
  });

  it('shows identity mode badges and has no alternate-mode or in-session switch control', () => {
    const onNew = vi.fn();
    render(
      <HistorySidebar
        isCollapsed={false}
        onToggle={vi.fn()}
        conversations={conversations}
        activeId="one"
        searchQuery=""
        onSearchChange={vi.fn()}
        onSelect={vi.fn()}
        onNew={onNew}
        onRename={vi.fn()}
        onTogglePin={vi.fn()}
        onDelete={vi.fn()}
        onForget={vi.fn()}
      />,
    );

    expect(screen.getByTestId('identity-badge-one')).toHaveTextContent('OKABE');
    expect(screen.getByTestId('identity-badge-two')).toHaveTextContent('SELF');
    // No control to mutate the active conversation's mode in place (Q21).
    expect(screen.queryByTestId('identity-mode-switch-active')).toBeNull();
    expect(screen.queryByLabelText(/切换当前会话身份/)).toBeNull();
    // Product: only one "新建会话" (inherits settings default); no alternate-mode entry.
    expect(screen.queryByTestId('new-conversation-alternate-mode')).toBeNull();
    expect(screen.queryByText(/以 Self 新建/)).toBeNull();
    fireEvent.click(screen.getByText('新建会话'));
    expect(onNew).toHaveBeenCalled();
  });
});
