/// <reference lib="webworker" />
// Rasterizes the cathode atlas off the main thread (OffscreenCanvas 2D). The bytes are the same
// deterministic image the renderer would otherwise build itself on first use.
import { buildGlyphAtlas } from '../worldline-engine/engine/glyphs';

self.onmessage = () => {
  try {
    const atlas = buildGlyphAtlas();
    (self as unknown as DedicatedWorkerGlobalScope).postMessage(atlas, [atlas.data.buffer]);
  } catch (error) {
    (self as unknown as DedicatedWorkerGlobalScope).postMessage({ error: String(error) });
  }
};
