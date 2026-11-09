/*
 * Large windows (amadeus-ops design/20261108-ui-inventory.md §1, §4.6): every size on the sheet is a fixed pixel, so at
 * 1920 the reading column was a strip with the middle empty. Instead of a second set of sizes, the installed app zooms
 * its webview as a browser does (layout, viewport units, measuring and the 3D view all agree), keeping the layout
 * near 1440 × 860 CSS pixels: 1920 × 1080 shows at 125 %, 2560 × 1440 at 165 %. Never below 100 %, never above 200 %,
 * in 5 % steps so a resize does not set it trembling. A dev server in a browser is left alone.
 */

const LAYOUT = { width: 1440, height: 860 };

/** The zoom for a window this many CSS pixels wide and high at 100 %. */
export function fitFactor(width: number, height: number): number {
  const fit = Math.min(width / LAYOUT.width, height / LAYOUT.height);
  return Math.min(2, Math.max(1, Math.floor(fit * 20) / 20));
}

export function fitWindow(): void {
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return;
  let applied = 1; // the webview starts at 100 % on every launch
  let timer = 0;
  const fit = async () => {
    // innerWidth is in zoomed CSS pixels; times the zoom it is the window at 100 %
    const next = fitFactor(window.innerWidth * applied, window.innerHeight * applied);
    if (next === applied) return;
    try {
      const { getCurrentWebview } = await import('@tauri-apps/api/webview');
      await getCurrentWebview().setZoom(next);
      applied = next;
    } catch {
      // Without the permission the sheet simply keeps its pixel sizes.
    }
  };
  window.addEventListener('resize', () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => { void fit(); }, 160);
  });
  void fit();
}
