import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const shellMock = vi.hoisted(() => ({
  inShell: false,
  revealInFolder: vi.fn<(path: string) => Promise<boolean>>(async () => true),
  openExternal: vi.fn<(url: string) => Promise<void>>(async () => undefined),
}));

const apiMock = vi.hoisted(() => ({
  restoreBackup: vi.fn<() => Promise<{ restart_required: boolean }>>(async () => ({ restart_required: true })),
  diagnosticsBundle: vi.fn<() => Promise<{ path: string; size: number }>>(async () => ({ path: 'C:\\Users\\lab\\diag.zip', size: 1024 })),
  dataLocation: vi.fn(async () => ({
    path: 'C:\\Users\\lab\\AppData\\Roaming\\Amadeus',
    default_path: 'C:\\Users\\lab\\AppData\\Roaming\\Amadeus',
    source: 'default',
    movable: true,
    previous_path: null,
    previous_exists: false,
  })),
}));

vi.mock('./shell', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./shell')>();
  return {
    ...actual,
    get inShell() {
      return shellMock.inShell;
    },
    revealInFolder: shellMock.revealInFolder,
    openExternal: shellMock.openExternal,
  };
});

vi.mock('./dataApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./dataApi')>();
  return {
    ...actual,
    restoreBackup: apiMock.restoreBackup,
    diagnosticsBundle: apiMock.diagnosticsBundle,
    dataLocation: apiMock.dataLocation,
  };
});

import { DataRecovery } from './DataRecovery';
import type { DataStatus } from './dataApi';

const SAMPLE_STATUS: DataStatus = {
  status: 'restore_failed',
  available: false,
  detail: 'restore interrupted before swap completed',
  backups: [
    {
      id: '20261108T224512000000Z-manual',
      time: '2026-11-08T22:45:12+00:00',
      reason: 'manual',
      size: 18_874_368,
      app_version: '0.1.0',
      can_restore: true,
    },
  ],
};

beforeEach(() => {
  shellMock.inShell = false;
  shellMock.revealInFolder.mockClear();
  shellMock.openExternal.mockClear();
  apiMock.restoreBackup.mockClear();
  apiMock.diagnosticsBundle.mockClear();
  apiMock.dataLocation.mockClear();
});

afterEach(cleanup);

describe('DataRecovery', () => {
  it('explains interrupted restore clearly when status is restore_failed', () => {
    render(<DataRecovery status={SAMPLE_STATUS} worldline="steins_gate" />);

    expect(screen.getByRole('heading', { name: '上次恢复未完成' })).toBeInTheDocument();
    expect(screen.getByText('上次恢复未完成且未能自动还原，请重新选择有效备份恢复，或打开数据目录处理。')).toBeInTheDocument();
    expect(screen.queryByText('数据异常')).toBeNull();
    expect(screen.queryByText('打开数据库时，检查没有通过。为了不让数据坏得更多，AMADEUS 先停在这里，不进入对话。')).toBeNull();
  });

  it('keeps corrupt copy when status is corrupt and lets user schedule a backup restore', async () => {
    render(<DataRecovery status={{ ...SAMPLE_STATUS, status: 'corrupt' }} worldline="beta" />);

    expect(screen.getByRole('heading', { name: '数据异常' })).toBeInTheDocument();
    expect(screen.getByText('打开数据库时，检查没有通过。为了不让数据坏得更多，AMADEUS 先停在这里，不进入对话。')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '恢复' }));
    const dialog = screen.getByRole('alertdialog', { name: '恢复到这份备份？' });
    fireEvent.click(within(dialog).getByRole('button', { name: '安排恢复' }));

    await waitFor(() => expect(apiMock.restoreBackup).toHaveBeenCalledWith('20261108T224512000000Z-manual'));
    expect(await screen.findByRole('status')).toHaveTextContent('已安排恢复到');
  });

  it('offers opening data folder only inside shell on user click', async () => {
    shellMock.inShell = true;
    render(<DataRecovery status={SAMPLE_STATUS} worldline="steins_gate" />);

    expect(apiMock.dataLocation).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '打开数据目录' }));
    await waitFor(() => expect(shellMock.revealInFolder).toHaveBeenCalledWith('C:\\Users\\lab\\AppData\\Roaming\\Amadeus'));
  });
});
