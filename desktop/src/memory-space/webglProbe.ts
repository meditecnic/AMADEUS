import type { GpuProbe } from './renderCapability';

let cached: GpuProbe | null = null;

/** Ask once whether WebGL2 exists and what is behind it. The probe context is given back right away. */
export function probeWebGL(): GpuProbe {
  if (cached) return cached;
  cached = { webgl2: false, renderer: null };
  if (typeof document === 'undefined' || typeof WebGL2RenderingContext === 'undefined') return cached;
  try {
    const gl = document.createElement('canvas').getContext('webgl2');
    if (!gl) return cached;
    const info = gl.getExtension('WEBGL_debug_renderer_info');
    const renderer = String(info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
    gl.getExtension('WEBGL_lose_context')?.loseContext();
    cached = { webgl2: true, renderer };
  } catch { /* stays "no WebGL2" */ }
  return cached;
}

/** For tests. */
export function resetProbe(): void { cached = null; }
