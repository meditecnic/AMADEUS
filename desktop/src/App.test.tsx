import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';

const probe = vi.hoisted(() => vi.fn());
vi.mock('./assetPack', () => ({ loadAssetPack: probe }));
vi.mock('./components/SalieriLogin', () => ({
  SalieriLogin: ({ onLoginSuccess }: { onLoginSuccess: () => void }) => <button onClick={onLoginSuccess}>login</button>,
}));
vi.mock('./components/EntrySequence', () => ({
  EntrySequence: ({ onComplete }: { onComplete: () => void }) => <button onClick={onComplete}>entry</button>,
}));
vi.mock('./components/Workstation', () => ({
  Workstation: ({ onLogout }: { onLogout: () => void }) => <button onClick={onLogout}>workstation</button>,
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
