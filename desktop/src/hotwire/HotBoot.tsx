import { useEffect, useRef, useState } from 'react';
import { apiFetch } from '../apiBase';
import { loadAssetPack } from '../assetPack';
import { useReducedMotion } from '../design-system/useReducedMotion';
import { DIVERGENCE, Nixie, useTunedValue } from '../workstation/Nixie';
import { COPY } from './copy.zh-CN';
import { GiantWord } from './GiantWord';
import { toneOf } from './tone';

/**
 * Self-made boot (design/references.md §2.6): a hot hairline opens the screen, drafting circles are drawn,
 * the giant AMADEUS lands, the start-up log prints only checks that really ran, and the meter ignites.
 * It hands over to login once the local service answers; until then the last log line says it is waiting.
 */
interface Props {
  worldline: 'steins_gate' | 'beta';
  onDone: () => void;
}

type Line = { key: string; label: string; value: string; state: 'ok' | 'wait' | 'off' };
const HEALTH_DEADLINE_MS = 30000;

export function HotBoot({ worldline, onDone }: Props) {
  const reduced = useReducedMotion();
  const tone = toneOf(worldline);
  const [beat, setBeat] = useState(reduced ? 9 : 0);
  const [health, setHealth] = useState<'wait' | 'ok' | 'timeout'>('wait');
  const [pack, setPack] = useState<boolean | null>(null);
  const [voice, setVoice] = useState<boolean | null>(null);
  const [leaving, setLeaving] = useState(false);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;
  const reading = DIVERGENCE[worldline];
  const tubes = useTunedValue(beat >= 5 ? reading : '0.000000', { duration: 1200 });

  // Timeline beats: 1 line, 2 drafts, 3 word, 4 log, 5 meter, 6 ready.
  useEffect(() => {
    if (reduced) return;
    const at = [200, 520, 900, 1250, 1650, 3000];
    const ids = at.map((ms, i) => window.setTimeout(() => setBeat((b) => Math.max(b, i + 1)), ms));
    return () => ids.forEach((id) => window.clearTimeout(id));
  }, [reduced]);

  useEffect(() => {
    let stopped = false;
    let retry: number | undefined;
    const controller = new AbortController();
    const deadline = window.setTimeout(() => { stopped = true; controller.abort(); setHealth('timeout'); }, HEALTH_DEADLINE_MS);
    const check = async () => {
      try {
        const res = await apiFetch('/health', { signal: controller.signal });
        if (!stopped && res.ok) { stopped = true; window.clearTimeout(deadline); setHealth('ok'); return; }
      } catch { /* the service may still be starting */ }
      if (!stopped) retry = window.setTimeout(() => { void check(); }, 800);
    };
    void check();
    void loadAssetPack().then(setPack);
    void apiFetch('/health/dependencies')
      .then((r) => (r.ok ? r.json() : null))
      .then((body: { dependencies?: { tts?: { ok?: boolean } } } | null) => {
        const ok = body?.dependencies?.tts?.ok;
        setVoice(typeof ok === 'boolean' ? ok : null);
      })
      .catch(() => setVoice(null));
    return () => { stopped = true; controller.abort(); window.clearTimeout(deadline); window.clearTimeout(retry); };
  }, []);

  const ready = health === 'ok' && pack !== null && beat >= 6;
  useEffect(() => {
    if (ready) setLeaving(true);
  }, [ready]);
  useEffect(() => {
    if (!leaving) return;
    const id = window.setTimeout(() => doneRef.current(), reduced ? 0 : 260);
    return () => window.clearTimeout(id);
  }, [leaving, reduced]);

  // Any deliberate key or click skips the show once the service is up.
  useEffect(() => {
    if (health !== 'ok') return;
    const skip = (e: Event) => {
      if (e instanceof KeyboardEvent && (e.repeat || e.isComposing)) return;
      setBeat(9);
    };
    window.addEventListener('keydown', skip);
    window.addEventListener('pointerdown', skip);
    return () => { window.removeEventListener('keydown', skip); window.removeEventListener('pointerdown', skip); };
  }, [health]);

  const lines: Line[] = [
    { key: 'node', label: COPY.boot.node, value: health === 'ok' ? COPY.boot.nodeOnline : health === 'timeout' ? '没有响应' : COPY.boot.nodeWaiting, state: health === 'ok' ? 'ok' : health === 'timeout' ? 'off' : 'wait' },
    { key: 'pack', label: COPY.boot.pack, value: pack === null ? '…' : pack ? COPY.boot.packOn : COPY.boot.packOff, state: pack ? 'ok' : pack === null ? 'wait' : 'off' },
    { key: 'voice', label: COPY.boot.voice, value: voice === null ? '未检测' : voice ? COPY.boot.voiceOn : COPY.boot.voiceOff, state: voice ? 'ok' : 'off' },
    { key: 'wl', label: COPY.boot.worldline, value: `${reading} · ${worldline === 'beta' ? 'β' : 'STEINS;GATE'}`, state: 'ok' },
  ];

  return (
    <div className={`hw-boot${leaving ? ' is-leaving' : ''}`} data-wl={tone} data-beat={beat} role="status" aria-label="AMADEUS 正在启动">
      <div className="hw-boot-grid" aria-hidden="true" />
      <svg className="hw-boot-draft" viewBox="0 0 1600 900" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        <path className="hw-draw base" pathLength={1} d="M800 450 H0" />
        <path className="hw-draw base" pathLength={1} d="M800 450 H1600" />
        {Array.from({ length: 41 }, (_, i) => (
          <path key={i} className="hw-draw tick" pathLength={1} d={`M${i * 40} 450 v${i % 5 === 0 ? -14 : -6}`} />
        ))}
        <circle className="hw-draw arc" pathLength={1} cx="800" cy="450" r="330" />
        <circle className="hw-draw arc thin" pathLength={1} cx="800" cy="450" r="250" />
        <circle className="hw-draw arc thin" pathLength={1} cx="1290" cy="300" r="120" />
        <path className="hw-draw arc" pathLength={1} d="M220 760 A 520 520 0 0 1 560 130" />
        <path className="hw-draw arc thin" pathLength={1} d="M800 60 V840 M420 450 H1180" />
      </svg>
      <GiantWord text="AMADEUS" className="hw-boot-word" />
      <div className="hw-boot-log">
        <b>{COPY.boot.title}</b>
        {lines.map((l, i) => (
          <p key={l.key} className={`is-${l.state}`} style={{ animationDelay: `${i * 140}ms` }}>
            <span className="k">{l.label}</span>
            <span className="dots" aria-hidden="true" />
            <span className="v">{l.value}</span>
          </p>
        ))}
        {health === 'timeout' ? (
          <button type="button" className="hw-boot-retry" onClick={() => window.location.reload()}>重新启动界面</button>
        ) : null}
      </div>
      <div className="hw-boot-meter">
        <Nixie value={tubes.text} state={tubes} label={reading} size="md" unlit={beat < 5} blueprint={false} />
      </div>
      {health === 'ok' && beat < 6 ? <small className="hw-boot-skip">{COPY.boot.skip}</small> : null}
    </div>
  );
}
