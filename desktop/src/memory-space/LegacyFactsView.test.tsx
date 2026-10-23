import { createRef } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { LegacyFactsView } from './LegacyFactsView';
import type { ArchiveScope } from './archiveApi';

const scope: ArchiveScope = { sessionId: 'owner & session', worldline: 'steins_gate', identityMode: 'self' };
const row = { id: 7, fact_key: 'drink', fact_value: '喜欢红茶' };
const emptyLedger = () => ({ local: [], shared: [], okabe: [] });
const json = (status: number, body: unknown) => ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;

function props(currentScope = scope) {
  return { scope: currentScope, archive: true, visible: true, searchRef: createRef<HTMLInputElement>(),
    onCloseFind: vi.fn(), onIdentity: vi.fn(), onWorldline: vi.fn() };
}

function mockLedger(ledger: () => unknown, remove: (url: URL) => Promise<Response> = async () => json(200, { deleted: true })) {
  const fetcher = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost');
    return init?.method === 'POST' ? remove(url) : Promise.resolve(json(200, ledger()));
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}

async function openDelete(text = row.fact_value) {
  const button = await screen.findByRole('button', { name: `删除：${text}` });
  button.focus();
  fireEvent.click(button);
  return screen.getByRole('alertdialog', { name: '删除这条记忆？' });
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('legacy memory deletion', () => {
  it.each([
    ['local', 'steins_gate'], ['shared', 'steins_gate'], ['okabe', 'steins_gate'],
    ['local', 'beta'], ['shared', 'beta'], ['okabe', 'beta'],
  ] as const)('confirms %s deletion in %s, calls its scoped endpoint and refreshes', async (source, worldline) => {
    let deleted = false;
    const currentScope: ArchiveScope = { ...scope, worldline, identityMode: source === 'okabe' ? 'okabe' : 'self' };
    const fetcher = mockLedger(() => ({ ...emptyLedger(), [source]: deleted ? [] : [row] }), async (url) => {
      expect(url.pathname).toBe(source === 'shared' ? '/api/memory/shared-facts/7/delete'
        : `/api/memory/facts/7/${source === 'local' ? 'delete-local' : 'dismiss'}`);
      expect(url.searchParams.get('session_id')).toBe(scope.sessionId);
      expect(url.searchParams.get('worldline')).toBe(worldline);
      expect(url.searchParams.has('identity_mode')).toBe(false);
      deleted = true;
      return json(200, { deleted: true });
    });
    const viewProps = props(currentScope);
    const { container } = render(<div className="memory-space"><LegacyFactsView {...viewProps} /></div>);
    const dialog = await openDelete();
    // The shared dialog is a sibling of the scrolling panel, so its absolute scrim covers Memory Space.
    expect(dialog.closest('.ws2-dialog-scrim')?.parentElement).toBe(container.firstElementChild);
    expect(within(dialog).getAllByRole('paragraph')[0]).toHaveTextContent(row.fact_value);
    expect(within(dialog).getByText('聊天记录不会删除。')).toBeInTheDocument();
    const wl = worldline === 'beta' ? 'β' : 'SG';
    const impact = source === 'shared' ? '这条由 SG 与 β 共用。删除后，两条世界线的 USER 对话都不再使用。'
      : `删除后，她在 ${source === 'okabe' ? 'OKABE' : 'USER'} · ${wl} 的对话里不再使用这条。`;
    expect(within(dialog).getByText(impact)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: '取消' })).toHaveFocus();
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
    expect(screen.queryByText('SG 与 β 共用')).toBe(source === 'shared' ? screen.getByText('SG 与 β 共用') : null);
    fireEvent.click(within(dialog).getByRole('button', { name: '删除', exact: true }));
    await screen.findByText('已删除。');
    await waitFor(() => expect(screen.queryByRole('button', { name: `删除：${row.fact_value}` })).toBeNull());
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(viewProps.searchRef.current).toHaveFocus();
    expect(fetcher.mock.calls.filter(([, init]) => init?.method !== 'POST')).toHaveLength(2);
  });

  it('keeps the local duplicate visible, warns about the shared fallback and reveals it after deletion', async () => {
    let deleted = false;
    mockLedger(() => ({ local: deleted ? [] : [row], shared: [{ ...row, id: 8, fact_value: '共用的绿茶' }], okabe: [] }), async () => {
      deleted = true;
      return json(200, { deleted: true });
    });
    render(<LegacyFactsView {...props()} />);
    const dialog = await openDelete();
    expect(screen.queryByText('共用的绿茶')).toBeNull();
    expect(screen.queryByText('SG 与 β 共用')).toBeNull();
    expect(within(dialog).getByText('SG 与 β 共用的记忆里也有这一项，删除后会显示出来，可以再删。')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: '删除', exact: true }));
    await screen.findByRole('button', { name: '删除：共用的绿茶' });
    expect(screen.getByText('SG 与 β 共用')).toBeInTheDocument();
    expect(screen.queryByText(row.fact_value)).toBeNull();
  });

  it.each(['取消', 'Escape'])('cancels with %s and restores focus without submitting', async (action) => {
    const fetcher = mockLedger(() => ({ ...emptyLedger(), local: [row] }));
    render(<LegacyFactsView {...props()} />);
    const dialog = await openDelete();
    if (action === 'Escape') fireEvent.keyDown(window, { key: 'Escape' });
    else fireEvent.click(within(dialog).getByRole('button', { name: '取消' }));
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(screen.getByRole('button', { name: `删除：${row.fact_value}` })).toHaveFocus();
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
  });

  it.each(['http', 'transport'])('shows the approved failure message for %s errors and allows retry', async (failure) => {
    let attempts = 0;
    let deleted = false;
    const fetcher = mockLedger(() => ({ ...emptyLedger(), local: deleted ? [] : [row] }), async () => {
      attempts += 1;
      if (attempts === 1) {
        if (failure === 'transport') throw new Error('offline');
        return json(500, { detail: 'private backend error' });
      }
      deleted = true;
      return json(200, { deleted: true });
    });
    render(<LegacyFactsView {...props()} />);
    const dialog = await openDelete();
    fireEvent.click(within(dialog).getByRole('button', { name: '删除', exact: true }));
    expect(await screen.findByRole('alert')).toHaveTextContent('删除失败，请稍后重试。');
    expect(screen.queryByText('private backend error')).toBeNull();
    expect(fetcher.mock.calls.filter(([, init]) => init?.method !== 'POST')).toHaveLength(1);
    fireEvent.click(within(dialog).getByRole('button', { name: '删除', exact: true }));
    await screen.findByText('已删除。');
    expect(attempts).toBe(2);
  });

  it('treats 404 as already deleted and announces it after refreshing', async () => {
    let deleted = false;
    mockLedger(() => ({ ...emptyLedger(), local: deleted ? [] : [row] }), async () => {
      deleted = true;
      return json(404, { detail: 'fact_not_found' });
    });
    render(<LegacyFactsView {...props()} />);
    fireEvent.click(within(await openDelete()).getByRole('button', { name: '删除', exact: true }));
    await screen.findByText('这条记忆已经不在了，列表已刷新。');
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(screen.queryByText(row.fact_value)).toBeNull();
    expect(screen.queryByText('删除失败，请稍后重试。')).toBeNull();
  });

  it('blocks repeated confirmation while a delete is pending', async () => {
    let release!: (response: Response) => void;
    const remove = vi.fn(() => new Promise<Response>((resolve) => { release = resolve; }));
    mockLedger(() => ({ ...emptyLedger(), local: [row] }), remove);
    render(<LegacyFactsView {...props()} />);
    const dialog = await openDelete();
    const confirm = within(dialog).getByRole('button', { name: '删除', exact: true });
    act(() => { fireEvent.click(confirm); fireEvent.click(confirm); });
    fireEvent.click(within(dialog).getByRole('button', { name: '处理中…' }));
    expect(remove).toHaveBeenCalledTimes(1);
    expect(within(dialog).getByRole('button', { name: '取消' })).toBeDisabled();
    expect(within(dialog).getByRole('button', { name: '处理中…' })).toBeDisabled();
    await act(async () => { release(json(200, { deleted: true })); });
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it.each([
    { ...scope, identityMode: 'okabe' as const },
    { ...scope, worldline: 'beta' as const },
    { ...scope, sessionId: 'another owner' },
  ])('invalidates an open confirmation after switching to %j, including a round trip', async (nextScope) => {
    const fetcher = mockLedger(() => ({ local: [row], shared: [], okabe: [row] }));
    const viewProps = props();
    const view = render(<LegacyFactsView {...viewProps} />);
    await openDelete();
    view.rerender(<LegacyFactsView {...viewProps} scope={nextScope} />);
    expect(screen.queryByRole('alertdialog')).toBeNull();
    view.rerender(<LegacyFactsView {...viewProps} />);
    await screen.findByRole('button', { name: `删除：${row.fact_value}` });
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
  });

  it('ignores a late delete response after the scope changes', async () => {
    let release!: (response: Response) => void;
    mockLedger(() => ({ ...emptyLedger(), local: [row] }), () => new Promise<Response>((resolve) => { release = resolve; }));
    const viewProps = props();
    const view = render(<LegacyFactsView {...viewProps} />);
    fireEvent.click(within(await openDelete()).getByRole('button', { name: '删除', exact: true }));
    view.rerender(<LegacyFactsView {...viewProps} scope={{ ...scope, worldline: 'beta' }} />);
    await act(async () => { release(json(500, { detail: 'late failure' })); });
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(screen.queryByText('删除失败，请稍后重试。')).toBeNull();
    expect(screen.queryByText('已删除。')).toBeNull();
    expect(screen.getByRole('button', { name: `删除：${row.fact_value}` })).toBeEnabled();
  });

  it('offers the same deletion flow from Find', async () => {
    mockLedger(() => ({ ...emptyLedger(), local: [row] }));
    render(<LegacyFactsView {...props()} archive={false} />);
    expect(screen.getByTestId('memory-space-find')).toBeInTheDocument();
    await openDelete();
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
  });

  it('reports a ledger read failure and reloads on retry', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(json(500, {}))
      .mockResolvedValue(json(200, { ...emptyLedger(), local: [row] }));
    vi.stubGlobal('fetch', fetcher);
    render(<LegacyFactsView {...props()} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('记忆暂时无法读取。');
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    await screen.findByRole('button', { name: `删除：${row.fact_value}` });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
