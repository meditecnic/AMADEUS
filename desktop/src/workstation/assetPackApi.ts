import { apiFetch } from '../apiBase';
import { ApiError } from './dataApi';

/*
 * Optional original asset pack management endpoints under /api/data/asset-pack.
 * Supports inspecting and installing from a local directory/zip path (JSON) or a
 * raw binary application/zip upload (File/Blob body, never multipart).
 */

export type AssetPackStatus = {
  installed: boolean;
  complete: boolean;
  movable: boolean;
  path: string;
  source: string;
  name: string | null;
  version: string | null;
  size_bytes: number;
  required_count: number;
  present_count: number;
  missing_files: string[];
};

export type AssetPackInvalidFile = {
  path: string;
  reason: string;
};

export type AssetPackPreview = {
  emotion: string;
  path: string;
  data_url: string;
};

export type AssetPackInspectResult = {
  valid: boolean;
  source_type: 'directory' | 'zip';
  stripped_root: string | null;
  name: string;
  version: string;
  required_count: number;
  found_count: number;
  missing_files: string[];
  invalid_files: AssetPackInvalidFile[];
  errors: string[];
  warnings: string[];
  total_bytes: number;
  previews: AssetPackPreview[];
};

async function failure(response: Response): Promise<ApiError> {
  let code = `http_${response.status}`;
  let parsedDetail: Record<string, unknown> | undefined;
  try {
    const raw = (await response.json()) as { detail?: unknown } | null;
    const detail = raw?.detail;
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      parsedDetail = detail as Record<string, unknown>;
      if (typeof parsedDetail.code === 'string') {
        code = parsedDetail.code;
      } else if (
        Array.isArray(parsedDetail.errors) &&
        parsedDetail.errors[0] &&
        typeof (parsedDetail.errors[0] as { code?: unknown }).code === 'string'
      ) {
        code = (parsedDetail.errors[0] as { code: string }).code;
      }
    } else if (typeof detail === 'string' && detail) {
      parsedDetail = { message: detail };
    }
  } catch {
    /* not JSON: keep http_<status> */
  }
  return new ApiError(response.status, code, parsedDetail);
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await apiFetch(path, init);
  } catch {
    throw new ApiError(0, 'unreachable');
  }
  if (!response.ok) throw await failure(response);
  return response.json() as Promise<T>;
}

function sourceRequest(source: string | Blob): RequestInit {
  if (typeof source === 'string') {
    return {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_path: source }),
    };
  }
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/zip' },
    body: source,
  };
}

export const assetPackStatus = () => requestJson<AssetPackStatus>('/api/data/asset-pack');

export const inspectAssetPack = (source: string | Blob) =>
  requestJson<AssetPackInspectResult>('/api/data/asset-pack/inspect', sourceRequest(source));

export const installAssetPack = (source: string | Blob) =>
  requestJson<AssetPackStatus>('/api/data/asset-pack/install', sourceRequest(source));

export const uninstallAssetPack = () =>
  requestJson<AssetPackStatus>('/api/data/asset-pack', { method: 'DELETE' });
