import { apiFetch } from '../apiBase';

/*
 * The local backend's data, passcode, usage and diagnostics routes (amadeus-ops reports 210, 211, 212, 216), typed for
 * the system page and the recovery screen. Every failure is an ApiError carrying the backend's `detail.code`; a request
 * that never reached the backend is `unreachable`, and a route this build's backend does not have is a 404.
 */

export class ApiError extends Error {
  constructor(readonly status: number, readonly code: string) { super(code); }
}

async function failure(response: Response): Promise<ApiError> {
  let code = `http_${response.status}`;
  try {
    const detail = (await response.json())?.detail;
    if (detail && typeof detail === 'object' && typeof detail.code === 'string') code = detail.code;
  } catch { /* not JSON: keep the status */ }
  return new ApiError(response.status, code);
}

async function call(path: string, init?: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await apiFetch(path, init);
  } catch {
    throw new ApiError(0, 'unreachable');
  }
  if (!response.ok) throw await failure(response);
  return response;
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  return (await call(path, init)).json() as Promise<T>;
}

const send = (method: string, body?: unknown): RequestInit => body === undefined ? { method } : {
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
};

/* ---------- data status, backups ---------- */

export type BackupReason = 'manual' | 'weekly' | 'pre-upgrade' | 'pre-restore';
export type Backup = {
  id: string;
  time: string | null;
  reason: BackupReason | string;
  size: number;
  app_version: string | null;
  can_restore: boolean;
  detail?: string;
};
export type DataStatus = { status: 'ok' | 'corrupt' | 'restore_failed' | string; detail: string | null; backups: Backup[] };

export const dataStatus = () => json<DataStatus>('/api/data/status');
export const listBackups = () => json<Backup[]>('/api/data/backups');
export const createBackup = () => json<{ id: string }>('/api/data/backups', send('POST'));
export const restoreBackup = (id: string) => json<{ restart_required: boolean }>(`/api/data/backups/${encodeURIComponent(id)}/restore`, send('POST'));
export const deleteBackup = (id: string) => json<unknown>(`/api/data/backups/${encodeURIComponent(id)}`, send('DELETE'));

/* ---------- the startup passcode ---------- */

export type AuthState = { passcode_set: boolean; passcode_error?: string };
export const authState = () => json<AuthState>('/api/auth/state');
/** Sets, changes (`next` a string) or turns off (`next` null) the passcode; `current` is null when none is set. */
export const setPasscode = (current: string | null, next: string | null) =>
  json<unknown>('/api/auth/passcode', send('PUT', { current, new: next }));

/* ---------- export ---------- */

export type ExportRequest =
  | { kind: 'conversations'; format: 'md' | 'json'; worldline: 'sg' | 'beta' | 'all' }
  | { kind: 'memory'; format: 'json'; worldline: 'sg' | 'beta' | 'all' };

/** The zip and the name the backend gave it. */
export async function exportArchive(request: ExportRequest): Promise<{ blob: Blob; filename: string }> {
  const query = new URLSearchParams({ kind: request.kind, format: request.format, worldline: request.worldline });
  const response = await call(`/api/data/export?${query}`);
  const disposition = response.headers.get('Content-Disposition') ?? '';
  const filename = /filename="?([^";]+)"?/i.exec(disposition)?.[1] ?? `amadeus-${request.kind}.zip`;
  return { blob: await response.blob(), filename };
}

/* ---------- usage ---------- */

export type Pricing = { currency: string; input: number; output: number; cached_input: number } & Record<string, unknown>;
export type UsageGroup = {
  provider_id: string;
  model_id: string;
  calls: number;
  input_tokens: number | null;
  output_tokens: number | null;
  cached_tokens: number | null;
  unreported_calls: number;
  unestimated_calls: number;
  pricing: Pricing | null;
  estimated_cost: number | null;
};
export type UsageReport = {
  estimate: boolean;
  range: 'month' | 'conversation' | 'all';
  tz: number;
  groups: UsageGroup[];
  totals_by_currency: Record<string, number>;
  unreported_calls: number;
  unpriced_models: { provider_id: string; model_id: string }[];
};
export function usageReport(range: UsageReport['range'], conversationId?: string): Promise<UsageReport> {
  const query = new URLSearchParams({ range, tz: String(-new Date().getTimezoneOffset()) });
  if (range === 'conversation' && conversationId) query.set('conversation_id', conversationId);
  return json<UsageReport>(`/api/data/usage?${query}`);
}

/* ---------- where the data lives (report 212) ---------- */

export type DataLocation = {
  path: string;
  default_path: string;
  source: 'env' | 'file' | 'default' | string;
  movable: boolean;
  previous_path: string | null;
  previous_exists: boolean;
};
export type DiskCategory =
  | 'conversations_memory' | 'voice_models' | 'speech_models' | 'embedding_models' | 'asset_pack'
  | 'pi_components' | 'logs' | 'backups' | 'other';
export type DiskUsage = { categories: Partial<Record<DiskCategory, number>>; total_bytes: number };
export const dataLocation = () => json<DataLocation>('/api/data/location');
export const diskUsage = () => json<DiskUsage>('/api/data/disk');

/* ---------- a bundle for reporting a problem (report 216) ---------- */

export const diagnosticsBundle = () => json<{ path: string; size: number }>('/api/diagnostics/bundle', send('POST'));

/* ---------- reading the numbers ---------- */

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value >= 100 ? value.toFixed(0) : value >= 10 ? value.toFixed(1) : value.toFixed(2)} ${units[unit]}`;
}

/** v0.1.0; a development build's backups say `dev`. */
export const formatVersion = (version: string | null) => (!version ? '' : version === 'dev' ? '开发版' : `v${version}`);

/** 2026-11-09 14:05, in the reader's own time. */
export function formatStamp(iso: string | null): string {
  if (!iso) return '时间不明';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '时间不明';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
