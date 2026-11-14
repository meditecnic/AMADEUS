import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const shellMock = vi.hoisted(() => ({
  inShell: false,
  pickPath: vi.fn<(kind: 'directory' | 'zip') => Promise<string | null>>(async () => null),
  restartApp: vi.fn<() => Promise<void>>(async () => undefined),
  revealInFolder: vi.fn<(path: string) => Promise<void>>(async () => undefined),
  saveBlob: vi.fn<(blob: Blob, name: string) => Promise<void>>(async () => undefined),
}));

vi.mock('./shell', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./shell')>();
  return {
    ...actual,
    get inShell() {
      return shellMock.inShell;
    },
    pickPath: shellMock.pickPath,
    restartApp: shellMock.restartApp,
    revealInFolder: shellMock.revealInFolder,
    saveBlob: shellMock.saveBlob,
  };
});

import { AboutPanel } from './AboutPanel';
import { DataPanel } from './DataPanel';

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const BACKUPS = [
  { id: '20261108T224512000000Z-manual', time: '2026-11-08T22:45:12+00:00', reason: 'manual', size: 18_874_368, app_version: '0.1.0', can_restore: true },
  { id: '20261024T030000000000Z-weekly', time: null, reason: 'weekly', size: 0, app_version: null, can_restore: false, detail: 'Backup validation failed' },
];

type CallRecord = { url: string; method: string; body?: any; hasBody: boolean };

let calls: CallRecord[];
let locationHandler: ((url: string, method: string, body: any) => Promise<Response> | Response) | null;

beforeEach(() => {
  calls = [];
  locationHandler = null;
  shellMock.inShell = false;
  shellMock.pickPath.mockReset().mockResolvedValue(null);
  shellMock.restartApp.mockReset().mockResolvedValue(undefined);
  shellMock.revealInFolder.mockReset().mockResolvedValue(undefined);
  shellMock.saveBlob.mockReset().mockResolvedValue(undefined);

  let rows = [...BACKUPS];
  let passcodeSet = false;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const hasBody = init?.body !== undefined && init?.body !== null;
    const body = hasBody ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body, hasBody });
    if (url === '/api/auth/state') return json(200, { passcode_set: passcodeSet });
    if (url === '/api/auth/passcode' && method === 'PUT') { passcodeSet = body.new !== null; return json(200, { status: 'success' }); }
    if (url === '/api/data/status') return json(200, { status: 'ok', detail: null, backups: rows });
    if (url.endsWith('/restore') && method === 'POST') return json(200, { restart_required: true });
    if (url.startsWith('/api/data/backups/') && method === 'DELETE') {
      rows = rows.filter((row) => !url.endsWith(row.id));
      return json(200, { status: 'success' });
    }
    if (url.startsWith('/api/data/location') && locationHandler) {
      return locationHandler(url, method, body);
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

  it('checks target directory, renders errors and byte summary, and resets stale check results on input change', async () => {
    locationHandler = (url, method) => {
      if (url === '/api/data/location' && method === 'GET') {
        return json(200, {
          path: '/home/lab/.amadeus',
          source: 'default',
          movable: true,
          previous_path: null,
          previous_exists: false,
        });
      }
      if (url === '/api/data/location/check' && method === 'POST') {
        return json(200, {
          ok: false,
          errors: [
            { code: 'target_not_empty', message: 'Target not empty' },
            { code: 'insufficient_space', message: 'Not enough space' },
          ],
          warnings: [],
          needed_bytes: 1_572_864,
          free_bytes: 524_288,
        });
      }
      throw new Error(`unexpected location call: ${method} ${url}`);
    };

    mount();
    const form = await screen.findByTestId('location-relocation-form');
    expect(within(form).queryByRole('button', { name: '选择目录' })).toBeNull();
    const input = within(form).getByLabelText('目标数据目录');
    const checkBtn = within(form).getByRole('button', { name: '检查目标目录' });
    const moveBtn = within(form).getByRole('button', { name: '开始迁移并重启' });
    expect(checkBtn).toBeDisabled();
    expect(moveBtn).toBeDisabled();

    fireEvent.change(input, { target: { value: '  /mnt/existing  ' } });
    expect(checkBtn).toBeEnabled();
    expect(moveBtn).toBeDisabled();

    fireEvent.click(checkBtn);
    const result = await within(form).findByTestId('location-check-result');
    expect(calls.find((c) => c.url === '/api/data/location/check')?.body).toEqual({ path: '/mnt/existing' });
    const errorList = within(result).getByRole('alert', { name: '目标目录错误' });
    expect(errorList).toHaveTextContent('目标目录必须是空目录，或还不存在的新目录。');
    expect(errorList).toHaveTextContent('目标磁盘可用空间不足（需要至少为当前数据总量的 1.2 倍）。');
    const summary = within(result).getByTestId('location-space-summary');
    expect(summary).toHaveTextContent('预计需要空间：1.50 MB');
    expect(summary).toHaveTextContent('目标可用空间：512 KB');
    expect(moveBtn).toBeDisabled();

    fireEvent.change(input, { target: { value: '/mnt/clean-dir' } });
    expect(within(form).queryByTestId('location-check-result')).toBeNull();
  });

  it('requires warning checkbox acknowledgment and handles 409 / 422 move check rejections', async () => {
    let moveAttempt = 0;
    locationHandler = (url, method) => {
      if (url === '/api/data/location' && method === 'GET') {
        return json(200, {
          path: '/home/lab/.amadeus',
          source: 'default',
          movable: true,
          previous_path: null,
          previous_exists: false,
        });
      }
      if (url === '/api/data/location/check' && method === 'POST') {
        return json(200, {
          ok: true,
          errors: [],
          warnings: [{ code: 'sync_directory', message: 'Sync directory detected' }],
          needed_bytes: 1_048_576,
          free_bytes: 20_971_520,
        });
      }
      if (url === '/api/data/location/move' && method === 'POST') {
        moveAttempt += 1;
        if (moveAttempt === 1) {
          return json(409, {
            detail: {
              code: 'warnings_not_acknowledged',
              ok: true,
              errors: [],
              warnings: [{ code: 'sync_directory', message: 'Sync directory detected' }],
              needed_bytes: 1_048_576,
              free_bytes: 20_971_520,
            },
          });
        }
        return json(422, {
          detail: {
            ok: false,
            errors: [{ code: 'insufficient_space', message: 'No space left' }],
            warnings: [],
            needed_bytes: 1_048_576,
            free_bytes: 512,
          },
        });
      }
      throw new Error(`unexpected location call: ${method} ${url}`);
    };

    mount();
    const form = await screen.findByTestId('location-relocation-form');
    fireEvent.change(within(form).getByLabelText('目标数据目录'), { target: { value: '/mnt/OneDrive/amadeus' } });
    fireEvent.click(within(form).getByRole('button', { name: '检查目标目录' }));

    const warningList = await within(form).findByRole('status', { name: '目标目录风险提示' });
    expect(warningList).toHaveTextContent('目标位置在同步盘（如 OneDrive / Dropbox）内，同步软件并发写入可能损坏数据库。');
    const moveBtn = within(form).getByRole('button', { name: '开始迁移并重启' });
    expect(moveBtn).toBeDisabled();

    const ackCheckbox = within(form).getByRole('checkbox', { name: '我已了解以上提示，仍要继续迁移' });
    fireEvent.click(ackCheckbox);
    expect(moveBtn).toBeEnabled();

    fireEvent.click(moveBtn);
    await waitFor(() => expect(calls.filter((c) => c.url === '/api/data/location/move')).toHaveLength(1));
    expect(calls.find((c) => c.url === '/api/data/location/move')?.body).toEqual({
      path: '/mnt/OneDrive/amadeus',
      acknowledge_warnings: true,
    });
    await waitFor(() => expect(within(form).getByRole('checkbox', { name: '我已了解以上提示，仍要继续迁移' })).not.toBeChecked());
    expect(moveBtn).toBeDisabled();

    fireEvent.click(within(form).getByRole('checkbox', { name: '我已了解以上提示，仍要继续迁移' }));
    fireEvent.click(moveBtn);
    const errorList = await within(form).findByRole('alert', { name: '目标目录错误' });
    expect(errorList).toHaveTextContent('目标磁盘可用空间不足（需要至少为当前数据总量的 1.2 倍）。');
    expect(moveBtn).toBeDisabled();
  });

  it('picks directory in shell, locks actions during move, triggers restartApp, and recovers when restart fails', async () => {
    shellMock.inShell = true;
    shellMock.pickPath.mockResolvedValueOnce('/Volumes/LabSSD/amadeus-data');
    shellMock.restartApp.mockRejectedValueOnce(new Error('restart failed')).mockResolvedValueOnce(undefined);

    let resolveMove!: (res: Response) => void;
    locationHandler = (url, method) => {
      if (url === '/api/data/location' && method === 'GET') {
        return json(200, {
          path: '/Users/okabe/.amadeus',
          source: 'default',
          movable: true,
          previous_path: '/Users/okabe/.amadeus-legacy',
          previous_exists: true,
        });
      }
      if (url === '/api/data/location/check' && method === 'POST') {
        return json(200, {
          ok: true,
          errors: [],
          warnings: [],
          needed_bytes: 1_048_576,
          free_bytes: 104_857_600,
        });
      }
      if (url === '/api/data/location/move' && method === 'POST') {
        return new Promise<Response>((resolve) => {
          resolveMove = resolve;
        });
      }
      throw new Error(`unexpected location call: ${method} ${url}`);
    };

    mount();
    const form = await screen.findByTestId('location-relocation-form');
    fireEvent.click(within(form).getByRole('button', { name: '选择目录' }));
    expect(shellMock.pickPath).toHaveBeenCalledWith('directory');
    await waitFor(() => expect(within(form).getByLabelText('目标数据目录')).toHaveValue('/Volumes/LabSSD/amadeus-data'));

    fireEvent.click(within(form).getByRole('button', { name: '检查目标目录' }));
    const moveBtn = within(form).getByRole('button', { name: '开始迁移并重启' });
    await waitFor(() => expect(moveBtn).toBeEnabled());

    fireEvent.click(moveBtn);
    expect(await screen.findByTestId('location-moving-notice')).toBeInTheDocument();
    expect(within(form).getByRole('button', { name: '迁移中…' })).toBeDisabled();
    expect(within(form).getByLabelText('目标数据目录')).toBeDisabled();
    expect(screen.getByRole('button', { name: '删除旧目录' })).toBeDisabled();

    resolveMove(json(200, { restart_required: true, path: '/Volumes/LabSSD/amadeus-data' }));

    const restartNotice = await screen.findByTestId('location-restart-required');
    expect(restartNotice).toHaveTextContent('/Volumes/LabSSD/amadeus-data');
    expect(restartNotice).toHaveTextContent('需要重启 AMADEUS 才能生效（相关操作已锁定）');
    await waitFor(() => expect(shellMock.restartApp).toHaveBeenCalledTimes(1));
    expect(await within(restartNotice).findByText(/自动重启失败/)).toBeInTheDocument();

    // Actions stay locked after move completes
    expect(within(form).getByLabelText('目标数据目录')).toBeDisabled();
    expect(within(form).getByRole('button', { name: '检查目标目录' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '删除旧目录' })).toBeDisabled();

    fireEvent.click(within(restartNotice).getByRole('button', { name: '立即重启' }));
    await waitFor(() => expect(shellMock.restartApp).toHaveBeenCalledTimes(2));
  });

  it('shows manual restart instructions after move when outside desktop shell', async () => {
    locationHandler = (url, method) => {
      if (url === '/api/data/location' && method === 'GET') {
        return json(200, {
          path: '/home/lab/.amadeus',
          source: 'default',
          movable: true,
          previous_path: null,
          previous_exists: false,
        });
      }
      if (url === '/api/data/location/check' && method === 'POST') {
        return json(200, { ok: true, errors: [], warnings: [], needed_bytes: 1024, free_bytes: 102_400 });
      }
      if (url === '/api/data/location/move' && method === 'POST') {
        return json(200, { restart_required: true, path: '/mnt/amadeus-next' });
      }
      throw new Error(`unexpected location call: ${method} ${url}`);
    };

    mount();
    const form = await screen.findByTestId('location-relocation-form');
    fireEvent.change(within(form).getByLabelText('目标数据目录'), { target: { value: '/mnt/amadeus-next' } });
    fireEvent.click(within(form).getByRole('button', { name: '检查目标目录' }));
    const moveBtn = within(form).getByRole('button', { name: '开始迁移并重启' });
    await waitFor(() => expect(moveBtn).toBeEnabled());

    fireEvent.click(moveBtn);
    const restartNotice = await screen.findByTestId('location-restart-required');
    expect(restartNotice).toHaveTextContent('当前不在桌面端外壳中，无法自动重启；请手动重启本地服务并刷新页面以切换到新目录。');
    expect(shellMock.restartApp).not.toHaveBeenCalled();
  });

  it('renders non-movable status and disables relocation when AMADEUS_DATA_DIR is set', async () => {
    locationHandler = (url, method) => {
      if (url === '/api/data/location' && method === 'GET') {
        return json(200, {
          path: '/env/custom/amadeus',
          source: 'env',
          movable: false,
          previous_path: null,
          previous_exists: false,
        });
      }
      throw new Error(`unexpected location call: ${method} ${url}`);
    };

    mount();
    const notice = await screen.findByTestId('location-non-movable');
    expect(screen.getByText('/env/custom/amadeus')).toBeInTheDocument();
    expect(notice).toHaveTextContent('当前由环境变量 AMADEUS_DATA_DIR 指定位置，应用内迁移已禁用。');
    expect(within(notice).getByRole('button', { name: '开始迁移并重启' })).toBeDisabled();
    expect(screen.queryByTestId('location-relocation-form')).toBeNull();
    expect(screen.queryByLabelText('目标数据目录')).toBeNull();
    expect(screen.queryByRole('button', { name: '检查目标目录' })).toBeNull();
  });

  it('reveals previous directory in shell, deletes it via stamped ConfirmDialog with no query or body, shows toast, and refreshes', async () => {
    shellMock.inShell = true;
    let previousExists = true;
    let previousPath: string | null = '/Users/okabe/.amadeus-old';

    locationHandler = (url, method) => {
      if (url === '/api/data/location' && method === 'GET') {
        return json(200, {
          path: '/Volumes/LabSSD/amadeus',
          source: 'custom',
          movable: true,
          previous_path: previousPath,
          previous_exists: previousExists,
        });
      }
      if (url === '/api/data/location/previous' && method === 'DELETE') {
        previousExists = false;
        previousPath = null;
        return json(200, { status: 'success' });
      }
      throw new Error(`unexpected location call: ${method} ${url}`);
    };

    mount();
    const prevBlock = await screen.findByTestId('location-previous-dir');
    expect(prevBlock).toHaveTextContent('/Users/okabe/.amadeus-old');

    fireEvent.click(within(prevBlock).getByRole('button', { name: '打开旧目录' }));
    expect(shellMock.revealInFolder).toHaveBeenCalledWith('/Users/okabe/.amadeus-old');

    fireEvent.click(within(prevBlock).getByRole('button', { name: '删除旧目录' }));
    const dialog = screen.getByRole('alertdialog', { name: '删除旧数据目录？' });
    expect(dialog).toHaveTextContent('不可撤回');
    expect(dialog).toHaveTextContent('/Users/okabe/.amadeus-old');
    expect(calls.some((c) => c.url.includes('/api/data/location/previous'))).toBe(false);

    fireEvent.click(within(dialog).getByRole('button', { name: '删除旧目录' }));
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull());

    const deleteCall = calls.find((c) => c.method === 'DELETE' && c.url.startsWith('/api/data/location/previous'));
    expect(deleteCall).toBeDefined();
    expect(deleteCall?.url).toBe('/api/data/location/previous');
    expect(deleteCall?.hasBody).toBe(false);

    const toast = await screen.findByTestId('location-cleanup-toast');
    expect(toast).toHaveAttribute('role', 'status');
    expect(toast).toHaveTextContent('已删除旧数据目录（/Users/okabe/.amadeus-old）。');
    await waitFor(() => expect(screen.queryByTestId('location-previous-dir')).toBeNull());
  });

  it('keeps ConfirmDialog open and shows error feedback when previous directory deletion fails', async () => {
    locationHandler = (url, method) => {
      if (url === '/api/data/location' && method === 'GET') {
        return json(200, {
          path: '/Volumes/LabSSD/amadeus',
          source: 'custom',
          movable: true,
          previous_path: '/Users/okabe/.amadeus-old',
          previous_exists: true,
        });
      }
      if (url === '/api/data/location/previous' && method === 'DELETE') {
        return json(409, { detail: { code: 'previous_not_found' } });
      }
      throw new Error(`unexpected location call: ${method} ${url}`);
    };

    mount();
    const prevBlock = await screen.findByTestId('location-previous-dir');
    expect(within(prevBlock).queryByRole('button', { name: '打开旧目录' })).toBeNull();

    fireEvent.click(within(prevBlock).getByRole('button', { name: '删除旧目录' }));
    const dialog = screen.getByRole('alertdialog', { name: '删除旧数据目录？' });
    fireEvent.click(within(dialog).getByRole('button', { name: '删除旧目录' }));

    const alert = await within(dialog).findByRole('alert');
    expect(alert).toHaveTextContent('旧数据目录已不存在或未记录。');
    expect(screen.getByRole('alertdialog', { name: '删除旧数据目录？' })).toBeInTheDocument();
  });
});

describe('DataPanel controlled relocation & AboutPanel lock', () => {
  it('skips mount fetches and suppresses duplicate inline banner when controlled with an active relocation', async () => {
    const onRelocationChange = vi.fn();
    render(
      <div className="ws2">
        <DataPanel relocation={{ path: '/mnt/new-amadeus' }} onRelocationChange={onRelocationChange} />
      </div>,
    );

    await Promise.resolve();
    await Promise.resolve();

    expect(calls).toHaveLength(0);
    expect(screen.queryByTestId('location-restart-required')).toBeNull();
    expect(screen.getByText('/mnt/new-amadeus')).toBeInTheDocument();
    expect(screen.getByLabelText('目标数据目录')).toBeDisabled();
    expect(screen.getByRole('button', { name: '检查目标目录' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '开始迁移并重启' })).toBeDisabled();
    expect(screen.getByText('数据已迁移，重启 AMADEUS 后可查看或修改。')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '设口令' })).toBeNull();
    expect(screen.getByRole('button', { name: '立即备份' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '导出' })).toBeDisabled();
  });

  it('locks AboutPanel diagnostic bundle generation during relocation and flags 503 data_relocating', async () => {
    const onRelocating = vi.fn();
    const { rerender } = render(<div className="ws2"><AboutPanel relocating onRelocating={onRelocating} /></div>);

    expect(screen.getByTestId('about-relocating-note')).toHaveTextContent('数据已迁移至新位置，重启后才能生成反馈包。');
    expect(screen.getByRole('button', { name: '生成反馈包' })).toBeDisabled();

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/diagnostics/bundle') {
        return json(503, { detail: { code: 'data_relocating', message: 'relocating' } });
      }
      throw new Error(`unexpected fetch: ${url}`);
    }));

    rerender(<div className="ws2"><AboutPanel relocating={false} onRelocating={onRelocating} /></div>);
    fireEvent.click(screen.getByRole('button', { name: '生成反馈包' }));

    await waitFor(() => expect(onRelocating).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole('alert')).toHaveTextContent('数据已迁移至新位置，需立即重启 AMADEUS 完成切换。');
  });
});
