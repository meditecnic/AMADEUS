import { mockIPC } from '@tauri-apps/api/mocks';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const updaterCheck = vi.fn();

vi.mock('./shell', () => ({
  CC_BY_NC_URL: 'https://example.invalid/cc',
  ISSUES_URL: 'https://example.invalid/issues',
  JINA_MODEL_URL: 'https://example.invalid/jina',
  NOTICES_URL: 'https://example.invalid/notices',
  inShell: true,
  licensesManifest: vi.fn(async () => null),
  openExternal: vi.fn(async () => undefined),
  revealInFolder: vi.fn(async () => undefined),
  shellVersion: vi.fn(async () => '0.1.0'),
}));

beforeEach(() => {
  vi.stubGlobal('__TAURI_INTERNALS__', {});
  mockIPC((command) => {
    if (command !== 'check_update') throw new Error(`Unexpected IPC command: ${command}`);
    return updaterCheck();
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  updaterCheck.mockReset();
  vi.resetModules();
});

describe('updates store (#5 silent check failure)', () => {
  it('does not pollute update state with failed when silent check fails (404/offline)', async () => {
    updaterCheck.mockRejectedValueOnce(new Error('404 Not Found'));
    const { checkAfterLogin, useUpdates } = await import('./updates');
    const { AboutPanel } = await import('./AboutPanel');

    let latestPhase = '';
    function Probe() {
      const state = useUpdates();
      latestPhase = state.phase;
      return <AboutPanel />;
    }

    render(<Probe />);
    checkAfterLogin();

    await waitFor(() => {
      expect(updaterCheck).toHaveBeenCalledTimes(1);
      expect(latestPhase).toBe('idle');
    });
    expect(screen.getByText('还没有检查')).toBeInTheDocument();
    expect(screen.queryByText('没能检查更新')).toBeNull();
    expect(screen.queryByText('连不上更新服务器')).toBeNull();
  });

  it('still reports failed state when manual checkForUpdate fails', async () => {
    updaterCheck.mockRejectedValueOnce(new Error('network offline'));
    const { checkForUpdate, useUpdates } = await import('./updates');
    const { AboutPanel } = await import('./AboutPanel');

    let latestPhase = '';
    function Probe() {
      const state = useUpdates();
      latestPhase = state.phase;
      return <AboutPanel />;
    }

    render(<Probe />);
    await checkForUpdate();

    await waitFor(() => {
      expect(latestPhase).toBe('failed');
    });
    expect(screen.getByText('没能检查更新')).toBeInTheDocument();
    expect(screen.getByText('连不上更新服务器')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
  });

  it('preserves already discovered available state if a silent check later fails', async () => {
    updaterCheck
      .mockResolvedValueOnce({
        rid: 1,
        currentVersion: '0.1.0',
        version: '0.2.0',
        body: 'notes',
        date: '2026-11-18T00:00:00Z',
        rawJson: {},
      })
      .mockRejectedValueOnce(new Error('offline'));
    const { checkForUpdate, useUpdates } = await import('./updates');

    let latest: { phase: string } = { phase: '' };
    function Probe() {
      latest = useUpdates();
      return null;
    }

    render(<Probe />);
    await checkForUpdate();
    expect(latest.phase).toBe('available');

    await checkForUpdate({ silent: true });
    expect(latest.phase).toBe('available');
  });
});
