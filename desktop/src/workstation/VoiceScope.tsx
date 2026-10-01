import { useEffect, useRef, useState } from 'react';

const POINTS = 56;

/** Her voice, drawn and controlled in one place: an oscilloscope trace under the name plate.
 *  The trace is her live output level; the bright part of the baseline is the channel volume.
 *  Wheel / arrows set the volume, click / Enter / M mutes (level kept for restore), ■ stops the sentence. */
export function VoiceScope({ level, volume, muted, speaking, onVolume, onToggleMute, onStop }: {
  level: number;
  volume: number;
  muted: boolean;
  speaking: boolean;
  onVolume: (v: number) => void;
  onToggleMute: () => void;
  onStop: () => void;
}) {
  const levelRef = useRef(level);
  levelRef.current = level;
  const [trace, setTrace] = useState<number[]>(() => Array(POINTS).fill(0));
  const pct = Math.round(volume * 100);

  useEffect(() => {
    if (!speaking) { setTrace(Array(POINTS).fill(0)); return; }
    let raf = 0;
    let last = 0;
    let phase = 0;
    const tick = (t: number) => {
      raf = requestAnimationFrame(tick);
      if (t - last < 38) return;
      last = t;
      phase += 1;
      // RMS is small (≈0–0.3); lift it, then give it a carrier so it reads as a waveform rather than a bar.
      const amp = Math.min(1, levelRef.current * 4.2);
      const v = amp * Math.sin(phase * 1.9) * (0.7 + 0.3 * Math.random());
      setTrace((prev) => [...prev.slice(1), v]);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [speaking]);

  const w = 132;
  const h = 26;
  const mid = h / 2;
  const gain = muted ? 0 : 0.35 + volume * 0.65;
  const d = trace.map((v, i) => `${i === 0 ? 'M' : 'L'}${((i / (POINTS - 1)) * w).toFixed(1)} ${(mid - v * gain * (mid - 2)).toFixed(1)}`).join('');
  const step = (delta: number) => onVolume(Math.max(0, Math.min(1, Math.round((volume + delta) * 100) / 100)));

  return (
    <div className={`ws2-scope${muted ? ' muted' : ''}${speaking ? ' live' : ''}`}>
      <div
        className="ws2-scope-body"
        role="slider"
        tabIndex={0}
        aria-label="角色语音音量"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-valuetext={muted ? `静音（${pct}%）` : `${pct}%`}
        title="滚轮或方向键调节音量 · 点击静音"
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
      {speaking && (
        <button type="button" className="ws2-scope-stop" aria-label="停止本句" title="停止本句" onClick={onStop}>
          <i aria-hidden="true" />
        </button>
      )}
    </div>
  );
}
