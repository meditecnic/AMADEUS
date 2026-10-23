import { useEffect, useState } from 'react';
import { useReducedMotion } from '../design-system/useReducedMotion';
import { Tip } from './Tip';
import { voiceEnvelope, voiceHeard } from './voiceEnvelope';

const POINTS = 56;
const ZERO = Array(POINTS).fill(0) as number[];

/** Her voice, drawn and controlled in one place: an oscilloscope trace under the name plate.
 *  The trace reads the shared voice envelope (the lips read the same one); the bright part of the baseline is the
 *  channel volume. While she is quiet, typing leaves a faint blip — she hears the keys.
 *  Wheel / arrows set the volume, click / Enter / M mutes (level kept for restore), ■ stops the sentence. */
export function VoiceScope({ volume, muted, speaking, onVolume, onToggleMute, onStop }: {
  volume: number;
  muted: boolean;
  speaking: boolean;
  onVolume: (v: number) => void;
  onToggleMute: () => void;
  onStop: () => void;
}) {
  const reducedMotion = useReducedMotion();
  const [trace, setTrace] = useState<number[]>(ZERO);

  useEffect(() => {
    setTrace(ZERO);
    let raf = 0;
    let running = false;
    let last = 0;
    let phase = 0;
    let quiet = 0;
    const tick = (t: number) => {
      const heard = reducedMotion ? 0 : voiceHeard.get();
      if (!speaking && heard < 0.002 && quiet >= POINTS) { running = false; return; }
      raf = requestAnimationFrame(tick);
      if (t - last < 38) return;
      last = t;
      phase += 1;
      // RMS is small (≈0–0.3); lift it, then give it a carrier so it reads as a waveform rather than a bar.
      const amp = speaking ? Math.min(1, voiceEnvelope.get() * 4.2) : 0;
      const v = amp * Math.sin(phase * 1.9) * (0.7 + 0.3 * Math.random()) + heard * 0.3 * Math.sin(phase * 2.7);
      quiet = Math.abs(v) < 0.004 ? quiet + 1 : 0;
      setTrace((prev) => [...prev.slice(1), v]);
    };
    const start = () => {
      if (running) return;
      running = true;
      quiet = 0;
      raf = requestAnimationFrame(tick);
    };
    if (speaking) start();
    const stopListening = voiceHeard.on('change', (v) => { if (v > 0.002 && !reducedMotion) start(); });
    return () => { cancelAnimationFrame(raf); stopListening(); };
  }, [speaking, reducedMotion]);

  const pct = Math.round(volume * 100);

  const w = 132;
  const h = 26;
  const mid = h / 2;
  const gain = muted ? 0 : 0.35 + volume * 0.65;
  const d = trace.map((v, i) => `${i === 0 ? 'M' : 'L'}${((i / (POINTS - 1)) * w).toFixed(1)} ${(mid - v * gain * (mid - 2)).toFixed(1)}`).join('');
  const step = (delta: number) => onVolume(Math.max(0, Math.min(1, Math.round((volume + delta) * 100) / 100)));

  return (
    <div className={`ws2-scope${muted ? ' muted' : ''}${speaking ? ' live' : ''}`}>
      <Tip label="滚轮或方向键调节音量 · 点击静音" side="top" align="end">
      <div
        className="ws2-scope-body"
        role="slider"
        tabIndex={0}
        aria-label="角色语音音量"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-valuetext={muted ? `静音（${pct}%）` : `${pct}%`}
        onClick={onToggleMute}
        onWheel={(e) => { e.preventDefault(); step(e.deltaY < 0 ? 0.05 : -0.05); }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowUp' || e.key === 'ArrowRight') { e.preventDefault(); step(0.05); }
          else if (e.key === 'ArrowDown' || e.key === 'ArrowLeft') { e.preventDefault(); step(-0.05); }
          else if (e.key === 'Home') { e.preventDefault(); onVolume(0); }
          else if (e.key === 'End') { e.preventDefault(); onVolume(1); }
          else if (e.key === 'Enter' || e.key === ' ' || e.key.toLowerCase() === 'm') { e.preventDefault(); onToggleMute(); }
        }}
      >
        <span className="ws2-scope-tag" aria-hidden="true">VOICE</span>
        <svg className="ws2-scope-svg" viewBox={`0 0 ${w} ${h}`} width={w} height={h} aria-hidden="true">
          <line className="base" x1="0" y1={mid} x2={w} y2={mid} />
          <line className="gain" x1="0" y1={mid} x2={muted ? 0 : w * volume} y2={mid} />
          {Array.from({ length: 11 }, (_, i) => (
            <line key={i} className={`tick${i % 5 === 0 ? ' major' : ''}`} x1={(i / 10) * w} x2={(i / 10) * w} y1={mid + 5} y2={mid + (i % 5 === 0 ? 9 : 7)} />
          ))}
          <path className="trace" d={d} />
        </svg>
        <span className="ws2-scope-val" aria-hidden="true">{muted ? 'MUTE' : String(pct).padStart(3, '0')}</span>
      </div>
      </Tip>
      {speaking && (
        <Tip label="停止本句" side="top">
          <button type="button" className="ws2-scope-stop" aria-label="停止本句" onClick={onStop}>
            <i aria-hidden="true" />
          </button>
        </Tip>
      )}
    </div>
  );
}
