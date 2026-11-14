import { packAsset } from '../assetPack';

/*
 * The first entrance after the app starts used to stutter (U 11-09): the workstation's pictures were fetched and
 * decoded for the first time on the very frames the pen was sweeping. While the login waits for the password, in idle
 * time, the pictures the workstation opens on are fetched and decoded instead, and held here so the decoded copies are
 * still there when the sheet is drawn. Nothing is read from the user's data: the sheet's textures and her portrait
 * (when the asset pack has it) only.
 */

const held: HTMLImageElement[] = [];
let started = false;

function sources(worldline: 'steins_gate' | 'beta'): string[] {
  const sheet = worldline === 'beta'
    ? ['/assets/sheet/beta-wall.webp', '/assets/sheet/specimen-1.webp', '/assets/sheet/specimen-6.webp', '/assets/sheet/specimen-4.webp', '/assets/sheet/specimen-3.webp']
    : ['/assets/sheet/sg-slate.webp', '/assets/sheet/ring-3.webp'];
  const portrait = ['/assets/images/kurisu_normal1.png', '/assets/images/kurisu_normal2.png', '/assets/images/kurisu_normal3.png', '/assets/images/kurisu_eyes_closed1.png']
    .map((path) => packAsset(path))
    .filter((url): url is string => Boolean(url));
  return ['/assets/sheet/grain.webp', '/assets/sheet/eraser.webp', ...sheet, ...portrait];
}

/** Fetch and decode, one picture at a time in idle moments, so the login itself never hitches. Once per app run. */
export function prewarmWorkstation(worldline: 'steins_gate' | 'beta', delay = 900): () => void {
  if (started) return () => {};
  let cancelled = false;
  const queue = sources(worldline);
  const idle = (fn: () => void) => (typeof window.requestIdleCallback === 'function'
    ? window.requestIdleCallback(fn, { timeout: 600 })
    : window.setTimeout(fn, 16));
  const next = () => {
    if (cancelled) return;
    const src = queue.shift();
    if (!src) return;
    const img = new Image();
    img.decoding = 'async';
    img.src = src;
    held.push(img);
    const done = () => idle(next);
    if (typeof img.decode === 'function') img.decode().catch(() => {}).finally(done);
    else { img.onload = done; img.onerror = done; }
  };
  const timer = window.setTimeout(() => { started = true; idle(next); }, delay);
  return () => { cancelled = true; window.clearTimeout(timer); };
}
