import type { Reply } from './data';

/*
 * Dev-mock state & handlers for the optional original asset pack:
 * - Default state is UNINSTALLED so /api/asset-pack/manifest.json returns 404 (matching real boot without a pack).
 * - Inspecting or installing a synthetic local path or .zip upload flips the mock client store between absent and installed.
 * - Zero copyrighted assets or URLs: mock image/audio responses return minimal synthetic placeholders.
 */

const EMOTIONS = [
  'neutral',
  'tsundere',
  'embarrassed',
  'intellectual',
  'happy',
  'surprised',
  'annoyed',
  'disappointed',
  'sad',
] as const;

const PREVIEW_FILES: Record<(typeof EMOTIONS)[number], string> = {
  neutral: 'assets/images/kurisu_normal1.png',
  tsundere: 'assets/images/kurisu_sided_angry1.png',
  embarrassed: 'assets/images/kurisu_sided_blush1.png',
  intellectual: 'assets/images/kurisu_sided_thinking1.png',
  happy: 'assets/images/kurisu_happy1.png',
  surprised: 'assets/images/kurisu_sided_surprised1.png',
  annoyed: 'assets/images/kurisu_annoyed1.png',
  disappointed: 'assets/images/kurisu_disappointed1.png',
  sad: 'assets/images/kurisu_sad1.png',
};

const REQUIRED_SPRITES: string[] = [
  ...Object.values(PREVIEW_FILES).flatMap((p) => {
    const stem = p.slice(0, -'1.png'.length);
    return [`${stem}1.png`, `${stem}2.png`, `${stem}3.png`];
  }),
  'assets/images/kurisu_eyes_closed1.png',
  'assets/images/kurisu_sided_eyes_closed1.png',
];

const REQUIRED_BGM: string[] = [
  'audio/bgm/Amadeus.ogg',
  'audio/bgm/Future of spirit.ogg',
  'audio/bgm/Logical theme.ogg',
  'audio/bgm/Messenger from zero.ogg',
  'audio/bgm/Messenger main theme.ogg',
];

export const MOCK_REQUIRED_FILES = [...REQUIRED_SPRITES, ...REQUIRED_BGM];

// 1x1 pixel valid transparent RGBA PNG (synthetic, 67 bytes).
const SYNTHETIC_PNG = new Uint8Array([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
  0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
  0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
  0x08, 0x06, 0x00, 0x00, 0x00, 0x1f, 0x15, 0xc4, 0x89,
  0x00, 0x00, 0x00, 0x0a, 0x49, 0x44, 0x41, 0x54,
  0x78, 0x9c, 0x63, 0x00, 0x01, 0x00, 0x00, 0x05, 0x00, 0x01,
  0x0d, 0x0a, 0x2d, 0xb4,
  0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4e, 0x44,
  0xae, 0x42, 0x60, 0x82,
]);

// Minimal synthetic PNG data URL matching SYNTHETIC_PNG.
const SYNTHETIC_PNG_DATA_URL =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVR4nGMAAQAABQABDQottAAAAABJRU5ErkJggg==';

// Minimal OggS page capture pattern placeholder for dev-mock asset serving.
const SYNTHETIC_OGG = new Uint8Array([
  0x4f, 0x67, 0x67, 0x53, 0x00, 0x02, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00,
  0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
  0x00, 0x00, 0x00,
]);

export type AssetPackMockStore = {
  installed: boolean;
  movable: boolean;
  path: string;
  source: string;
  name: string | null;
  version: string | null;
  sizeBytes: number;
  missingFiles: string[];
};

export type AssetPackRequestMeta = {
  rawBody?: Uint8Array;
  contentType?: string;
};

export function createAssetPackStore(movable = true): AssetPackMockStore {
  return {
    installed: false,
    movable,
    path: '/Users/mock/AmadeusData/asset-pack',
    source: 'data_dir',
    name: null,
    version: null,
    sizeBytes: 0,
    missingFiles: [...MOCK_REQUIRED_FILES],
  };
}

const ok = (body: unknown, status = 200): Reply => ({ status, body });
const fail = (status: number, code: string, message?: string, extra?: Record<string, unknown>): Reply =>
  ok({ detail: { code, ...(message ? { message } : {}), ...extra } }, status);

function statusPayload(store: AssetPackMockStore) {
  const requiredCount = MOCK_REQUIRED_FILES.length;
  const missing = store.installed ? [...store.missingFiles] : [...MOCK_REQUIRED_FILES];
  const presentCount = Math.max(0, requiredCount - missing.length);
  return {
    installed: store.installed,
    complete: store.installed && missing.length === 0,
    movable: store.movable,
    path: store.path,
    source: store.source,
    name: store.installed ? store.name : null,
    version: store.installed ? store.version : null,
    size_bytes: store.installed ? store.sizeBytes : 0,
    required_count: requiredCount,
    present_count: store.installed ? presentCount : 0,
    missing_files: store.installed ? missing : [...MOCK_REQUIRED_FILES],
  };
}

function buildInspectOutcome(
  body: Record<string, unknown>,
  meta?: AssetPackRequestMeta,
): { ok: true; result: Record<string, unknown>; sizeBytes: number } | { ok: false; reply: Reply } {
  const isBinaryUpload = Boolean(
    meta?.rawBody !== undefined ||
    meta?.contentType?.startsWith('application/zip') ||
    meta?.contentType?.startsWith('application/octet-stream'),
  );

  if (isBinaryUpload) {
    const bytes = meta?.rawBody ?? new Uint8Array(0);
    if (bytes.byteLength === 0) {
      return { ok: false, reply: fail(422, 'empty_upload', '上传的压缩包为空') };
    }
    const isZipMagic = bytes.byteLength >= 4 && bytes[0] === 0x50 && bytes[1] === 0x4b;
    if (!isZipMagic) {
      return { ok: false, reply: fail(422, 'invalid_archive', '压缩包格式无效') };
    }
    return {
      ok: true,
      sizeBytes: Math.max(bytes.byteLength, 48_120_000),
      result: {
        valid: true,
        source_type: 'zip',
        stripped_root: null,
        name: 'Amadeus Optional Asset Pack (Mock)',
        version: '1.0.0',
        required_count: MOCK_REQUIRED_FILES.length,
        found_count: MOCK_REQUIRED_FILES.length,
        missing_files: [],
        invalid_files: [],
        errors: [],
        warnings: [],
        total_bytes: Math.max(bytes.byteLength, 48_120_000),
        previews: EMOTIONS.map((emotion) => ({
          emotion,
          path: PREVIEW_FILES[emotion],
          data_url: SYNTHETIC_PNG_DATA_URL,
        })),
      },
    };
  }

  const contentType = meta?.contentType?.toLowerCase() ?? '';
  if (contentType && !contentType.includes('application/json')) {
    return { ok: false, reply: fail(422, 'unsupported_content_type', '不支持的 Content-Type') };
  }

  const rawPath = typeof body.source_path === 'string' ? body.source_path.trim() : '';
  if (!rawPath) {
    return { ok: false, reply: fail(422, 'invalid_request', '请提供 source_path 或上传 .zip') };
  }
  const isAbsolute = rawPath.startsWith('/') || /^[A-Za-z]:[\\/]/.test(rawPath);
  if (!isAbsolute) {
    return { ok: false, reply: fail(422, 'path_not_absolute', '路径必须是绝对路径') };
  }
  if (rawPath.includes('missing') || rawPath.includes('not-found')) {
    return { ok: false, reply: fail(422, 'source_not_found', '找不到指定目录或压缩包') };
  }

  const isIncomplete = rawPath.includes('incomplete') || rawPath.includes('partial');
  const sourceType = rawPath.toLowerCase().endsWith('.zip') ? 'zip' : 'directory';
  const missingFiles = isIncomplete ? ['assets/images/kurisu_sad3.png', 'audio/bgm/Amadeus.ogg'] : [];
  const totalBytes = isIncomplete ? 31_200_000 : 48_120_000;

  return {
    ok: true,
    sizeBytes: totalBytes,
    result: {
      valid: !isIncomplete,
      source_type: sourceType,
      stripped_root: sourceType === 'zip' ? 'amadeus-pack' : null,
      name: 'Amadeus Optional Asset Pack (Mock)',
      version: '1.0.0',
      required_count: MOCK_REQUIRED_FILES.length,
      found_count: MOCK_REQUIRED_FILES.length - missingFiles.length,
      missing_files: missingFiles,
      invalid_files: [],
      errors: isIncomplete ? ['缺少必需素材文件，无法通过完整性校验。'] : [],
      warnings: [],
      total_bytes: totalBytes,
      previews: EMOTIONS.map((emotion) => ({
        emotion,
        path: PREVIEW_FILES[emotion],
        data_url: SYNTHETIC_PNG_DATA_URL,
      })),
    },
  };
}

export function handleAssetPack(
  url: URL,
  method: string,
  body: Record<string, unknown>,
  store: AssetPackMockStore,
  meta?: AssetPackRequestMeta,
): Reply | null {
  const path = decodeURIComponent(url.pathname);

  if (path === '/api/data/asset-pack') {
    if (method === 'GET') return ok(statusPayload(store));
    if (method === 'DELETE') {
      if (!store.movable) {
        return fail(409, 'asset_pack_not_movable', '当前素材目录由环境变量固定，不能卸载');
      }
      if (url.search || Object.keys(body).length > 0 || (meta?.rawBody?.byteLength ?? 0) > 0) {
        return fail(422, 'invalid_delete_request', 'DELETE 请求不接受查询参数或请求体');
      }
      store.installed = false;
      store.name = null;
      store.version = null;
      store.sizeBytes = 0;
      store.missingFiles = [...MOCK_REQUIRED_FILES];
      return ok(statusPayload(store));
    }
    return fail(405, 'method_not_allowed');
  }

  if (path === '/api/data/asset-pack/inspect' && method === 'POST') {
    const outcome = buildInspectOutcome(body, meta);
    if (!outcome.ok) return outcome.reply;
    return ok(outcome.result);
  }

  if (path === '/api/data/asset-pack/install' && method === 'POST') {
    if (!store.movable) {
      return fail(409, 'asset_pack_not_movable', '当前素材目录由环境变量固定，不能安装或覆盖');
    }
    const outcome = buildInspectOutcome(body, meta);
    if (!outcome.ok) return outcome.reply;
    if (!outcome.result.valid) {
      return fail(422, 'invalid_asset_pack', '素材包校验未通过', { inspect: outcome.result });
    }
    store.installed = true;
    store.name = String(outcome.result.name || 'Amadeus Optional Asset Pack (Mock)');
    store.version = String(outcome.result.version || '1.0.0');
    store.sizeBytes = outcome.sizeBytes;
    store.missingFiles = [];
    return ok(statusPayload(store));
  }

  if (path.startsWith('/api/asset-pack/')) {
    if (!store.installed) {
      return fail(404, 'optional_asset_pack_absent');
    }
    const rel = path.slice('/api/asset-pack/'.length);
    if (rel === 'manifest.json') {
      return ok({
        format: 1,
        name: store.name ?? 'Amadeus Optional Asset Pack (Mock)',
        version: store.version ?? '1.0.0',
      });
    }
    if (rel.endsWith('.png')) {
      return {
        status: 200,
        body: null,
        raw: SYNTHETIC_PNG,
        headers: { 'Content-Type': 'image/png' },
      };
    }
    if (rel.endsWith('.ogg')) {
      return {
        status: 200,
        body: null,
        raw: SYNTHETIC_OGG,
        headers: { 'Content-Type': 'audio/ogg' },
      };
    }
    return fail(404, 'asset_not_found');
  }

  return null;
}
