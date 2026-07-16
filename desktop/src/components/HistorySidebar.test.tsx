import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { HistorySidebar, type ConversationSummary } from './HistorySidebar';

const conversations: ConversationSummary[] = [
  {
    id: 'one', title: '时间机器实验', title_source: 'auto', is_default: true,
    is_pinned: false, provider_id: 'deepseek', model_id: 'deepseek-v4-flash', last_active_at: '1',
  },
  {
    id: 'two', title: 'β 世界线', title_source: 'manual', is_default: false,
    is_pinned: true, provider_id: 'openai', model_id: 'gpt-5.6-luna', last_active_at: '2',
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
    fireEvent.click(screen.getByLabelText('取消置顶 β 世界线'));
    fireEvent.click(screen.getByLabelText('重命名 β 世界线'));
    fireEvent.click(screen.getByLabelText('删除 β 世界线'));
    fireEvent.click(screen.getByLabelText('清空 β 世界线'));
    fireEvent.click(screen.getByText('新建会话'));

    expect(onSelect).toHaveBeenCalledWith(conversations[1]);
    expect(onTogglePin).toHaveBeenCalledWith(conversations[1]);
    expect(onRename).toHaveBeenCalledWith(conversations[1]);
    expect(onDelete).toHaveBeenCalledWith(conversations[1]);
    expect(onForget).toHaveBeenCalledWith(conversations[1]);
    expect(onNew).toHaveBeenCalled();
    expect(screen.queryByText('时间机器实验')).not.toBeInTheDocument();
  });
});
