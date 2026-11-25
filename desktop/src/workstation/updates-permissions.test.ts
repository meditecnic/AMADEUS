import { mockIPC } from '@tauri-apps/api/mocks';
import { act, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import capability from '../../src-tauri/capabilities/default.json';

afterEach(() => vi.unstubAllGlobals());

it('grants the main window the updater commands used to check and install', async () => {
  const commands: string[] = [];
  vi.stubGlobal('__TAURI_INTERNALS__', {});
  mockIPC((command) => {
    commands.push(command);
    if (command === 'check_update' || command === 'plugin:updater|check') {
      return { rid: 1, currentVersion: '0.1.0', version: '0.2.0', rawJson: {} };
    }
  });

  const { checkForUpdate, installUpdate, useUpdates } = await import('./updates');
  const { result } = renderHook(useUpdates);
  await act(() => checkForUpdate());
  expect(result.current.phase).toBe('available');
  await act(() => installUpdate());
  expect(result.current.phase).toBe('installing');

  const updaterCommands = commands.filter((command) => command.startsWith('plugin:updater|'));
  expect(updaterCommands.some((command) => command !== 'plugin:updater|check')).toBe(true);
  expect(capability.windows).toContain('main');
  for (const command of updaterCommands) {
    const action = command.split('|')[1].replace(/_/g, '-');
    expect(capability.permissions).toContain(`updater:allow-${action}`);
  }
});
