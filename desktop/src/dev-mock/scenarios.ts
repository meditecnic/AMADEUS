import streaming from './scenarios/streaming.json';
import loginSuccess from './scenarios/login-success.json';
import loginFailure from './scenarios/login-failure.json';
import sessionList from './scenarios/session-list.json';
import memory from './scenarios/memory.json';
import empty from './scenarios/empty.json';
import error from './scenarios/error.json';
import offline from './scenarios/offline.json';
import shiftFailure from './scenarios/shift-failure.json';
import shiftSlow from './scenarios/shift-slow.json';
import workstationLong from './scenarios/workstation-long.json';
import readyDelay from './scenarios/ready-delay.json';
import transportClose from './scenarios/transport-close.json';
import slice from './scenarios/slice.json';
import loginPasscode from './scenarios/login-passcode.json';
import { workstationFixture } from './workstation-fixture';
import { model } from './fixtures';

import memoryNew from './scenarios/memory-new.json';
import memoryLarge from './scenarios/memory-large.json';
import memoryHuge from './scenarios/memory-huge.json';
import memoryMid from './scenarios/memory-mid.json';
import memoryLoading from './scenarios/memory-loading.json';
import memoryUnavailable from './scenarios/memory-unavailable.json';
import memoryError from './scenarios/memory-error.json';
import type { MemorySeed } from './memory';
import type { DataSeed } from './data';
import dataCorrupt from './scenarios/data-corrupt.json';
import dataRestoreFailed from './scenarios/data-restore-failed.json';
import updateAvailable from './scenarios/update-available.json';
import updateBroken from './scenarios/update-broken.json';

export type Scenario = Omit<typeof streaming, 'errors' | 'delays' | 'chatError' | 'authError' | 'conversations' | 'memory'> & {
  conversations: Array<typeof streaming.conversations[number] & { model_id?: string }>;
  models: typeof model[];
  transport: { readyDelayMs: number; closeAfterReadyMs: number | null };
  memory: MemorySeed;
  errors: Array<{ method: string; pathPrefix: string; status: number; body: unknown }>;
  /** Holds matching REST replies back, e.g. to show a reading state. */
  delays: Array<{ method: string; pathPrefix: string; ms: number }>;
  chatError: string | null;
  authError: string | null;
  /** The data routes' state: backups, a corrupt store, a failed restore. */
  data?: DataSeed;
  /** What /__mock/update offers: a newer version, and where the check or the download fails. */
  update?: { version?: string; date?: string; notes?: string; fail?: string; size?: number };
};
type Override = Omit<Partial<Scenario>, 'login' | 'shift'> & {
  extends?: string;
  login?: Partial<Scenario['login']>;
  shift?: Partial<Scenario['shift']>;
};

export const scenarios: Record<string, Scenario> = Object.fromEntries(
  [streaming, loginSuccess, loginFailure, sessionList, memory, memoryNew, memoryLarge, memoryHuge, memoryMid, memoryLoading, memoryUnavailable,
    memoryError, empty, error, offline, shiftFailure, shiftSlow, workstationLong, readyDelay, transportClose, slice, loginPasscode,
    dataCorrupt, dataRestoreFailed, updateAvailable, updateBroken]
    .map((override: Override) => [override.name!, {
      ...streaming, ...override,
      login: { ...streaming.login, ...override.login },
      shift: { ...streaming.shift, ...override.shift },
      models: [model],
      transport: { readyDelayMs: 0, closeAfterReadyMs: null, ...override.transport },
      ...(override.name === 'workstation-long' ? workstationFixture : {}),
    }]),
);

export function readCookie(cookies: string, key: string): string | undefined {
  const value = cookies.split(';').map(part => part.trim()).find(part => part.startsWith(`${key}=`))?.slice(key.length + 1);
  try { return value === undefined ? undefined : decodeURIComponent(value); } catch { return undefined; }
}

export function selectScenario(url: URL, cookies = ''): string {
  const name = url.searchParams.get('scenario') ?? readCookie(cookies, 'amadeus_mock_scenario') ?? 'streaming';
  return Object.prototype.hasOwnProperty.call(scenarios, name) ? name : 'streaming';
}
