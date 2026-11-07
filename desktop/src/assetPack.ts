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

const normalize = (path: string) => path.replace(/\\/g, '/').replace(/^\/+/, '');
const present = (path: string) => available && (listed === null || listed.has(normalize(path)));

export function loadAssetPack(): Promise<boolean> {
  if (probe) return probe;
  probe = (async () => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
    try {
      const response = await apiFetch(`${PACK_PATH}/manifest.json`, { signal: controller.signal });
      available = response.ok;
      // A pack may hold only part of the material (only her portraits, say); then the rest falls back as if absent.
      if (available) {
        const manifest: unknown = await response.json().catch(() => null);
        const files = (manifest as { files?: unknown } | null)?.files;
        listed = Array.isArray(files) ? new Set(files.filter((f): f is string => typeof f === 'string').map(normalize)) : null;
      }
    } catch {
      available = false;
    } finally {
      window.clearTimeout(timer);
    }
    const root = document.documentElement;
    const cssUrl = (path: string) => `url("${apiUrl(PACK_PATH)}/${path}")`;
    if (present('bg/SG0_IBG006D.png')) root.style.setProperty('--pack-login-bg', cssUrl('bg/SG0_IBG006D.png'));
    if (present('assets/ui/ring_config.png')) root.style.setProperty('--pack-ring', cssUrl('assets/ui/ring_config.png'));
    root.dataset.assetPack = available ? 'on' : 'off';
    return available;
  })();
  return probe;
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
  return `${apiUrl(PACK_PATH)}/${normalize(path)}`;
}
