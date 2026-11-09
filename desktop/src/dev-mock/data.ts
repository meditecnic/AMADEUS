/*
 * The dev mock's data routes (amadeus-ops reports 210, 211, 212, 216): backups and restore, the startup passcode,
 * usage, export, where the data lives and the diagnostics bundle. Paths are synthetic; nothing is read from or written
 * to disk, and the export is a small zip made here.
 */

export type Reply = { status: number; body: unknown; delayMs?: number; headers?: Record<string, string>; raw?: Uint8Array };
type Frame = Record<string, unknown>;

export type DataSeed = {
  /** 'ok' | 'corrupt' | 'restore_failed' */
  status?: string;
  detail?: string;
  backups?: SeedBackup[];
};
type SeedBackup = { id: string; size: number; app_version?: string | null; can_restore?: boolean; detail?: string };
type Backup = { id: string; time: string | null; reason: string; size: number; app_version: string | null; can_restore: boolean; detail?: string };

const ok = (body: unknown, status = 200): Reply => ({ status, body });
const fail = (status: number, code: string): Reply => ok({ detail: { code } }, status);

const ROOT = 'C:\\Users\\lab\\AppData\\Roaming\\Amadeus';

const DEFAULT_BACKUPS: SeedBackup[] = [
  { id: '20261108T224512000000Z-manual', size: 18_874_368, app_version: '0.1.0' },
  { id: '20261107T030000000000Z-weekly', size: 18_350_080, app_version: '0.1.0' },
  { id: '20261031T030000000000Z-weekly', size: 17_301_504, app_version: '0.1.0' },
  { id: '20261028T091522000000Z-pre-upgrade', size: 16_252_928, app_version: '0.1.0-rc.2' },
  { id: '20261024T030000000000Z-weekly', size: 0, app_version: null, can_restore: false, detail: 'Backup validation failed: checksum mismatch: memory.sqlite3' },
];

/** 20261108T224512000000Z → 2026-11-08T22:45:12+00:00 */
const timeOf = (id: string) => {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})/.exec(id);
  return m ? `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}+00:00` : null;
};
const toBackup = (seed: SeedBackup): Backup => {
  const restorable = seed.can_restore !== false;
  return {
    id: seed.id, time: restorable ? timeOf(seed.id) : null, reason: seed.id.split('Z-')[1] ?? 'manual', size: seed.size,
    app_version: seed.app_version ?? null, can_restore: restorable, ...(seed.detail ? { detail: seed.detail } : {}),
  };
};

export function createDataStore(seed: DataSeed | undefined, passcode: string | null) {
  return {
    status: seed?.status ?? 'ok',
    detail: seed?.detail ?? null,
    backups: (seed?.backups ?? DEFAULT_BACKUPS).map(toBackup),
    pendingRestore: null as string | null,
    passcode,
  };
}
export type DataStore = ReturnType<typeof createDataStore>;

/** A corrupt database closes every route but /health, the data routes and the diagnostics bundle, as the backend does. */
export function dataUnavailable(store: DataStore, path: string): Reply | null {
  if (store.status !== 'corrupt' || path === '/health' || path.startsWith('/api/data/') || path.startsWith('/api/diagnostics/')) return null;
  return fail(503, 'data_unavailable');
}

export function handleData(url: URL, method: string, body: Frame, store: DataStore): Reply | null {
  const path = decodeURIComponent(url.pathname);

  if (path === '/api/auth/state' && method === 'GET') return ok({ passcode_set: store.passcode !== null });
  if (path === '/api/auth/passcode' && method === 'PUT') {
    const current = typeof body.current === 'string' ? body.current : null;
    const next = body.new === null || body.new === undefined ? null : String(body.new);
    if (next !== null && !next.trim()) return fail(422, 'empty_passcode');
    if (store.passcode !== null && current !== store.passcode) return { ...fail(401, 'invalid_passcode'), delayMs: 500 };
    store.passcode = next;
    return { ...ok({ status: 'success' }), delayMs: 500 };
  }

  if (path === '/api/data/status' && method === 'GET') return ok({ status: store.status, detail: store.detail, backups: store.backups });
  if (path === '/api/data/backups' && method === 'GET') return ok(store.backups);
  if (path === '/api/data/backups' && method === 'POST') {
    if (store.status === 'corrupt') return fail(500, 'backup_failed');
    const now = new Date();
    const id = `${now.toISOString().replace(/[-:]/g, '').replace(/\.(\d{3})Z$/, '$1000Z')}-manual`;
    const backup = toBackup({ id, size: 18_903_040, app_version: '0.1.0' });
    store.backups.unshift(backup);
    return { ...ok({ format: 1, id, created_at: backup.time, app_version: '0.1.0', reason: 'manual', files: [], unreadable: [] }), delayMs: 1400 };
  }
  const restore = /^\/api\/data\/backups\/([^/]+)\/restore$/.exec(path);
  if (restore && method === 'POST') {
    const backup = store.backups.find((row) => row.id === restore[1]);
    if (!backup) return fail(404, 'backup_not_found');
    if (!backup.can_restore) return fail(400, 'invalid_backup');
    store.pendingRestore = backup.id;
    return { ...ok({ restart_required: true }), delayMs: 600 };
  }
  const one = /^\/api\/data\/backups\/([^/]+)$/.exec(path);
  if (one && method === 'DELETE') {
    if (store.pendingRestore === one[1]) return fail(409, 'restore_pending');
    const index = store.backups.findIndex((row) => row.id === one[1]);
    if (index < 0) return fail(404, 'backup_not_found');
    store.backups.splice(index, 1);
    return ok({ status: 'success' });
  }

  if (path === '/api/data/usage' && method === 'GET') {
    const range = url.searchParams.get('range') ?? 'month';
    if (range === 'conversation' && !url.searchParams.get('conversation_id')) return fail(422, 'conversation_id_required');
    return { ...ok(usage(range, Number(url.searchParams.get('tz') ?? 0))), delayMs: 350 };
  }
  if (path === '/api/data/export' && method === 'GET') {
    const kind = url.searchParams.get('kind');
    const format = url.searchParams.get('format') ?? 'json';
    const worldline = url.searchParams.get('worldline') ?? 'all';
    if ((kind !== 'conversations' && kind !== 'memory') || (kind === 'memory' && format !== 'json')) return fail(422, 'invalid_export_scope');
    const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
    const filename = `amadeus-${stamp}-${kind}-${worldline}.zip`;
    const note = `AMADEUS dev mock: a sample ${kind} export (${format}, ${worldline}). The real backend writes your data here.\n`;
    return {
      status: 200, body: null, delayMs: 700, raw: zip([{ name: `README.${format === 'md' ? 'md' : 'txt'}`, text: note }]),
      headers: { 'Content-Type': 'application/zip', 'Content-Disposition': `attachment; filename="${filename}"` },
    };
  }
  if (path === '/api/data/location' && method === 'GET') {
    return ok({ path: ROOT, default_path: ROOT, source: 'default', movable: true, previous_path: null, previous_exists: false });
  }
  if (path === '/api/data/disk' && method === 'GET') {
    const categories = {
      conversations_memory: 19_293_184, voice_models: 431_947_776, speech_models: 0, embedding_models: 0,
      asset_pack: 1_395_864_371, pi_components: 0, logs: 3_250_585, backups: 70_778_880, other: 412_160,
    };
    return { ...ok({ categories, total_bytes: Object.values(categories).reduce((a, b) => a + b, 0) }), delayMs: 450 };
  }
  if (path === '/api/diagnostics/bundle' && method === 'POST') {
    const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
    return { ...ok({ path: `${ROOT}\\diagnostics\\amadeus-diagnostics-${stamp}.zip`, size: 186_419 }), delayMs: 1600 };
  }
  return null;
}

/* ---------- usage: one month's worth, scaled for the other ranges ---------- */

type Price = { currency: string; input: number; output: number; cached_input: number };
const USAGE: Array<{ provider_id: string; model_id: string; calls: number; input: number | null; output: number | null; cached: number | null; pricing: Price | null }> = [
  { provider_id: 'deepseek', model_id: 'deepseek-chat', calls: 214, input: 1_284_310, output: 196_472, cached: 820_144, pricing: { currency: 'CNY', input: 2, output: 8, cached_input: 0.5 } },
  { provider_id: 'deepseek', model_id: 'deepseek-reasoner', calls: 12, input: 96_220, output: 41_880, cached: 30_016, pricing: { currency: 'CNY', input: 4, output: 16, cached_input: 1 } },
  { provider_id: 'openai', model_id: 'gpt-4.1-mini', calls: 18, input: 142_608, output: 22_391, cached: 0, pricing: { currency: 'USD', input: 0.4, output: 1.6, cached_input: 0.1 } },
  { provider_id: 'openai-compatible', model_id: 'local-model', calls: 37, input: null, output: null, cached: null, pricing: null },
];

function usage(range: string, tz: number) {
  const scale = range === 'all' ? 3.4 : range === 'conversation' ? 0.12 : 1;
  const totals: Record<string, number> = {};
  const groups = USAGE.map((row) => {
    const n = (v: number | null) => v === null ? null : Math.round(v * scale);
    const calls = Math.max(1, Math.round(row.calls * scale));
    const [input, output, cached] = [n(row.input), n(row.output), n(row.cached)];
    const reported = input !== null;
    let cost: number | null = null;
    if (row.pricing && reported) {
      cost = ((input! - (cached ?? 0)) * row.pricing.input + output! * row.pricing.output + (cached ?? 0) * row.pricing.cached_input) / 1_000_000;
      totals[row.pricing.currency] = (totals[row.pricing.currency] ?? 0) + cost;
    }
    return {
      provider_id: row.provider_id, model_id: row.model_id, calls, input_tokens: input, output_tokens: output, cached_tokens: cached,
      unreported_calls: reported ? 0 : calls, unestimated_calls: row.pricing && reported ? 0 : calls, pricing: row.pricing, estimated_cost: cost,
    };
  });
  return {
    estimate: true, range, tz, groups, totals_by_currency: totals,
    unreported_calls: groups.reduce((sum, g) => sum + g.unreported_calls, 0),
    unpriced_models: groups.filter((g) => g.pricing === null).map(({ provider_id, model_id }) => ({ provider_id, model_id })),
  };
}

/* ---------- a stored (uncompressed) zip ---------- */

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
const crc32 = (bytes: Uint8Array) => {
  let c = 0xffffffff;
  for (const b of bytes) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
};

function zip(files: { name: string; text: string }[]): Uint8Array {
  const encoder = new TextEncoder();
  const parts: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;
  for (const file of files) {
    const name = encoder.encode(file.name);
    const data = encoder.encode(file.text);
    const crc = crc32(data);
    const local = new DataView(new ArrayBuffer(30));
    [[0, 0x04034b50, 4], [4, 20, 2], [6, 0x0800, 2], [8, 0, 2], [14, crc, 4], [18, data.length, 4], [22, data.length, 4], [26, name.length, 2]]
      .forEach(([at, value, size]) => size === 4 ? local.setUint32(at, value, true) : local.setUint16(at, value, true));
    const entry = new DataView(new ArrayBuffer(46));
    [[0, 0x02014b50, 4], [4, 20, 2], [6, 20, 2], [8, 0x0800, 2], [16, crc, 4], [20, data.length, 4], [24, data.length, 4], [28, name.length, 2], [42, offset, 4]]
      .forEach(([at, value, size]) => size === 4 ? entry.setUint32(at, value, true) : entry.setUint16(at, value, true));
    parts.push(new Uint8Array(local.buffer), name, data);
    central.push(new Uint8Array(entry.buffer), name);
    offset += 30 + name.length + data.length;
  }
  const size = central.reduce((sum, part) => sum + part.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  [[0, 0x06054b50, 4], [8, files.length, 2], [10, files.length, 2], [12, size, 4], [16, offset, 4]]
    .forEach(([at, value, bytes]) => bytes === 4 ? end.setUint32(at, value, true) : end.setUint16(at, value, true));
  const all = [...parts, ...central, new Uint8Array(end.buffer)];
  const out = new Uint8Array(all.reduce((sum, part) => sum + part.length, 0));
  let at = 0;
  for (const part of all) { out.set(part, at); at += part.length; }
  return out;
}
