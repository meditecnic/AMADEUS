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
import { workstationFixture } from './workstation-fixture';
import { model } from './fixtures';

export type Scenario = Omit<typeof streaming, 'errors' | 'chatError' | 'authError' | 'conversations'> & {
  conversations: Array<typeof streaming.conversations[number] & { model_id?: string }>;
  models: typeof model[];
  transport: { readyDelayMs: number; closeAfterReadyMs: number | null };
  errors: Array<{ method: string; pathPrefix: string; status: number; body: unknown }>;
  chatError: string | null;
  authError: string | null;
};
type Override = Omit<Partial<Scenario>, 'login' | 'shift'> & {
  extends?: string;
  login?: Partial<Scenario['login']>;
  shift?: Partial<Scenario['shift']>;
};

export const scenarios: Record<string, Scenario> = Object.fromEntries(
  [streaming, loginSuccess, loginFailure, sessionList, memory, empty, error, offline, shiftFailure, shiftSlow, workstationLong, readyDelay, transportClose]
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
