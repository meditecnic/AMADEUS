/**
 * Web fonts for the workstation, loaded while the login terminal waits.
 *
 * The first login after a page load fetched and decoded some thirty font files (the 7.5 MB reading face
 * among them) right in the middle of the entrance, and every arrival re-shaped and re-laid the freshly
 * mounted workstation while the sheet was sweeping. Later logins were smooth because those faces were
 * already decoded. Now the workstation remembers which faces it ended up using (family, weight, style and
 * unicode-range subset) and the login page loads exactly those in idle time; a short seed covers a first run.
 */

const STORE = 'amadeus_pc_ws_fonts';
const MAX_FACES = 160;
/** At most this many faces in flight, so the warm-up never competes with the login screen. */
const BATCH = 6;

/** Faces the workstation is known to draw on its first screen. Only used until it has reported its own. */
const SEED: [font: string, text: string][] = [
  ['400 16px "LXGW Neo XiHei"', '说第一句话吧'],
  ['900 16px "Noto Serif JP"', '牧瀬紅莉栖'],
  ['400 16px "Noto Serif JP"', 'まきせくりす'],
  ['400 16px "Noto Serif SC"', '新的观测记录世界线'],
  ['400 16px "Noto Sans SC"', '世界线会话记忆设置连接发送'],
  ['500 16px Oxanium', 'AMADEUS'],
  ['400 16px "IBM Plex Mono"', 'SYNC 0123456789'],
  ['500 16px "IBM Plex Mono"', 'SYNC 0123456789'],
  ['400 16px "EB Garamond"', 'Amadeus'],
  ['500 16px "EB Garamond"', 'Amadeus'],
  ['italic 400 16px "EB Garamond"', 'Amadeus'],
];

function hash(text: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 0x01000193);
  return (h >>> 0).toString(36);
}

/** One @font-face rule: family, weight, style and which unicode-range subset. */
export function fontFaceKey(face: Pick<FontFace, 'family' | 'weight' | 'style' | 'unicodeRange'>): string {
  const range = face.unicodeRange && face.unicodeRange !== 'U+0-10FFFF' ? hash(face.unicodeRange) : '*';
  return `${face.family.replace(/["']/g, '')}|${face.weight}|${face.style}|${range}`;
}

function faces(): FontFace[] {
  const set = typeof document !== 'undefined' ? document.fonts : undefined;
  if (!set || typeof set.forEach !== 'function') return [];
  const out: FontFace[] = [];
  set.forEach(f => out.push(f));
  return out;
}

function stored(): string[] {
  try {
    const list: unknown = JSON.parse(localStorage.getItem(STORE) || '[]');
    return Array.isArray(list) ? list.filter((k): k is string => typeof k === 'string') : [];
  } catch {
    return [];
  }
}

/** Called once the workstation has settled: every face it has drawn so far goes first in the list. */
export function rememberWorkstationFonts(): void {
  const used = faces().filter(f => f.status === 'loaded').map(fontFaceKey);
  if (!used.length) return;
  const list = [...new Set([...used, ...stored()])].slice(0, MAX_FACES);
  try { localStorage.setItem(STORE, JSON.stringify(list)); } catch { /* warm-up is best effort */ }
}

const idle = () => new Promise<void>(resolve => {
  const ric = (window as Window & { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number }).requestIdleCallback;
  if (ric) ric(() => resolve(), { timeout: 300 });
  else window.setTimeout(resolve, 16);
});

/** Load the remembered faces (or the seed) in idle time. Returns a cancel function. */
export function warmWorkstationFonts(delay = 400): () => void {
  const all = faces();
  if (!all.length) return () => {};
  let cancelled = false;
  const run = async () => {
    const wanted = new Set(stored());
    if (wanted.size) {
      const pending = all.filter(f => f.status === 'unloaded' && wanted.has(fontFaceKey(f)));
      for (let i = 0; i < pending.length && !cancelled; i += BATCH) {
        await idle();
        if (cancelled) return;
        await Promise.allSettled(pending.slice(i, i + BATCH).map(f => f.load()));
      }
      return;
    }
    for (const [font, text] of SEED) {
      await idle();
      if (cancelled) return;
      await document.fonts.load(font, text).catch(() => []);
    }
  };
  const timer = window.setTimeout(() => { void run(); }, delay);
  return () => { cancelled = true; window.clearTimeout(timer); };
}
