import { createProgram, createTarget, deleteTarget, pickHdrFormat, FULLSCREEN_VS } from './gl';
import type { GL, Program, Target, TargetFormat } from './gl';
import { buildGlyphAtlas } from './glyphs';
import { PARCH_FS, CORRIDOR_FS, SPACE_FS, DARK_FS } from '../shaders/worlds';
import { GEAR_VS, GEAR_FS } from '../shaders/gear';
import { READOUT_FS } from '../shaders/readout';
import { SMOKE_FS, RIBBON_VS, RIBBON_FS, PIECE_VS, PIECE_FS } from '../shaders/fx';
import { BRIGHT_FS, DOWN_FS, UP_FS, COMPOSITE_FS } from '../shaders/post';
import type { FrameState, ParchState, CableState, PieceState } from '../show/frame';
import { STAGE_ASPECT } from '../show/geom';
import { MAX_GEARS, GEAR_STRIDE } from '../show/gears';
import { MAX_PIECES, PIECE_STRIDE } from '../show/pieces';
import { MAX_CABLE_VERTS } from '../show/cables';

const BLOOM_LEVELS = 5;
const MAX_RIBBON_VERTS = MAX_CABLE_VERTS;

interface Programs {
  parch: Program;
  corridor: Program;
  space: Program;
  dark: Program;
  gear: Program;
  readout: Program;
  smoke: Program;
  ribbon: Program;
  piece: Program;
  bright: Program;
  down: Program;
  up: Program;
  composite: Program;
}

export class Renderer {
  readonly canvas: HTMLCanvasElement;
  private gl: GL;
  private p!: Programs;
  private fmt!: TargetFormat;
  hdr = false;
  private scene: Target | null = null;
  private combo: Target | null = null;
  private snap: Target | null = null;
  private bloom: Target[] = [];
  private vaoEmpty!: WebGLVertexArrayObject;
  private vaoGear!: WebGLVertexArrayObject;
  private vaoRibbon!: WebGLVertexArrayObject;
  private vaoPiece!: WebGLVertexArrayObject;
  private quadBuf!: WebGLBuffer;
  private gearBuf!: WebGLBuffer;
  private ribbonBuf!: WebGLBuffer;
  private pieceBuf!: WebGLBuffer;
  private atlas!: WebGLTexture;
  private w = 0;
  private h = 0;
  private lost = false;
  private disposed = false;
  onLost: (() => void) | null = null;
  onRestored: (() => void) | null = null;

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    const gl = canvas.getContext('webgl2', {
      antialias: false,
      alpha: false,
      depth: false,
      stencil: false,
      premultipliedAlpha: false,
      preserveDrawingBuffer: false,
      powerPreference: 'high-performance',
    });
    if (!gl) throw new Error('WebGL2 不可用');
    this.gl = gl;
    canvas.addEventListener('webglcontextlost', this.handleLost, false);
    canvas.addEventListener('webglcontextrestored', this.handleRestored, false);
    this.init();
  }

  private handleLost = (e: Event): void => {
    e.preventDefault();
    this.lost = true;
    this.onLost?.();
  };

  private handleRestored = (): void => {
    this.lost = false;
    this.scene = null;
    this.combo = null;
    this.snap = null;
    this.bloom = [];
    this.w = this.h = 0;
    this.init();
    this.onRestored?.();
  };

  get isLost(): boolean {
    return this.lost;
  }

  private init(): void {
    const gl = this.gl;
    const hf = pickHdrFormat(gl);
    this.fmt = hf.fmt;
    this.hdr = hf.hdr;
    this.p = {
      parch: createProgram(gl, FULLSCREEN_VS, PARCH_FS, 'parch'),
      corridor: createProgram(gl, FULLSCREEN_VS, CORRIDOR_FS, 'corridor'),
      space: createProgram(gl, FULLSCREEN_VS, SPACE_FS, 'space'),
      dark: createProgram(gl, FULLSCREEN_VS, DARK_FS, 'dark'),
      gear: createProgram(gl, GEAR_VS, GEAR_FS, 'gear'),
      readout: createProgram(gl, FULLSCREEN_VS, READOUT_FS, 'readout'),
      smoke: createProgram(gl, FULLSCREEN_VS, SMOKE_FS, 'smoke'),
      ribbon: createProgram(gl, RIBBON_VS, RIBBON_FS, 'ribbon'),
      piece: createProgram(gl, PIECE_VS, PIECE_FS, 'piece'),
      bright: createProgram(gl, FULLSCREEN_VS, BRIGHT_FS, 'bright'),
      down: createProgram(gl, FULLSCREEN_VS, DOWN_FS, 'down'),
      up: createProgram(gl, FULLSCREEN_VS, UP_FS, 'up'),
      composite: createProgram(gl, FULLSCREEN_VS, COMPOSITE_FS, 'composite'),
    };
    this.vaoEmpty = gl.createVertexArray()!;

    this.quadBuf = gl.createBuffer()!;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.quadBuf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, 1, 1, -1, -1, 1, 1, -1, 1]), gl.STATIC_DRAW);

    this.gearBuf = gl.createBuffer()!;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.gearBuf);
    gl.bufferData(gl.ARRAY_BUFFER, MAX_GEARS * GEAR_STRIDE * 4, gl.DYNAMIC_DRAW);
    this.vaoGear = gl.createVertexArray()!;
    gl.bindVertexArray(this.vaoGear);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.quadBuf);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.gearBuf);
    for (let i = 0; i < 5; i++) {
      gl.enableVertexAttribArray(1 + i);
      gl.vertexAttribPointer(1 + i, 4, gl.FLOAT, false, GEAR_STRIDE * 4, i * 16);
      gl.vertexAttribDivisor(1 + i, 1);
    }

    this.ribbonBuf = gl.createBuffer()!;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.ribbonBuf);
    gl.bufferData(gl.ARRAY_BUFFER, MAX_RIBBON_VERTS * 16, gl.DYNAMIC_DRAW);
    this.vaoRibbon = gl.createVertexArray()!;
    gl.bindVertexArray(this.vaoRibbon);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.ribbonBuf);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 4, gl.FLOAT, false, 16, 0);

    this.pieceBuf = gl.createBuffer()!;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.pieceBuf);
    gl.bufferData(gl.ARRAY_BUFFER, MAX_PIECES * PIECE_STRIDE * 4, gl.DYNAMIC_DRAW);
    this.vaoPiece = gl.createVertexArray()!;
    gl.bindVertexArray(this.vaoPiece);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.quadBuf);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.pieceBuf);
    for (let i = 0; i < 3; i++) {
      gl.enableVertexAttribArray(1 + i);
      gl.vertexAttribPointer(1 + i, 4, gl.FLOAT, false, PIECE_STRIDE * 4, i * 16);
      gl.vertexAttribDivisor(1 + i, 1);
    }
    gl.bindVertexArray(null);

    const at = buildGlyphAtlas();
    this.atlas = gl.createTexture()!;
    gl.bindTexture(gl.TEXTURE_2D, this.atlas);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, at.width, at.height, 0, gl.RGBA, gl.UNSIGNED_BYTE, at.data);
    gl.generateMipmap(gl.TEXTURE_2D);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  }

  resize(w: number, h: number): void {
    w = Math.max(16, Math.round(w));
    h = Math.max(9, Math.round(h));
    if (w === this.w && h === this.h && this.scene) return;
    this.w = w;
    this.h = h;
    this.canvas.width = w;
    this.canvas.height = h;
    const gl = this.gl;
    deleteTarget(gl, this.scene);
    deleteTarget(gl, this.combo);
    deleteTarget(gl, this.snap);
    this.snap = null;
    this.bloom.forEach((t) => deleteTarget(gl, t));
    this.scene = createTarget(gl, w, h, this.fmt);
    this.combo = createTarget(gl, w, h, this.fmt);
    this.bloom = [];
    let bw = Math.max(1, w >> 1);
    let bh = Math.max(1, h >> 1);
    for (let i = 0; i < BLOOM_LEVELS; i++) {
      this.bloom.push(createTarget(gl, bw, bh, this.fmt));
      bw = Math.max(1, bw >> 1);
      bh = Math.max(1, bh >> 1);
    }
  }

  get size(): [number, number] {
    return [this.w, this.h];
  }

  private fullscreen(): void {
    const gl = this.gl;
    gl.bindVertexArray(this.vaoEmpty);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  private bindTarget(t: Target | null): void {
    const gl = this.gl;
    gl.bindFramebuffer(gl.FRAMEBUFFER, t ? t.fbo : null);
    gl.viewport(0, 0, t ? t.w : this.w, t ? t.h : this.h);
  }

  private drawParch(pp: ParchState, px: number): void {
    const gl = this.gl;
    const P = this.p.parch;
    gl.useProgram(P.prog);
    const u = P.u;
    gl.uniform1f(u.uAspect, STAGE_ASPECT);
    gl.uniform1f(u.uPx, px);
    gl.uniform1f(u.uZoom, pp.zoom);
    gl.uniform2fv(u.uOff, pp.off);
    gl.uniform1f(u.uTime, pp.time);
    gl.uniform1f(u.uBright, pp.bright);
    gl.uniform1f(u.uDark, pp.dark);
    gl.uniform1f(u.uCool, pp.cool);
    gl.uniform1f(u.uDoors, pp.doors);
    gl.uniform1f(u.uDoorZ, pp.doorZ);
    gl.uniform1f(u.uLattice, pp.lattice);
    gl.uniform1f(u.uPlain, pp.plain);
    gl.uniform1f(u.uPlainDark, pp.plainDark);
    gl.uniform1f(u.uSpecks, pp.specks);
    gl.uniform1f(u.uStain, pp.stain);
    gl.uniform1f(u.uLockInk, pp.lockInk);
    gl.uniform1f(u.uWarp, pp.warp);
    gl.uniform1f(u.uVpY, pp.vpY);
    gl.uniform4fv(u.uFz, pp.fz);
    gl.uniform1f(u.uSky, pp.sky);
    const ink = new Float32Array(24);
    ink.set(pp.ink.slice(0, 24));
    gl.uniform4fv(u.uInk, ink);
    gl.uniform1i(u.uInkN, Math.min(6, Math.floor(pp.ink.length / 4)));
    this.fullscreen();
  }

  private drawCables(c: CableState, front: boolean): void {
    const n = front ? c.front : c.back;
    if (n <= 0 || c.alpha <= 0) return;
    const gl = this.gl;
    const first = front ? c.back : 0;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.ribbonBuf);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, c.data, first * 4, n * 4);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.useProgram(this.p.ribbon.prog);
    gl.uniform1f(this.p.ribbon.u.uAspect, STAGE_ASPECT);
    gl.uniform1f(this.p.ribbon.u.uAlpha, c.alpha);
    gl.bindVertexArray(this.vaoRibbon);
    gl.drawArrays(gl.TRIANGLES, 0, n);
    gl.bindVertexArray(null);
  }

  private drawGears(f: FrameState, px: number): void {
    const g = f.gears;
    if (!g || g.count <= 0) return;
    const gl = this.gl;
    const n = Math.min(g.count, MAX_GEARS);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.gearBuf);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, g.data, 0, n * GEAR_STRIDE);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.useProgram(this.p.gear.prog);
    gl.uniform1f(this.p.gear.u.uAspect, STAGE_ASPECT);
    gl.uniform1f(this.p.gear.u.uPx, px);
    gl.bindVertexArray(this.vaoGear);
    gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, n);
    gl.bindVertexArray(null);
  }

  private uploadPieces(pc: PieceState): void {
    const gl = this.gl;
    const n = pc.back + pc.front;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.pieceBuf);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, pc.data, 0, n * PIECE_STRIDE);
  }

  private drawPieces(first: number, n: number, px: number): void {
    if (n <= 0 || !this.snap) return;
    const gl = this.gl;
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    const P = this.p.piece;
    gl.useProgram(P.prog);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.snap.tex);
    gl.uniform1i(P.u.uSnap, 0);
    gl.uniform1f(P.u.uAspect, STAGE_ASPECT);
    gl.uniform1f(P.u.uPx, px);
    gl.bindVertexArray(this.vaoPiece);
    // attribute offsets for a sub-range of instances
    gl.bindBuffer(gl.ARRAY_BUFFER, this.pieceBuf);
    for (let i = 0; i < 3; i++) gl.vertexAttribPointer(1 + i, 4, gl.FLOAT, false, PIECE_STRIDE * 4, first * PIECE_STRIDE * 4 + i * 16);
    gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, n);
    for (let i = 0; i < 3; i++) gl.vertexAttribPointer(1 + i, 4, gl.FLOAT, false, PIECE_STRIDE * 4, i * 16);
    gl.bindVertexArray(null);
  }

  render(f: FrameState): void {
    if (this.lost || this.disposed || !this.scene || !this.combo) return;
    const gl = this.gl;
    const P = this.p;
    const px = 1 / this.h;
    gl.disable(gl.DEPTH_TEST);
    gl.disable(gl.CULL_FACE);

    if (f.pieces) {
      if (!this.snap || this.snap.w !== this.w || this.snap.h !== this.h) {
        deleteTarget(gl, this.snap);
        this.snap = createTarget(gl, this.w, this.h, this.fmt);
      }
      this.bindTarget(this.snap);
      gl.disable(gl.BLEND);
      this.drawParch(f.pieces.snap, px);
      this.uploadPieces(f.pieces);
    }

    // ---- scene: background world, cables behind, pieces behind
    this.bindTarget(this.scene);
    gl.disable(gl.BLEND);
    if (f.bg === 'corridor' && f.corridor) {
      const c = f.corridor;
      gl.useProgram(P.corridor.prog);
      const u = P.corridor.u;
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, this.atlas);
      gl.uniform1i(u.uAtlas, 0);
      gl.uniform1f(u.uAspect, STAGE_ASPECT);
      gl.uniform1f(u.uPx, px);
      gl.uniform1f(u.uT, c.t);
      gl.uniform2fv(u.uVP, c.vp);
      gl.uniform1f(u.uTravel, c.travel);
      gl.uniform1f(u.uPTravel, c.ptravel);
      gl.uniform1f(u.uLines, c.lines);
      gl.uniform1f(u.uSpread, c.spread);
      gl.uniform1f(u.uFadeIn, c.fadeIn);
      gl.uniform1f(u.uPillar, c.pillar);
      gl.uniform1f(u.uSolid, c.solid);
      gl.uniform1f(u.uMoss, c.moss);
      gl.uniform1f(u.uCrack, c.crack);
      gl.uniform1f(u.uSoft, c.soft);
      gl.uniform1f(u.uDiss, c.diss);
      gl.uniform2fv(u.uFrontPosts, c.frontPosts);
      gl.uniform1f(u.uReveal, c.reveal);
      gl.uniform1f(u.uOnly, c.only);
      gl.uniform1f(u.uSparse, c.sparse);
      gl.uniform1f(u.uRow, c.row);
      gl.uniform1fv(u.uCells, c.cells);
      gl.uniform4fv(u.uT1, c.t1);
      gl.uniform4fv(u.uT1b, c.t1b);
      this.fullscreen();
    } else if (f.bg === 'space' && f.space) {
      const s = f.space;
      if (s.reveal < 1) {
        // the door corridor stays at the sides while the space opens from a centre slit
        this.drawParch(f.parch, px);
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      }
      gl.useProgram(P.space.prog);
      const u = P.space.u;
      gl.uniform1f(u.uAspect, STAGE_ASPECT);
      gl.uniform1f(u.uPx, px);
      gl.uniform1f(u.uT, s.t);
      gl.uniform1f(u.uReveal, s.reveal);
      gl.uniform2fv(u.uVP, s.vp);
      gl.uniform1f(u.uRoll, s.roll);
      gl.uniform1f(u.uSweepH, s.sweep);
      const burst = new Float32Array(4 * 28);
      burst.set(s.burst.slice(0, 4 * 28));
      gl.uniform4fv(u.uBurst, burst);
      gl.uniform1i(u.uBurstN, Math.min(28, Math.floor(s.burst.length / 4)));
      this.fullscreen();
      gl.disable(gl.BLEND);
    } else if (f.bg === 'dark' && f.dark) {
      const d = f.dark;
      gl.useProgram(P.dark.prog);
      const u = P.dark.u;
      gl.uniform1f(u.uAspect, STAGE_ASPECT);
      gl.uniform1f(u.uPx, px);
      gl.uniform1f(u.uT, d.t);
      gl.uniform1f(u.uPhase, d.phase);
      gl.uniform1f(u.uRot, d.rot);
      gl.uniform1f(u.uSweep, d.sweep);
      gl.uniform1f(u.uWidth, d.width);
      gl.uniform2fv(u.uArchC, d.archC);
      gl.uniform2fv(u.uArchR, d.archR);
      gl.uniform1f(u.uWhite, d.white);
      gl.uniform1f(u.uWhite2, d.white2);
      gl.uniform1f(u.uGround, d.ground);
      gl.uniform1f(u.uHelix, d.helix);
      gl.uniform1f(u.uRib, d.rib);
      gl.uniform1f(u.uWisp, d.wisp);
      gl.uniform1f(u.uHeadL, d.headL);
      gl.uniform1f(u.uHeadU, d.headU);
      gl.uniform1f(u.uHole, d.hole);
      gl.uniform1f(u.uTunnel, d.tunnel);
      gl.uniform1f(u.uFloor, d.floor);
      gl.uniform1f(u.uFront, d.front);
      gl.uniform1f(u.uLayer, 0);
      this.fullscreen();
    } else if (f.bg === 'black') {
      gl.clearColor(0.006, 0.0055, 0.005, 1);
      gl.clear(gl.COLOR_BUFFER_BIT);
    } else {
      this.drawParch(f.parch, px);
    }
    // (the back cables over the back pieces: in the exit the paper is redrawn as pieces, and the
    // cables still show over them until src 13.38)
    if (f.pieces) this.drawPieces(0, f.pieces.back, px);
    if (f.cables) this.drawCables(f.cables, false);
    if (f.gears && f.gearsBehind) this.drawGears(f, px);
    gl.disable(gl.BLEND);

    // ---- instrument over the scene -> combo
    this.bindTarget(this.combo);
    {
      const r = f.readout;
      gl.useProgram(P.readout.prog);
      const u = P.readout.u;
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, this.scene.tex);
      gl.uniform1i(u.uScene, 0);
      gl.activeTexture(gl.TEXTURE1);
      gl.bindTexture(gl.TEXTURE_2D, this.atlas);
      gl.uniform1i(u.uAtlas, 1);
      gl.uniform1f(u.uAspect, STAGE_ASPECT);
      gl.uniform1f(u.uPx, px);
      gl.uniform4fv(u.uRO, r.ro);
      gl.uniform3fv(u.uView, r.view);
      gl.uniform1fv(u.uDigA, r.digA);
      gl.uniform1fv(u.uDigB, r.digB);
      gl.uniform1fv(u.uMix, r.mix);
      const show = f.showReadout ? 1 : 0;
      const lit = new Float32Array(8);
      let avg = 0;
      for (let i = 0; i < 8; i++) {
        lit[i] = r.lit[i] * show;
        avg += lit[i];
      }
      gl.uniform1fv(u.uLit, lit);
      gl.uniform1fv(u.uFlash, r.flash);
      gl.uniform1f(u.uGlow, r.glow);
      gl.uniform1f(u.uHalo, r.halo);
      gl.uniform1f(u.uGlowAvg, (avg / 8) * Math.min(r.glow, 1.6));
      gl.uniform1f(u.uPlinth, r.plinth * show);
      gl.uniform1f(u.uGlass, r.glass * show);
      gl.uniform1f(u.uEnvDark, r.envDark);
      gl.uniform1f(u.uClear, r.clear ?? 0);
      gl.uniform1f(u.uTGlow, r.tglow ?? 0);
      gl.uniform1f(u.uRimFade, r.rimFade ?? 0);
      gl.uniform1f(u.uMode, r.mode);
      gl.uniform1f(u.uBlur, r.blur);
      gl.uniform1f(u.uLineK, r.lineK);
      gl.uniform1f(u.uTime, f.time);
      gl.uniform4fv(u.uWarp, r.warp);
      gl.uniform1f(u.uWarpY, r.warpY);
      gl.uniform3fv(u.uWarpB, r.warpB ?? [0, 0, 0]);
      gl.uniform1f(u.uBoardOnly, r.boardOnly);
      this.fullscreen();
      gl.activeTexture(gl.TEXTURE0);
    }
    // the T9 light band passes in front of the outer tubes: its light again, added over them
    if (f.bg === 'dark' && f.dark && f.dark.front > 0) {
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.ONE, gl.ONE);
      gl.useProgram(P.dark.prog);
      gl.uniform1f(P.dark.u.uLayer, 1);
      this.fullscreen();
      gl.uniform1f(P.dark.u.uLayer, 0);
      gl.disable(gl.BLEND);
    }
    if (f.cables) this.drawCables(f.cables, true);
    if (f.gears && !f.gearsBehind) this.drawGears(f, px);
    if (f.smoke && f.smoke.a > 0) {
      const s = f.smoke;
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      gl.useProgram(P.smoke.prog);
      gl.uniform1f(P.smoke.u.uAspect, STAGE_ASPECT);
      gl.uniform1f(P.smoke.u.uK, s.k);
      gl.uniform1f(P.smoke.u.uA, s.a);
      gl.uniform1f(P.smoke.u.uTime, s.time);
      gl.uniform4fv(P.smoke.u.uRO, s.ro);
      this.fullscreen();
    }
    if (f.pieces) this.drawPieces(f.pieces.back, f.pieces.front, px);
    gl.disable(gl.BLEND);

    // ---- bloom chain
    const B = this.bloom;
    this.bindTarget(B[0]);
    gl.useProgram(P.bright.prog);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.combo.tex);
    gl.uniform1i(P.bright.u.uTex, 0);
    gl.uniform2f(P.bright.u.uTexel, 1 / this.w, 1 / this.h);
    gl.uniform1f(P.bright.u.uThresh, this.hdr ? 1.02 : 0.9);
    this.fullscreen();
    gl.useProgram(P.down.prog);
    gl.uniform1i(P.down.u.uTex, 0);
    for (let i = 1; i < B.length; i++) {
      this.bindTarget(B[i]);
      gl.bindTexture(gl.TEXTURE_2D, B[i - 1].tex);
      gl.uniform2f(P.down.u.uTexel, 1 / B[i - 1].w, 1 / B[i - 1].h);
      this.fullscreen();
    }
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE);
    gl.useProgram(P.up.prog);
    gl.uniform1i(P.up.u.uTex, 0);
    for (let i = B.length - 1; i > 0; i--) {
      this.bindTarget(B[i - 1]);
      gl.bindTexture(gl.TEXTURE_2D, B[i].tex);
      gl.uniform2f(P.up.u.uTexel, 0.5 / B[i].w, 0.5 / B[i].h);
      gl.uniform1f(P.up.u.uWeight, 1.0);
      this.fullscreen();
    }
    gl.disable(gl.BLEND);

    // ---- final composite
    this.bindTarget(null);
    {
      const q = f.post;
      const u = P.composite.u;
      gl.useProgram(P.composite.prog);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, this.combo.tex);
      gl.uniform1i(u.uCombo, 0);
      gl.activeTexture(gl.TEXTURE1);
      gl.bindTexture(gl.TEXTURE_2D, B[0].tex);
      gl.uniform1i(u.uBloom, 1);
      gl.uniform1f(u.uBloomK, q.bloom);
      gl.uniform1f(u.uFlash, q.flash);
      gl.uniform3fv(u.uFlashColor, q.flashColor);
      gl.uniform1f(u.uCA, q.ca);
      gl.uniform1f(u.uGrain, q.grain);
      gl.uniform1f(u.uFrame, f.grainFrame);
      gl.uniform1f(u.uFade, q.fade);
      gl.uniform1f(u.uVig, q.vig);
      gl.uniform1f(u.uExposure, q.exposure);
      gl.uniform2fv(u.uCenter, q.center);
      gl.uniform1f(u.uTear, q.tear);
      gl.uniform1f(u.uTearSeed, q.tearSeed);
      gl.uniform3fv(u.uSlipA, q.slipA);
      gl.uniform3fv(u.uSlipB, q.slipB);
      gl.uniform1f(u.uZoom, q.zoom);
      gl.uniform1f(u.uLens, q.lens);
      gl.uniform1f(u.uFlare, q.flare);
      gl.uniform1f(u.uRed, q.red);
      gl.uniform1f(u.uStreak, q.streak);
      gl.uniform1f(u.uPin, q.pin);
      gl.uniform1f(u.uCAMode, q.caMode);
      gl.uniform1f(u.uCACyan, q.caCyan);
      gl.uniform1f(u.uFloorDark, q.floorDark);
      gl.uniform1f(u.uFlareStreak, q.flareStreak);
      gl.uniform1f(u.uFlareRays, q.flareRays);
      gl.uniform1f(u.uVeil, q.veil);
      gl.uniform1f(u.uAspect, STAGE_ASPECT);
      this.fullscreen();
      gl.activeTexture(gl.TEXTURE0);
    }
    gl.bindVertexArray(null);
  }

  /** Block until the GPU has finished (used only by the deterministic test hook). */
  finish(): void {
    if (!this.lost) this.gl.finish();
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    const gl = this.gl;
    this.canvas.removeEventListener('webglcontextlost', this.handleLost);
    this.canvas.removeEventListener('webglcontextrestored', this.handleRestored);
    if (gl.isContextLost()) return;
    deleteTarget(gl, this.scene);
    deleteTarget(gl, this.combo);
    deleteTarget(gl, this.snap);
    this.bloom.forEach((t) => deleteTarget(gl, t));
    Object.values(this.p).forEach((pr) => gl.deleteProgram(pr.prog));
    gl.deleteBuffer(this.quadBuf);
    gl.deleteBuffer(this.gearBuf);
    gl.deleteBuffer(this.ribbonBuf);
    gl.deleteBuffer(this.pieceBuf);
    gl.deleteVertexArray(this.vaoEmpty);
    gl.deleteVertexArray(this.vaoGear);
    gl.deleteVertexArray(this.vaoRibbon);
    gl.deleteVertexArray(this.vaoPiece);
    gl.deleteTexture(this.atlas);
    this.scene = this.combo = this.snap = null;
    this.bloom = [];
  }
}
