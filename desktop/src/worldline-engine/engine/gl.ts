// Minimal WebGL2 helpers: programs, render targets, a fullscreen triangle.

export type GL = WebGL2RenderingContext;

export interface Program {
  prog: WebGLProgram;
  u: Record<string, WebGLUniformLocation | null>;
}

/** A program whose compile and link were issued but not yet queried. */
export interface PendingProgram {
  prog: WebGLProgram;
  vs: WebGLShader;
  fs: WebGLShader;
  label: string;
}

function shader(gl: GL, type: number, src: string, label: string): WebGLShader {
  const sh = gl.createShader(type);
  if (!sh) throw new Error(`createShader failed (${label})`);
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  return sh;
}

/** Issue compile + link without reading any status, so a driver with KHR_parallel_shader_compile
 *  can do the work off the calling thread. Status queries are what make a compile synchronous. */
export function beginProgram(gl: GL, vs: string, fs: string, label: string): PendingProgram {
  const v = shader(gl, gl.VERTEX_SHADER, vs, label + '.vs');
  const f = shader(gl, gl.FRAGMENT_SHADER, fs, label + '.fs');
  const prog = gl.createProgram();
  if (!prog) throw new Error('createProgram failed');
  gl.attachShader(prog, v);
  gl.attachShader(prog, f);
  gl.linkProgram(prog);
  return { prog, vs: v, fs: f, label };
}

/** Non-blocking readiness check (always true without the extension: the next query just waits). */
export function programSettled(gl: GL, p: PendingProgram, ext: KHR_parallel_shader_compile | null): boolean {
  return !ext || gl.getProgramParameter(p.prog, ext.COMPLETION_STATUS_KHR) === true;
}

/** Read link status (and compile logs on failure) and collect the uniform table. */
export function finishProgram(gl: GL, p: PendingProgram): Program {
  const { prog, vs, fs, label } = p;
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS) && !gl.isContextLost()) {
    for (const [sh, tag] of [[vs, '.vs'], [fs, '.fs']] as const) {
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
        const log = gl.getShaderInfoLog(sh) ?? '';
        gl.deleteShader(vs);
        gl.deleteShader(fs);
        throw new Error(`Shader compile error (${label}${tag}):\n${log}`);
      }
    }
    gl.deleteShader(vs);
    gl.deleteShader(fs);
    throw new Error(`Program link error (${label}):\n${gl.getProgramInfoLog(prog)}`);
  }
  gl.deleteShader(vs);
  gl.deleteShader(fs);
  const u: Record<string, WebGLUniformLocation | null> = {};
  const n = gl.getProgramParameter(prog, gl.ACTIVE_UNIFORMS) as number;
  for (let i = 0; i < n; i++) {
    const info = gl.getActiveUniform(prog, i);
    if (!info) continue;
    const name = info.name.replace(/\[0\]$/, '');
    u[name] = gl.getUniformLocation(prog, info.name);
  }
  return { prog, u };
}

export function createProgram(gl: GL, vs: string, fs: string, label: string): Program {
  return finishProgram(gl, beginProgram(gl, vs, fs, label));
}

export interface Target {
  fbo: WebGLFramebuffer;
  tex: WebGLTexture;
  w: number;
  h: number;
}

export interface TargetFormat {
  internal: number;
  format: number;
  type: number;
}

export function createTarget(gl: GL, w: number, h: number, fmt: TargetFormat): Target {
  const tex = gl.createTexture()!;
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texImage2D(gl.TEXTURE_2D, 0, fmt.internal, w, h, 0, fmt.format, fmt.type, null);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  const fbo = gl.createFramebuffer()!;
  gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
  const status = gl.checkFramebufferStatus(gl.FRAMEBUFFER);
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  if (status !== gl.FRAMEBUFFER_COMPLETE) {
    gl.deleteFramebuffer(fbo);
    gl.deleteTexture(tex);
    throw new Error('Framebuffer incomplete: 0x' + status.toString(16));
  }
  return { fbo, tex, w, h };
}

export function deleteTarget(gl: GL, t: Target | null | undefined): void {
  if (!t) return;
  gl.deleteFramebuffer(t.fbo);
  gl.deleteTexture(t.tex);
}

/** Pick an HDR render format if the device supports rendering to it. */
export function pickHdrFormat(gl: GL): { fmt: TargetFormat; hdr: boolean } {
  const ext = gl.getExtension('EXT_color_buffer_float') || gl.getExtension('EXT_color_buffer_half_float');
  if (ext) {
    const fmt = { internal: gl.RGBA16F, format: gl.RGBA, type: gl.HALF_FLOAT };
    try {
      const t = createTarget(gl, 4, 4, fmt);
      deleteTarget(gl, t);
      return { fmt, hdr: true };
    } catch {
      /* fall through */
    }
  }
  return { fmt: { internal: gl.RGBA8, format: gl.RGBA, type: gl.UNSIGNED_BYTE }, hdr: false };
}

export const FULLSCREEN_VS = `#version 300 es
precision highp float;
out vec2 vUv;
void main(){
  vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
  vUv = p;
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`;
