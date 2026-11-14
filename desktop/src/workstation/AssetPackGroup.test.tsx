import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const shellMock = vi.hoisted(() => ({
  inShell: false,
  pickPath: vi.fn<(kind: 'directory' | 'zip') => Promise<string | null>>(async () => null),
}));

const packMock = vi.hoisted(() => ({
  reloadAssetPack: vi.fn<() => Promise<boolean>>(async () => true),
}));

vi.mock('./shell', () => ({
  get inShell() {
    return shellMock.inShell;
  },
  pickPath: shellMock.pickPath,
}));

vi.mock('../assetPack', () => ({
  reloadAssetPack: packMock.reloadAssetPack,
}));

import { AssetPackGroup } from './AssetPackGroup';

const EMOTIONS = [
  'neutral',
  'tsundere',
  'embarrassed',
  'intellectual',
  'happy',
  'surprised',
  'annoyed',
  'disappointed',
  'sad',
] as const;

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const UNINSTALLED_STATUS = {
  installed: false,
  complete: false,
  movable: true,
  path: '/tmp/amadeus-data/asset-pack',
  source: 'data_dir',
  name: null,
  version: null,
  size_bytes: 0,
  required_count: 34,
  present_count: 0,
  missing_files: ['assets/images/kurisu_normal1.png'],
};

const INSTALLED_STATUS = {
  installed: true,
  complete: true,
  movable: true,
  path: '/tmp/amadeus-data/asset-pack',
  source: 'data_dir',
  name: 'Optional Pack',
  version: '1.0.0',
  size_bytes: 48_120_000,
  required_count: 34,
  present_count: 34,
  missing_files: [],
};

const VALID_INSPECT = {
  valid: true,
  source_type: 'zip' as const,
  stripped_root: 'pack-root',
  name: 'Optional Pack',
  version: '1.0.0',
  required_count: 34,
  found_count: 34,
  missing_files: [],
  invalid_files: [],
  errors: [],
  warnings: [],
  total_bytes: 48_120_000,
  previews: EMOTIONS.map((emotion) => ({
    emotion,
    path: `assets/images/${emotion}_1.png`,
    data_url: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVR4nGMAAQAABQABDQottAAAAABJRU5ErkJggg==',
  })),
};

describe('AssetPackGroup', () => {
  beforeEach(() => {
    shellMock.inShell = false;
    shellMock.pickPath.mockReset();
    packMock.reloadAssetPack.mockClear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('inspects and installs a raw binary .zip upload (never multipart), reloads pack, and fires toast notice', async () => {
    const requests: Array<{ url: string; method: string; headers: Headers; body: unknown }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
      const method = (init?.method ?? 'GET').toUpperCase();
      const headers = new Headers(init?.headers);
      requests.push({ url, method, headers, body: init?.body });

      if (url.endsWith('/api/data/asset-pack') && method === 'GET') return json(200, UNINSTALLED_STATUS);
      if (url.endsWith('/api/data/asset-pack/inspect') && method === 'POST') return json(200, VALID_INSPECT);
      if (url.endsWith('/api/data/asset-pack/install') && method === 'POST') return json(200, INSTALLED_STATUS);
      return json(404, {});
    }));

    const onNotice = vi.fn();
    render(<AssetPackGroup onNotice={onNotice} />);

    expect(await screen.findByText('未安装')).toBeTruthy();

    const zipFile = new File([new Uint8Array([0x50, 0x4b, 0x03, 0x04])], 'my-pack.zip', { type: 'application/zip' });
    const fileInput = screen.getByTestId('asset-pack-file-input') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [zipFile] } });
    expect(screen.getByTestId('asset-pack-selected-file').textContent).toContain('my-pack.zip');

    fireEvent.click(screen.getByRole('button', { name: '检查素材' }));

    const preview = await screen.findByTestId('asset-pack-inspect-preview');
    expect(within(preview).getByText(/34 \/ 34/)).toBeTruthy();
    expect(within(preview).getAllByRole('img')).toHaveLength(9);

    const inspectReq = requests.find((r) => r.url.endsWith('/inspect'));
    expect(inspectReq?.headers.get('Content-Type')).toBe('application/zip');
    expect(inspectReq?.body).toBe(zipFile);

    fireEvent.click(screen.getByRole('button', { name: '确认安装素材包' }));

    await waitFor(() => {
      expect(packMock.reloadAssetPack).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByText(/已完整安装/)).toBeTruthy();
    expect(onNotice).toHaveBeenCalledWith(expect.stringContaining('已安装'), 'info');
    const installReq = requests.find((r) => r.url.endsWith('/install'));
    expect(installReq?.headers.get('Content-Type')).toBe('application/zip');
    expect(installReq?.body).toBe(zipFile);
  });

  it('supports Tauri directory/zip pickers and manual path inspection, and blocks install on invalid inspections', async () => {
    shellMock.inShell = true;
    shellMock.pickPath.mockResolvedValue('/Users/tester/KurisuAssets');

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.endsWith('/api/data/asset-pack') && method === 'GET') return json(200, UNINSTALLED_STATUS);
      if (url.endsWith('/api/data/asset-pack/inspect') && method === 'POST') {
        expect(JSON.parse(String(init?.body))).toEqual({ source_path: '/Users/tester/KurisuAssets' });
        return json(200, {
          ...VALID_INSPECT,
          valid: false,
          source_type: 'directory',
          stripped_root: null,
          found_count: 28,
          missing_files: ['audio/bgm/Amadeus.ogg'],
          invalid_files: [{ path: 'assets/images/kurisu_normal1.png', reason: 'not_png' }],
          errors: ['存在损坏文件'],
          warnings: ['缺少部分音乐文件'],
        });
      }
      return json(404, {});
    }));

    render(<AssetPackGroup />);

    const pickDirBtn = await screen.findByRole('button', { name: '选择目录' });
    expect(screen.getByRole('button', { name: '选择 ZIP 包' })).toBeTruthy();
    fireEvent.click(pickDirBtn);

    await waitFor(() => {
      expect((screen.getByPlaceholderText(/amadeus-asset-pack/) as HTMLInputElement).value).toBe('/Users/tester/KurisuAssets');
    });

    fireEvent.click(screen.getByRole('button', { name: '检查素材' }));
    const preview = await screen.findByTestId('asset-pack-inspect-preview');
    expect(within(preview).getByText('存在阻塞问题')).toBeTruthy();
    expect(within(preview).getByText('存在损坏文件')).toBeTruthy();
    expect(within(preview).getByText(/not_png/)).toBeTruthy();
    expect(within(preview).getByText('缺少部分音乐文件')).toBeTruthy();
    expect(within(preview).getByText('audio/bgm/Amadeus.ogg')).toBeTruthy();
    expect((screen.getByRole('button', { name: '确认安装素材包' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('requires ConfirmDialog before DELETE /api/data/asset-pack (no query/body) and shows env-lock when non-movable', async () => {
    const deleteCalls: Array<{ url: string; hasBody: boolean }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.endsWith('/api/data/asset-pack') && method === 'GET') {
        return json(200, { ...INSTALLED_STATUS, complete: false, present_count: 33, missing_files: ['audio/bgm/Amadeus.ogg'] });
      }
      if (url.includes('/api/data/asset-pack') && method === 'DELETE') {
        deleteCalls.push({ url, hasBody: init?.body !== undefined && init?.body !== null });
        return json(200, UNINSTALLED_STATUS);
      }
      return json(404, {});
    }));

    const onNotice = vi.fn();
    render(<AssetPackGroup onNotice={onNotice} />);

    expect(await screen.findByText(/缺失（33\/34）/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '卸载素材包' }));
    expect(deleteCalls).toHaveLength(0);

    const dialog = await screen.findByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '确认卸载' }));

    await waitFor(() => {
      expect(packMock.reloadAssetPack).toHaveBeenCalledTimes(1);
    });
    expect(deleteCalls).toEqual([{ url: '/api/data/asset-pack', hasBody: false }]);
    expect(onNotice).toHaveBeenCalledWith(expect.stringContaining('已卸载'), 'info');

    cleanup();
    vi.stubGlobal('fetch', vi.fn(async () => json(200, { ...INSTALLED_STATUS, movable: false, source: 'env' })));
    render(<AssetPackGroup />);
    expect(await screen.findByTestId('asset-pack-env-lock')).toBeTruthy();
    expect((screen.getByRole('button', { name: '卸载素材包' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '上传 ZIP 文件' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('accepts drag-and-drop .zip files and rejects non-zip drops without calling the backend', async () => {
    const inspectCalls: unknown[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
      if (url.endsWith('/api/data/asset-pack')) return json(200, UNINSTALLED_STATUS);
      if (url.endsWith('/api/data/asset-pack/inspect')) {
        inspectCalls.push(init?.body);
        return json(200, VALID_INSPECT);
      }
      return json(404, {});
    }));

    render(<AssetPackGroup />);
    const dropzone = await screen.findByTestId('asset-pack-dropzone');

    const badFile = new File(['hello'], 'notes.txt', { type: 'text/plain' });
    fireEvent.drop(dropzone, { dataTransfer: { files: [badFile] } });
    expect(screen.getByRole('alert').textContent).toContain('.zip');

    const goodFile = new File([new Uint8Array([0x50, 0x4b, 0x03, 0x04])], 'dragged.zip', { type: 'application/zip' });
    fireEvent.drop(dropzone, { dataTransfer: { files: [goodFile] } });
    expect(screen.getByTestId('asset-pack-selected-file').textContent).toContain('dragged.zip');

    fireEvent.click(screen.getByRole('button', { name: '检查素材' }));
    await screen.findByTestId('asset-pack-inspect-preview');
    expect(inspectCalls).toEqual([goodFile]);
  });
});
