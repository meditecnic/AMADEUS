import type React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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

/** The "…" menu is a Radix DropdownMenu: it opens on pointer-down or Enter/Space, not on a synthetic click. */
const openMenu = (trigger: HTMLElement) => fireEvent.keyDown(trigger, { key: 'Enter' });
/** Escape reaches Radix's dismiss layer through the document, like a real key press on the focused item. */
const pressEscape = (init: Record<string, unknown> = {}) =>
  fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape', ...init });

describe('HistorySidebar conversations', () => {
  it('ignores composition Escape in the menu and restores normal Escape focus without scrolling', async () => {
    render(<HistorySidebar isCollapsed={false} onToggle={vi.fn()} conversations={conversations}
      activeId="one" searchQuery="" onSearchChange={vi.fn()} onSelect={vi.fn()} onNew={vi.fn()}
      onRename={vi.fn()} onTogglePin={vi.fn()} onDelete={vi.fn()} onForget={vi.fn()} />);
    const trigger = screen.getByLabelText('会话操作 时间机器实验');
    openMenu(trigger);
    for (const guard of [{ isComposing: true }, { keyCode: 229 }]) {
      pressEscape(guard);
      expect(screen.getByRole('menu')).toBeInTheDocument();
    }
    const focus = vi.spyOn(trigger, 'focus');
    pressEscape();
    expect(screen.queryByRole('menu')).toBeNull();
    // Radix hands focus back once the menu's focus scope has unmounted (next task).
    await waitFor(() => expect(focus).toHaveBeenCalledWith({ preventScroll: true }));
  });

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
      openMenu(screen.getByLabelText('会话操作 β 世界线'));
      fireEvent.click(screen.getByLabelText(label));
    };
    menuAction('取消置顶 β 世界线');
    menuAction('删除 β 世界线');
    menuAction('清空 β 世界线');
    fireEvent.click(screen.getByText('新建会话'));

    expect(onSelect).toHaveBeenCalledWith(conversations[1]);
    expect(onTogglePin).toHaveBeenCalledWith(conversations[1]);
    // Rename has no dialog any more; it edits the row in place (covered below).
    expect(onRename).not.toHaveBeenCalled();
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
    openMenu(screen.getByLabelText('会话操作 时间机器实验'));
    fireEvent.click(screen.getByLabelText('删除 时间机器实验'));
    expect(onDelete).toHaveBeenCalledWith(conversations[0]);
  });

  const renderList = (overrides: Partial<React.ComponentProps<typeof HistorySidebar>> = {}) => render(
    <HistorySidebar
      isCollapsed={false} onToggle={vi.fn()} conversations={conversations}
      activeId="one" searchQuery="" onSearchChange={vi.fn()}
      onSelect={vi.fn()} onNew={vi.fn()} onRename={vi.fn()}
      onTogglePin={vi.fn()} onDelete={vi.fn()} onForget={vi.fn()}
      {...overrides}
    />,
  );

  it('renames in place: the menu opens the title as a field, Enter saves, focus returns to the row', async () => {
    const onRename = vi.fn().mockResolvedValue(undefined);
    renderList({ onRename });
    openMenu(screen.getByLabelText('会话操作 β 世界线'));
    fireEvent.click(screen.getByLabelText('重命名 β 世界线'));
    const field = await screen.findByRole('textbox', { name: '会话标题' });
    expect(field).toHaveValue('β 世界线');
    expect(field).toHaveFocus();
    expect(screen.queryByRole('dialog')).toBeNull();

    // A composing Enter belongs to the IME, not to the field.
    fireEvent.change(field, { target: { value: '世界线变动率' } });
    fireEvent.keyDown(field, { key: 'Enter', isComposing: true });
    expect(onRename).not.toHaveBeenCalled();

    fireEvent.keyDown(field, { key: 'Enter' });
    await waitFor(() => expect(screen.queryByRole('textbox', { name: '会话标题' })).toBeNull());
    expect(onRename).toHaveBeenCalledWith(conversations[1], '世界线变动率');
    expect(screen.getByText('β 世界线').closest('button')).toHaveFocus();
  });

  it('Esc leaves the title untouched; a failed save keeps the field open with the reason', async () => {
    const onRename = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(undefined);
    renderList({ onRename });
    openMenu(screen.getByLabelText('会话操作 时间机器实验'));
    fireEvent.click(screen.getByLabelText('重命名 时间机器实验'));
    let field = await screen.findByRole('textbox', { name: '会话标题' });
    fireEvent.change(field, { target: { value: '不会保存' } });
    fireEvent.keyDown(field, { key: 'Escape' });
    expect(screen.queryByRole('textbox', { name: '会话标题' })).toBeNull();
    expect(onRename).not.toHaveBeenCalled();

    openMenu(screen.getByLabelText('会话操作 时间机器实验'));
    fireEvent.click(screen.getByLabelText('重命名 时间机器实验'));
    field = await screen.findByRole('textbox', { name: '会话标题' });
    fireEvent.change(field, { target: { value: '跃迁记录' } });
    await act(async () => { fireEvent.keyDown(field, { key: 'Enter' }); });
    expect(await screen.findByRole('alert')).toHaveTextContent('重命名失败，请重试。');
    expect(field).toHaveAttribute('aria-invalid', 'true');
    fireEvent.keyDown(field, { key: 'Enter' });
    await waitFor(() => expect(screen.queryByRole('textbox', { name: '会话标题' })).toBeNull());
    expect(onRename).toHaveBeenLastCalledWith(conversations[0], '跃迁记录');
  });

  it('right-clicking a row opens the same session actions at the pointer', () => {
    const onTogglePin = vi.fn();
    const onDelete = vi.fn();
    renderList({ onTogglePin, onDelete });
    const row = screen.getByText('时间机器实验').closest('article')!;
    fireEvent.contextMenu(row, { clientX: 40, clientY: 30 });
    expect(screen.getByRole('menu')).toBeInTheDocument();
    expect(row).toHaveAttribute('data-state', 'open');
    fireEvent.click(screen.getByRole('menuitem', { name: '置顶 时间机器实验' }));
    expect(onTogglePin).toHaveBeenCalledWith(conversations[0]);
    expect(screen.queryByRole('menu')).toBeNull();
    fireEvent.contextMenu(row, { clientX: 40, clientY: 30 });
    fireEvent.click(screen.getByRole('menuitem', { name: '删除 时间机器实验' }));
    expect(onDelete).toHaveBeenCalledWith(conversations[0]);
  });

  it('clears the search from its button or Esc, and says when nothing matches', () => {
    const onSearchChange = vi.fn();
    const { rerender } = renderList({ onSearchChange });
    expect(screen.queryByRole('button', { name: '清除搜索' })).toBeNull();
    rerender(
      <HistorySidebar
        isCollapsed={false} onToggle={vi.fn()} conversations={conversations}
        activeId="one" searchQuery="不存在" onSearchChange={onSearchChange}
        onSelect={vi.fn()} onNew={vi.fn()} onRename={vi.fn()}
        onTogglePin={vi.fn()} onDelete={vi.fn()} onForget={vi.fn()}
      />,
    );
    expect(screen.getByText(/没有匹配的会话/)).toBeInTheDocument();
    expect(screen.queryByText(/暂无会话/)).toBeNull();
    const search = screen.getByLabelText('搜索会话');
    fireEvent.keyDown(search, { key: 'Escape', isComposing: true });
    expect(onSearchChange).not.toHaveBeenCalled();
    fireEvent.keyDown(search, { key: 'Escape' });
    expect(onSearchChange).toHaveBeenLastCalledWith('');
    onSearchChange.mockClear();
    fireEvent.click(screen.getByRole('button', { name: '清除搜索' }));
    expect(onSearchChange).toHaveBeenCalledWith('');
    expect(search).toHaveFocus();
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
