import { useSyncExternalStore } from 'react';
import { inShell, shellVersion } from './shell';

/*
 * Checking for a new version (amadeus-ops design/20261108-ui-inventory.md §2.12): once, quietly, after login, and on
 * demand from 关于. One store for the app, so the header's mark and the page agree. In the installed app it is the
 * shell's updater (GitHub Releases' latest.json, signed); on the design mock it is the dev server's /__mock/update,
 * which plays the same states; anywhere else (a dev build in a browser, the tests) there is nothing to ask.
 */

export type UpdateInfo = { version: string; notes: string; date: string | null };
export type UpdateState =
  | { phase: 'idle' }
  | { phase: 'checking'; silent: boolean }
  | { phase: 'current'; checkedAt: number }
  | { phase: 'available'; update: UpdateInfo }
  | { phase: 'downloading'; update: UpdateInfo; received: number; total: number | null }
  | { phase: 'installing'; update: UpdateInfo }
  | { phase: 'failed'; step: 'check' | 'download'; silent: boolean; update?: UpdateInfo }
  | { phase: 'unsupported' };

type Source = {
  check(): Promise<UpdateInfo | null>;
  install(progress: (received: number, total: number | null) => void): Promise<void>;
  version(): Promise<string | null>;
};

class Unsupported extends Error {}
const wait = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

function shellSource(): Source {
  let pending: import('@tauri-apps/plugin-updater').Update | null = null;
  return {
    async check() {
      const { check } = await import('@tauri-apps/plugin-updater');
      pending = await check();
      return pending ? { version: pending.version, notes: pending.body ?? '', date: pending.date ?? null } : null;
    },
    async install(progress) {
      if (!pending) throw new Error('no_update');
      let received = 0;
      let total: number | null = null;
      // On Windows the installer takes over and the app exits; this may never return.
      await pending.downloadAndInstall((event) => {
        if (event.event === 'Started') { total = event.data.contentLength ?? null; progress(0, total); }
        else if (event.event === 'Progress') { received += event.data.chunkLength; progress(received, total); }
      });
    },
    version: shellVersion,
  };
}

type MockPlan = { current: string; update: UpdateInfo | null; fail: 'check' | 'download' | null; size: number };

function mockSource(): Source {
  let plan: Promise<MockPlan> | null = null;
  const load = () => plan ??= fetch('/__mock/update', { cache: 'no-store' }).then((response) => {
    if (!response.ok) throw new Unsupported();
    return response.json() as Promise<MockPlan>;
  }).catch((error) => {
    plan = null;
    throw error instanceof Unsupported ? error : new Unsupported();
  });
  return {
    async check() {
      plan = null; // read the scenario afresh, as a real check would ask again
      const p = await load();
      await wait(900);
      if (p.fail === 'check') throw new Error('check_failed');
      return p.update;
    },
    async install(progress) {
      const p = await load();
      const total = p.size;
      let received = 0;
      progress(0, total);
      while (received < total) {
        await wait(110);
        received = Math.min(total, received + Math.ceil(total / 28));
        if (p.fail === 'download' && received > total * 0.62) throw new Error('download_failed');
        progress(received, total);
      }
      await wait(400);
    },
    async version() {
      try { return (await load()).current; } catch { return null; }
    },
  };
}

let source: Source | null | undefined;
const getSource = () => {
  if (source === undefined) source = inShell ? shellSource() : import.meta.env.DEV && import.meta.env.MODE !== 'test' ? mockSource() : null;
  return source;
};

let state: UpdateState = { phase: 'idle' };
const listeners = new Set<() => void>();
const set = (next: UpdateState) => { state = next; listeners.forEach((listener) => listener()); };
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };

export function useUpdates(): UpdateState {
  return useSyncExternalStore(subscribe, () => state, () => state);
}

export async function checkForUpdate({ silent = false } = {}): Promise<void> {
  if (state.phase === 'checking' || state.phase === 'downloading' || state.phase === 'installing') return;
  const from = getSource();
  if (!from) { set({ phase: 'unsupported' }); return; }
  set({ phase: 'checking', silent });
  try {
    const update = await from.check();
    set(update ? { phase: 'available', update } : { phase: 'current', checkedAt: Date.now() });
  } catch (error) {
    set(error instanceof Unsupported ? { phase: 'unsupported' } : { phase: 'failed', step: 'check', silent });
  }
}

let checkedAfterLogin = false;
/** The quiet check, once per run of the app. */
export function checkAfterLogin(): void {
  if (checkedAfterLogin) return;
  checkedAfterLogin = true;
  void checkForUpdate({ silent: true });
}

export async function installUpdate(): Promise<void> {
  const update = state.phase === 'available' || (state.phase === 'failed' && state.step === 'download') ? state.update : undefined;
  const from = getSource();
  if (!update || !from) return;
  set({ phase: 'downloading', update, received: 0, total: null });
  try {
    await from.install((received, total) => set({ phase: 'downloading', update, received, total }));
    set({ phase: 'installing', update });
  } catch {
    set({ phase: 'failed', step: 'download', silent: false, update });
  }
}

let version: Promise<string | null> | null = null;
/** The running version: the shell's, the mock's, or null (a dev build in a browser). */
export function appVersion(): Promise<string | null> {
  return version ??= (getSource()?.version() ?? Promise.resolve(null)).catch(() => null);
}

/** For the header: a new version waits (or is on its way in). */
export const updateWaiting = (s: UpdateState) => s.phase === 'available' || s.phase === 'downloading' || s.phase === 'installing'
  || (s.phase === 'failed' && s.step === 'download');
