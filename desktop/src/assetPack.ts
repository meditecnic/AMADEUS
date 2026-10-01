/** Original Steins;Gate material (portraits, BGM, SFX, boot animation, login art) is not shipped with
 *  the app. It is an optional pack that the user installs into the data directory; the backend serves
 *  it under /api/asset-pack/. Without it every caller falls back to self-made defaults. */

const PACK_BASE = '/api/asset-pack';
const PROBE_TIMEOUT_MS = 1500;

let available = false;
let probe: Promise<boolean> | null = null;

export function loadAssetPack(): Promise<boolean> {
  if (probe) return probe;
  probe = (async () => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
    try {
      const response = await fetch(`${PACK_BASE}/manifest.json`, { signal: controller.signal });
      available = response.ok;
    } catch {
      available = false;
    } finally {
      window.clearTimeout(timer);
    }
    const root = document.documentElement;
    const cssUrl = (path: string) => `url("${PACK_BASE}/${path}")`;
    if (available) {
      root.style.setProperty('--pack-login-bg', cssUrl('bg/SG0_IBG006D.png'));
      root.style.setProperty('--pack-ring', cssUrl('assets/ui/ring_config.png'));
    }
    root.dataset.assetPack = available ? 'on' : 'off';
    return available;
  })();
  return probe;
}

export function hasAssetPack(): boolean {
  return available;
}

/** Pack-relative path (a leading slash is ignored) → URL, or null when no pack is installed. */
export function packAsset(path: string): string | null {
  if (!available) return null;
  return `${PACK_BASE}/${path.replace(/^\/+/, '')}`;
}
