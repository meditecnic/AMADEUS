import { useEffect, useRef, useState } from 'react';
import { useReducedMotion } from '../design-system/useReducedMotion';
import { Controller, parseReading, type FrameState, type Renderer } from '../worldline-engine';
import { DIVERGENCE, WORLDLINE_NAME } from './Nixie';
import { ShiftOverlay } from './ShiftOverlay';
import { ShiftTransaction } from './shiftTransaction';
import { ShiftPerformance } from './shiftPerformance';
import { acquireShiftStage, prewarmShiftStage, shiftStageAvailable, type ShiftStageLease } from './shiftStage';
import './WorldlineShift.css';

/** Presentation timings (ms). None of them gates the transaction: publication follows the real lock. */
export const SHIFT_TIMING = {
  /** The overlay covers the workstation (the menu underneath closes once covered). */
  cover: 320,
  /** A cold start waits at most this long for the GPU stage, then presents with CSS. */
  coldWait: 2500,
  /** Skip during the GPU show: dip to dark, cut, lift. */
  dipIn: 140,
  dipOut: 260,
  /** Natural success end: the settled frame holds, then dims and dissolves into the workstation. */
  hold: 260,
  exit: 720,
  /** Skipped success (already dark): straight dissolve. */
  skipExit: 420,
  /** CSS presentation: time for the tubes to tune before the dissolve. */
  cssHold: 1250,
  cssExit: 520,
  /** Failure: the readable source frame, then the dissolve. */
  failHold: 900,
  failExit: 420,
  /** Reduced motion: a static beat on the result, no dissolve. */
  reducedHold: 400,
} as const;

const fade = (el: Element, frames: Keyframe[], duration: number, easing: string) => {
  if (typeof el.animate === 'function') el.animate(frames, { duration, easing, fill: 'forwards' });
};

export function WorldlineShift({ transaction, onDone, onCovered }: {
  transaction: ShiftTransaction;
  onDone: () => void;
  /** The overlay fully covers the workstation. */
  onCovered?: () => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const skip = useRef<() => void>(() => {});
  const reduce = useReducedMotion();
  const reduced = useRef(reduce);
  reduced.current = reduce;
  const fallback = useRef<() => void>(() => {});
  const done = useRef(onDone);
  done.current = onDone;
  const covered = useRef(onCovered);
  covered.current = onCovered;
  const [cssReading, setCssReading] = useState(DIVERGENCE[transaction.from]);

  useEffect(() => {
    const el = root.current!;
    const lease = transaction.attach();
    let active = true;
    const timers: number[] = [];
    const later = (fn: () => void, ms: number) => {
      timers.push(window.setTimeout(() => { if (active) fn(); }, ms));
    };
    let raf = 0;
    let started = false;
    let ending = false;
    let dipping = false;
    /** The user asked to skip (click/Esc); fallbacks and the CSS lock skip internally. */
    let userSkipped = false;
    let css = true;
    let stage: ShiftStageLease | null = null;
    let gpu: Renderer | null = null;
    let observer: ResizeObserver | null = null;
    let lastCss = DIVERGENCE[transaction.from];
    const monitor = new ShiftPerformance();
    const origin = parseReading(DIVERGENCE[transaction.from])!;
    const target = parseReading(DIVERGENCE[transaction.to])!;
    const forceFallback = import.meta.env.DEV && new URLSearchParams(window.location.search).get('force-fallback') === '1';
    const reading = el.querySelector<HTMLElement>('[data-shift-reading]')!;
    const status = el.querySelector<HTMLElement>('[data-shift-status]')!;

    const releaseGPU = (keep: boolean) => {
      observer?.disconnect();
      observer = null;
      if (gpu) {
        gpu.onLost = null;
        gpu = null;
      }
      if (stage) {
        const s = stage;
        stage = null;
        // A healthy stage stays warm for the next shift; a failing one is dropped with its context.
        if (keep) s.release(); else s.discard();
      }
    };
    const useCSS = (reason: string) => {
      if (!active || css) return;
      css = true;
      el.dataset.renderer = 'css';
      el.dataset.fallback = reason;
      delete el.dataset.dip;
      dipping = false;
      releaseGPU(reason === 'reduced-motion');
      // Same controller/run/outcome: pending goes to GATE, ready draws final lock.
      controller.skip();
    };
    const controller = new Controller({
      render(frame: FrameState) {
        if (!active) return;
        if (gpu) {
          try { gpu.render(frame); }
          catch { useCSS('render-error'); return; }
        }
        const locked = frame.readout.locked.every(Boolean);
        reading.textContent = frame.readout.shown;
        el.dataset.shot = frame.shot;
        el.dataset.locked = String(locked);
        if (css) {
          // The static instrument shows what is locked, never the scramble.
          const shown = locked ? frame.readout.shown : DIVERGENCE[transaction.from];
          if (shown !== lastCss) { lastCss = shown; setCssReading(shown); }
        }
        transaction.observe(lease, frame);
        if (!css && monitor.sample(performance.now(), document.visibilityState !== 'hidden')) {
          useCSS('low-fps');
        }
      },
      finish() {},
    }, origin, {
      onChange(snapshot) {
        if (!active) return;
        el.dataset.state = snapshot.waiting ? 'waiting' : snapshot.outcome === 'fail' ? 'failed' : snapshot.phase === 'ended' ? 'settled' : 'running';
        status.textContent = snapshot.waiting ? 'SYNC · 等待目标世界线确认'
          : snapshot.outcome === 'fail' ? '跃迁失败 · 已保留源世界线'
            : snapshot.phase === 'ended' ? '世界线已锁定' : '世界线跃迁中';
        if (snapshot.phase === 'ended' && !ending) {
          ending = true;
          leave(snapshot.outcome === 'fail', userSkipped);
        }
      },
    });

    /** Hand back to the workstation without a hard cut. */
    const leave = (failed: boolean, skipped: boolean) => {
      const finish = (hold: number, duration: number, dim: boolean) => later(() => {
        el.dataset.exit = '';
        if (duration > 0) {
          if (dim && stage) fade(stage.canvas, [{ filter: 'brightness(1)' }, { filter: 'brightness(.42)' }], duration * 0.7, 'ease-out');
          fade(el, [{ opacity: 1 }, { opacity: 0 }], duration, 'cubic-bezier(.45, 0, .4, 1)');
        }
        later(() => done.current(), duration);
      }, hold);
      if (reduced.current) return finish(SHIFT_TIMING.reducedHold, 0, false);
      if (failed) return finish(SHIFT_TIMING.failHold, SHIFT_TIMING.failExit, false);
      if (css) return skipped ? finish(200, SHIFT_TIMING.skipExit, false) : finish(SHIFT_TIMING.cssHold, SHIFT_TIMING.cssExit, false);
      if (!skipped) return finish(SHIFT_TIMING.hold, SHIFT_TIMING.exit, true);
      // Skipped into the result: under the dark veil, then dissolve.
      if (el.dataset.dip === 'in') return finish(0, SHIFT_TIMING.skipExit, false);
      el.dataset.dip = 'in';
      finish(SHIFT_TIMING.dipIn, SHIFT_TIMING.skipExit, false);
    };

    const begin = (withGPU: boolean, reason?: string) => {
      if (!active || started) return;
      started = true;
      if (withGPU && stage) {
        const s = stage;
        gpu = s.renderer;
        css = false;
        el.prepend(s.canvas);
        el.dataset.renderer = 'webgl2';
        delete el.dataset.fallback;
        gpu.onLost = () => useCSS('context-lost');
        const size = () => {
          const w = s.canvas.clientWidth || el.clientWidth;
          const width = Math.min(1600, Math.round(w * Math.min(window.devicePixelRatio || 1, 1.5)));
          gpu!.resize(width, width * 9 / 16);
        };
        try { size(); }
        catch { releaseGPU(false); gpu = null; css = true; }
        if (!css) {
          observer = new ResizeObserver(() => {
            if (!gpu || !active) return;
            try { size(); controller.draw(); }
            catch { useCSS('resize-error'); }
          });
          observer.observe(s.canvas);
        }
      }
      if (css) {
        el.dataset.renderer = 'css';
        el.dataset.fallback = reason ?? 'unavailable';
      }
      controller.start({ origin, target, scenario: 'success', reduced: css, outcome: transaction.outcome });
      el.dataset.started = '';
      // Static lock after the real outcome. The same controller draws its final frame.
      transaction.outcome.then(() => { if (active && css) controller.skip(); });
    };

    /** Runs before the overlay's first paint: a warm stage starts on this very frame. */
    const boot = () => {
      raf = 0;
      if (!active || started) return;
      if (reduced.current) return begin(false, 'reduced-motion');
      if (forceFallback) return begin(false, 'forced-preview');
      const ready = acquireShiftStage();
      if (ready) { stage = ready; return begin(true); }
      if (!shiftStageAvailable()) return begin(false, 'unavailable');
      // Cold start: the overlay is already up and the page stays responsive while the stage
      // compiles; bounded, then the same run continues in CSS.
      el.dataset.boot = 'warming';
      later(() => begin(false, 'warming'), SHIFT_TIMING.coldWait);
      void prewarmShiftStage({ urgent: true }).then(() => {
        if (!active || started) return;
        const s = acquireShiftStage();
        if (s) { stage = s; begin(true); } else begin(false, 'unavailable');
      });
    };

    skip.current = () => {
      if (!active || ending) return;
      userSkipped = true;
      if (!started) {
        // Skipped before the first frame: start with whatever is ready and skip at once.
        if (raf) cancelAnimationFrame(raf);
        raf = 0;
        const s = !reduced.current && !forceFallback ? acquireShiftStage() : null;
        if (s) { stage = s; begin(true); } else begin(false, reduced.current ? 'reduced-motion' : 'skipped');
        controller.skip();
        return;
      }
      if (css || reduced.current) { controller.skip(); return; }
      if (dipping) return;
      dipping = true;
      el.dataset.dip = 'in';
      later(() => {
        dipping = false;
        controller.skip();
        if (ending || css) return;
        el.dataset.dip = 'out';
        later(() => { if (el.dataset.dip === 'out') delete el.dataset.dip; }, SHIFT_TIMING.dipOut);
      }, SHIFT_TIMING.dipIn);
    };
    fallback.current = () => useCSS('reduced-motion');
    const key = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.isComposing || event.keyCode === 229) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      skip.current();
    };
    window.addEventListener('keydown', key, true);
    el.dataset.renderer = 'pending';
    // StrictMode's discarded setup is cleaned up before this frame, so it never takes the stage.
    raf = requestAnimationFrame(boot);
    later(() => covered.current?.(), reduced.current ? 0 : SHIFT_TIMING.cover);
    return () => {
      active = false;
      if (raf) cancelAnimationFrame(raf);
      transaction.detach(lease);
      controller.dispose();
      releaseGPU(true);
      timers.forEach(id => window.clearTimeout(id));
      window.removeEventListener('keydown', key, true);
      skip.current = () => {};
      fallback.current = () => {};
    };
  }, [transaction]);

  useEffect(() => { if (reduce) fallback.current(); }, [reduce]);

  return (
    <div ref={root} className="worldline-shift" data-renderer="pending" role="status" aria-live="polite"
      aria-label={`世界线跃迁：${WORLDLINE_NAME[transaction.to]}`} onClick={() => skip.current()}>
      <ShiftOverlay from={transaction.from} to={transaction.to} reading={cssReading} />
      <span className="shift-sr" data-shift-reading aria-hidden="true">{DIVERGENCE[transaction.from]}</span>
      <div className="shift-veil" aria-hidden="true" />
      <p className="shift-status" data-shift-status>SYNC · 等待目标世界线确认</p>
      <button type="button" className="shift-skip" onClick={event => { event.stopPropagation(); skip.current(); }}>点击或 Esc 跳过</button>
    </div>
  );
}
