import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useEffect } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';

const probe = vi.hoisted(() => vi.fn());
const mounts = vi.hoisted(() => vi.fn());
vi.mock('./assetPack', () => ({ loadAssetPack: probe }));
vi.mock('./components/SalieriLogin', () => ({
  SalieriLogin: ({ onLoginSuccess }: { onLoginSuccess: () => void }) => <button onClick={onLoginSuccess}>login</button>,
}));
vi.mock('./components/EntrySequence', () => ({
  EntrySequence: ({ onComplete }: { onComplete: () => void }) => <button onClick={onComplete}>entry</button>,
}));
vi.mock('./components/Workstation', () => ({
  Workstation: ({ onLogout }: { onLogout: () => void }) => {
    useEffect(() => { mounts(); }, []);
    return <button onClick={onLogout}>workstation</button>;
  },
}));
afterEach(() => { cleanup(); localStorage.clear(); vi.clearAllMocks(); });

it.each([true, false])('reaches all four states regardless of pack availability (%s)', async (pack) => {
  let resolve!: (available: boolean) => void;
  probe.mockReturnValue(new Promise<boolean>(r => { resolve = r; }));
  const { container, rerender } = render(<App />);
  expect(container.querySelector('main')).toHaveAttribute('data-step', 'probe');
  await act(async () => resolve(pack));
  expect(screen.getByRole('button', { name: 'login' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'login' }));
  expect(screen.getByRole('button', { name: 'entry' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'entry' }));
  expect(screen.getByRole('button', { name: 'workstation' })).toBeInTheDocument();
  rerender(<App />);
  expect(screen.queryByRole('button', { name: 'entry' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'workstation' }));
  fireEvent.click(screen.getByRole('button', { name: 'login' }));
  expect(screen.getByRole('button', { name: 'entry' })).toBeInTheDocument();
});

it('recovers from a failed optional pack probe and starts a fresh entry on a new page runtime', async () => {
  probe.mockRejectedValue(new Error('optional pack unavailable'));
  const { unmount } = render(<App />);
  await act(async () => {});
  fireEvent.click(screen.getByRole('button', { name: 'login' }));
  fireEvent.click(screen.getByRole('button', { name: 'entry' }));
  unmount();
  render(<App />);
  await act(async () => {});
  fireEvent.click(screen.getByRole('button', { name: 'login' }));
  expect(screen.getByRole('button', { name: 'entry' })).toBeInTheDocument();
});

it('mounts the live workstation once beneath the entrance and never remounts it', async () => {
  probe.mockResolvedValue(true);
  const { container } = render(<App />);
  await act(async () => {});
  fireEvent.click(screen.getByRole('button', { name: 'login' }));
  expect(container.querySelector('main')).toHaveAttribute('data-step', 'entry');
  expect(mounts).toHaveBeenCalledTimes(1);
  // The entrance reveals the already-mounted workstation instead of a second boot.
  expect(screen.getByRole('button', { name: 'entry' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'workstation' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'entry' }));
  expect(container.querySelector('main')).toHaveAttribute('data-step', 'workstation');
  expect(mounts).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('button', { name: 'entry' })).not.toBeInTheDocument();
});
