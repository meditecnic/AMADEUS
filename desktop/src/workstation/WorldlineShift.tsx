import { useEffect, useRef } from 'react';
import { useReducedMotion } from '../design-system/useReducedMotion';
import { Controller, Renderer, parseReading, type FrameState } from '../worldline-engine';
import { DIVERGENCE } from './Nixie';
import { ShiftOverlay } from './ShiftOverlay';
import { ShiftTransaction } from './shiftTransaction';
import { ShiftPerformance } from './shiftPerformance';
import './WorldlineShift.css';

export function WorldlineShift({ transaction, onDone }: { transaction: ShiftTransaction; onDone: () => void }) {
  const root = useRef<HTMLDivElement>(null);
  const skip = useRef<() => void>(() => {});
  const reduce = useReducedMotion();
  const reduced = useRef(reduce);
  reduced.current = reduce;
  const fallback = useRef<() => void>(() => {});
  const done = useRef(onDone);
  done.current = onDone;

  useEffect(() => {
    const el = root.current!;
    const lease = transaction.attach();
    let active = true;
    let timer = 0;
    let ending = false;
    let gpu: Renderer | null = null;
    let css = true;
    let observer: ResizeObserver | null = null;
    // StrictMode replays effects. A released WebGL context cannot be reused by
    // its second setup, so each presentation lease owns a fresh canvas.
    const canvas = document.createElement('canvas');
    canvas.className = 'shift-canvas';
    canvas.setAttribute('aria-hidden', 'true');
    el.prepend(canvas);
    let attemptedGPU = false;
    const monitor = new ShiftPerformance();
    const releaseGPU = () => {
      observer?.disconnect();
      observer = null;
      if (gpu) {
        gpu.onLost = null;
        gpu.onRestored = null;
        gpu.dispose();
        gpu = null;
      }
      // Release the context as well as GL objects, including partial initialization.
      if (attemptedGPU) {
        canvas.getContext('webgl2')?.getExtension('WEBGL_lose_context')?.loseContext();
        attemptedGPU = false;
      }
    };
    const useCSS = (reason: string) => {
      if (!active || css) return;
      css = true;
      el.dataset.renderer = 'css';
      el.dataset.fallback = reason;
      releaseGPU();
      // Same controller/run/outcome: pending goes to GATE, ready draws final lock.
      controller.skip();
    };
    const reading = el.querySelector<HTMLElement>('[data-shift-reading]')!;
    const status = el.querySelector<HTMLElement>('[data-shift-status]')!;
    const controller = new Controller({
      render(frame: FrameState) {
        if (!active) return;
        if (gpu) {
          try { gpu.render(frame); }
          catch { useCSS('render-error'); return; }
        }
        reading.textContent = frame.readout.shown;
        el.dataset.shot = frame.shot;
        el.dataset.locked = String(frame.readout.locked.every(Boolean));
        transaction.observe(lease, frame);
        if (!css && monitor.sample(performance.now(), document.visibilityState !== 'hidden')) {
          useCSS('low-fps');
        }
      },
      finish() {},
    }, parseReading(DIVERGENCE[transaction.from])!, {
      onChange(snapshot) {
        if (!active) return;
        el.dataset.state = snapshot.waiting ? 'waiting' : snapshot.outcome === 'fail' ? 'failed' : snapshot.phase === 'ended' ? 'settled' : 'running';
        status.textContent = snapshot.waiting ? 'SYNC · 等待目标世界线确认'
          : snapshot.outcome === 'fail' ? '跃迁失败 · 已保留源世界线'
            : snapshot.phase === 'ended' ? '世界线已锁定' : '世界线跃迁中';
        if (snapshot.phase === 'ended' && !ending) {
          ending = true;
          // Leave a readable failure frame; success skip has only a short settle.
          timer = window.setTimeout(() => { if (active) done.current(); }, snapshot.outcome === 'fail' ? 900 : 120);
        }
      },
    });
    skip.current = () => controller.skip();
    fallback.current = () => useCSS('reduced-motion');
    const key = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.isComposing || event.keyCode === 229) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      skip.current();
    };
    window.addEventListener('keydown', key, true);
    el.dataset.renderer = 'css';
    // Let StrictMode's discarded setup clean up before allocating GPU resources.
    void Promise.resolve().then(() => {
      if (!active) return;
      const forceFallback = import.meta.env.DEV && new URLSearchParams(window.location.search).get('force-fallback') === '1';
      if (!reduced.current && !forceFallback) {
        try {
          attemptedGPU = true;
          gpu = new Renderer(canvas);
          css = false;
          el.dataset.renderer = 'webgl2';
          gpu.onLost = () => useCSS('context-lost');
          const resize = () => {
            if (!gpu || !active) return;
            const width = Math.min(1600, Math.round(el.clientWidth * Math.min(window.devicePixelRatio || 1, 1.5)));
            try { gpu.resize(width, width * 9 / 16); controller.draw(); }
            catch { useCSS('resize-error'); }
          };
          observer = new ResizeObserver(resize);
          observer.observe(el);
          resize();
        } catch {
          css = true;
          el.dataset.renderer = 'css';
          el.dataset.fallback = 'unavailable';
          releaseGPU();
        }
      } else {
        el.dataset.fallback = reduced.current ? 'reduced-motion' : 'forced-preview';
      }
      controller.start({
        origin: parseReading(DIVERGENCE[transaction.from])!,
        target: parseReading(DIVERGENCE[transaction.to])!,
        scenario: 'success',
        reduced: css,
        outcome: transaction.outcome,
      });
      // Static lock after the real outcome. The same controller draws its final frame.
      transaction.outcome.then(() => { if (active && css) controller.skip(); });
    });
    return () => {
      active = false;
      transaction.detach(lease);
      controller.dispose();
      releaseGPU();
      canvas.remove();
      window.clearTimeout(timer);
      window.removeEventListener('keydown', key, true);
      skip.current = () => {};
      fallback.current = () => {};
    };
  }, [transaction]);

  useEffect(() => { if (reduce) fallback.current(); }, [reduce]);

  return (
    <div ref={root} className="worldline-shift" data-renderer="css" role="status" aria-live="polite"
      aria-label={`世界线跃迁：${transaction.to === 'beta' ? 'β' : 'STEINS;GATE'}`} onClick={() => skip.current()}>
      <ShiftOverlay from={transaction.from} to={transaction.to} />
      <p className="shift-status" data-shift-status>SYNC · 等待目标世界线确认</p>
      <button type="button" className="shift-skip" onClick={event => { event.stopPropagation(); skip.current(); }}>点击或 Esc 跳过</button>
    </div>
  );
}
