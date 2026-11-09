import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DataPanel } from './DataPanel';

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const BACKUPS = [
  { id: '20261108T224512000000Z-manual', time: '2026-11-08T22:45:12+00:00', reason: 'manual', size: 18_874_368, app_version: '0.1.0', can_restore: true },
  { id: '20261024T030000000000Z-weekly', time: null, reason: 'weekly', size: 0, app_version: null, can_restore: false, detail: 'Backup validation failed' },
];

let calls: Array<{ url: string; method: string; body?: { current: string | null; new: string | null } }>;

beforeEach(() => {
  calls = [];
  let rows = [...BACKUPS];
  let passcodeSet = false;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    if (url === '/api/auth/state') return json(200, { passcode_set: passcodeSet });
    if (url === '/api/auth/passcode' && method === 'PUT') { passcodeSet = body.new !== null; return json(200, { status: 'success' }); }
    if (url === '/api/data/status') return json(200, { status: 'ok', detail: null, backups: rows });
    if (url.endsWith('/restore') && method === 'POST') return json(200, { restart_required: true });
    if (url.startsWith('/api/data/backups/') && method === 'DELETE') {
      rows = rows.filter((row) => !url.endsWith(row.id));
      return json(200, { status: 'success' });
    }
    // a backend before report 212: no location routes
    if (url === '/api/data/location' || url === '/api/data/disk') return json(404, { detail: 'Not Found' });
    throw new Error(`unexpected fetch: ${url}`);
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const mount = () => render(<div className="ws2"><DataPanel /></div>);

describe('DataPanel', () => {
  it('asks for a new passcode twice, sends nothing on a mismatch, and says when it is set', async () => {
    mount();
    fireEvent.click(await screen.findByRole('button', { name: '设口令' }));
    const form = screen.getByRole('form', { name: '设口令' });
    fireEvent.change(within(form).getByLabelText('新口令'), { target: { value: 'lab-member' } });
    fireEvent.change(within(form).getByLabelText('再输一次'), { target: { value: 'lab-membr' } });
    fireEvent.click(within(form).getByRole('button', { name: '保存口令' }));
    expect(await within(form).findByRole('alert')).toHaveTextContent('两次输入的不一样');
    expect(calls.some((call) => call.url === '/api/auth/passcode')).toBe(false);

    fireEvent.change(within(form).getByLabelText('再输一次'), { target: { value: 'lab-member' } });
    fireEvent.click(within(form).getByRole('button', { name: '保存口令' }));
    expect(await screen.findByText('口令已设好，下次打开时会问。')).toBeInTheDocument();
    expect(calls.find((call) => call.url === '/api/auth/passcode')?.body).toEqual({ current: null, new: 'lab-member' });
    expect(screen.getByRole('button', { name: '改口令' })).toBeInTheDocument();
  });

  it('deletes a backup only through the stamped slip', async () => {
    mount();
    const list = await screen.findByRole('list', { name: '备份' });
    fireEvent.click(within(list).getAllByRole('button', { name: /^删除 .+ 的备份$/ })[1]);
    const dialog = screen.getByRole('alertdialog', { name: '删除这份备份？' });
    expect(dialog).toHaveTextContent('不可撤回');
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false);

    fireEvent.click(within(dialog).getByRole('button', { name: '删除' }));
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull());
    expect(calls.find((call) => call.method === 'DELETE')?.url).toBe('/api/data/backups/20261024T030000000000Z-weekly');
    expect(within(list).getAllByRole('listitem')).toHaveLength(1);
  });

  it('schedules a restore for the next start and leaves out the location the backend cannot tell', async () => {
    mount();
    fireEvent.click(await screen.findByRole('button', { name: /^恢复到 .+ 的备份$/ }));
    fireEvent.click(within(screen.getByRole('alertdialog', { name: '恢复到这份备份？' })).getByRole('button', { name: '安排恢复' }));
    expect(await screen.findByText(/^已安排：下次打开 AMADEUS 时/)).toBeInTheDocument();
    expect(screen.getByText('待恢复')).toBeInTheDocument();
    expect(calls.find((call) => call.url.endsWith('/restore'))?.url).toBe('/api/data/backups/20261108T224512000000Z-manual/restore');
    expect(screen.queryByText('数据位置')).toBeNull();
  });
});
