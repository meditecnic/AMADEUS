// The user's "立体 | 图版" pick and the note that a machine was found slow: front-end local settings, kept in this
// browser only. They are not sent to the backend and do not follow the account (DIRECTION 19, #12).
import type { DisplayChoice } from './renderCapability';

export const CHOICE_KEY = 'amadeus_memory_display';
export const SLOW_KEY = 'amadeus_memory_cone_slow';
/** A slow machine goes straight to the plate for this long. */
export const SLOW_TTL_MS = 7 * 24 * 3_600_000;

type Store = Pick<Storage, 'getItem' | 'setItem'>;

function store(): Store | null {
  try { return typeof localStorage === 'undefined' ? null : localStorage; } catch { return null; }
}

export function readChoice(from: Store | null = store()): DisplayChoice {
  try {
    const value = from?.getItem(CHOICE_KEY);
    return value === '3d' || value === 'plate' ? value : null;
  } catch { return null; }
}

export function writeChoice(choice: Exclude<DisplayChoice, null>, to: Store | null = store()): void {
  try { to?.setItem(CHOICE_KEY, choice); } catch { /* private window: the pick lasts this visit only */ }
}

/** "This machine and this renderer": enough to tell a laptop's two GPUs apart without any identifier. */
export function machineKey(renderer: string | null): string {
  const screenSize = typeof screen === 'undefined' ? '' : `${screen.width}x${screen.height}`;
  const cores = typeof navigator === 'undefined' ? 0 : navigator.hardwareConcurrency ?? 0;
  return [renderer ?? 'unknown', screenSize, cores].join('|');
}

function readSlow(from: Store | null): Record<string, number> {
  try {
    const parsed = JSON.parse(from?.getItem(SLOW_KEY) ?? '{}') as Record<string, unknown>;
    const out: Record<string, number> = {};
    for (const [key, value] of Object.entries(parsed)) if (typeof value === 'number' && Number.isFinite(value)) out[key] = value;
    return out;
  } catch { return {}; }
}

/** When the note for this machine runs out, or `null` if there is none or it already has. */
export function readSlowUntil(key: string, now: number, from: Store | null = store()): number | null {
  const until = readSlow(from)[key];
  return until !== undefined && until > now ? until : null;
}

export function writeSlow(key: string, now: number, to: Store | null = store()): void {
  try {
    const all = readSlow(to);
    for (const [k, until] of Object.entries(all)) if (until <= now) delete all[k];
    to?.setItem(SLOW_KEY, JSON.stringify({ ...all, [key]: now + SLOW_TTL_MS }));
  } catch { /* the note is only a shortcut */ }
}

export function clearSlow(key: string, to: Store | null = store()): void {
  try {
    const all = readSlow(to);
    delete all[key];
    to?.setItem(SLOW_KEY, JSON.stringify(all));
  } catch { /* nothing stored, nothing to clear */ }
}
