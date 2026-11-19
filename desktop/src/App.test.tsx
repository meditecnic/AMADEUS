import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DataStatus } from './workstation/dataApi';

const dataStatusMock = vi.hoisted(() => vi.fn<() => Promise<DataStatus>>());

vi.mock('./assetPack', () => ({ loadAssetPack: vi.fn(async () => false) }));
vi.mock('./components/SalieriLogin', () => ({
  SalieriLogin: () => <div data-testid="salieri-login" />,
}));
vi.mock('./components/Workstation', () => ({
  Workstation: () => <div data-testid="workstation" />,
}));
vi.mock('./workstation/dataApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./workstation/dataApi')>();
  return {
    ...actual,
    dataStatus: dataStatusMock,
  };
});

import App from './App';

afterEach(() => {
  cleanup();
  dataStatusMock.mockReset();
});

describe('App data availability routing', () => {
  it('routes restore_failed with available: false into DataRecovery with interrupted-restore guidance', async () => {
    dataStatusMock.mockResolvedValue({
      status: 'restore_failed',
      available: false,
      detail: 'rollback failed',
      backups: [],
    });

    render(<App />);

    expect(await screen.findByRole('heading', { name: '上次恢复未完成' })).toBeInTheDocument();
    expect(screen.getByText('上次恢复未完成且未能自动还原，请重新选择有效备份恢复，或打开数据目录处理。')).toBeInTheDocument();
    expect(screen.queryByTestId('salieri-login')).toBeNull();
  });

  it('allows restore_failed with available: true to proceed to login and workstation', async () => {
    dataStatusMock.mockResolvedValue({
      status: 'restore_failed',
      available: true,
      detail: 'rolled back cleanly',
      backups: [],
    });

    render(<App />);

    expect(await screen.findByTestId('salieri-login')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '上次恢复未完成' })).toBeNull();
    expect(screen.queryByRole('heading', { name: '数据异常' })).toBeNull();
  });

  it('allows restore_failed when available is omitted (dev-mock compatibility)', async () => {
    dataStatusMock.mockResolvedValue({
      status: 'restore_failed',
      detail: 'rolled back cleanly',
      backups: [],
    });

    render(<App />);

    expect(await screen.findByTestId('salieri-login')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '上次恢复未完成' })).toBeNull();
  });

  it('routes corrupt database into DataRecovery with corrupt fault heading', async () => {
    dataStatusMock.mockResolvedValue({
      status: 'corrupt',
      available: false,
      detail: 'database disk image is malformed',
      backups: [],
    });

    render(<App />);

    expect(await screen.findByRole('heading', { name: '数据异常' })).toBeInTheDocument();
    expect(screen.queryByTestId('salieri-login')).toBeNull();
  });
});
