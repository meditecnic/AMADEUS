import { apiFetch, apiUrl } from './apiBase';
/** Original Steins;Gate material (portraits, BGM, SFX, boot animation, login art) is not shipped with
 *  the app. It is an optional pack that the user installs into the data directory; the backend serves
 *  it under /api/asset-pack/. Without it every caller falls back to self-made defaults. */

const PACK_PATH = '/api/asset-pack';
const PROBE_TIMEOUT_MS = 1500;

let available = false;
/** Files the manifest lists; null when it lists none, which means every file of the full pack is there. */
let listed: Set<string> | null = null;
let probe: Promise<boolean> | null = null;
let revision = 0;
let fetchSeq = 0;
const listeners = new Set<() => void>();

const normalize = (path: string) => path.replace(/\\/g, '/').replace(/^\/+/, '');
const present = (path: string) => available && (listed === null || listed.has(normalize(path)));

function applyDocumentPackState() {
  const root = document.documentElement;
  const applyCssAsset = (prop: string, path: string) => {
    const url = packAsset(path);
    if (url) root.style.setProperty(prop, `url("${url}")`);
    else root.style.removeProperty(prop);
  };
  applyCssAsset('--pack-login-bg', 'bg/SG0_IBG006D.png');
  applyCssAsset('--pack-ring', 'assets/ui/ring_config.png');
  root.dataset.assetPack = available ? 'on' : 'off';
}

function notifyListeners() {
  for (const listener of Array.from(listeners)) {
    listener();
  }
}

async function fetchManifest(isReload: boolean): Promise<boolean> {
  const seq = ++fetchSeq;
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  let nextAvailable = false;
  let nextListed: Set<string> | null = null;
  try {
    const init: RequestInit = { signal: controller.signal };
    if (isReload) init.cache = 'no-store';
    const response = await apiFetch(`${PACK_PATH}/manifest.json`, init);
    nextAvailable = response.ok;
    // A pack may hold only part of the material (only her portraits, say); then the rest falls back as if absent.
    if (nextAvailable) {
      const manifest: unknown = await response.json().catch(() => null);
      // No pack answers 200 {"installed": false}; an older backend answered 404.
      if ((manifest as { installed?: unknown } | null)?.installed === false) {
        nextAvailable = false;
      } else {
        const files = (manifest as { files?: unknown } | null)?.files;
        nextListed = Array.isArray(files)
          ? new Set(files.filter((f): f is string => typeof f === 'string').map(normalize))
          : null;
      }
    }
  } catch {
    nextAvailable = false;
  } finally {
    window.clearTimeout(timer);
  }
  if (seq !== fetchSeq) return available;
  available = nextAvailable;
  listed = nextAvailable ? nextListed : null;
  if (isReload) revision += 1;
  applyDocumentPackState();
  if (isReload) notifyListeners();
  return available;
}

export function loadAssetPack(): Promise<boolean> {
  if (probe) return probe;
  probe = fetchManifest(false);
  return probe;
}

/** Re-probe the backend asset pack manifest (no-store), increment revision, update CSS vars, and notify subscribers. */
export function reloadAssetPack(): Promise<boolean> {
  probe = fetchManifest(true);
  return probe;
}

export function getAssetPackRevision(): number {
  return revision;
}

export function subscribeAssetPack(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function hasAssetPack(): boolean {
  return available;
}

/** Whether the installed pack holds this file (a pack without a file list holds them all). */
export function hasPackFile(path: string): boolean {
  return present(path);
}

/** Pack-relative path (a leading slash is ignored) → URL, or null when the pack is absent or lacks that file. */
export function packAsset(path: string): string | null {
  if (!present(path)) return null;
  const base = `${apiUrl(PACK_PATH)}/${normalize(path)}`;
  return revision > 0 ? `${base}?v=${revision}` : base;
}
