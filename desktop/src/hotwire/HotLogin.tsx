import { useEffect, useRef, useState, type FormEvent } from 'react';
import { apiFetch } from '../apiBase';
import { audioService } from '../services/AudioService';
import { audioPlayer } from '../hooks/useAudioPlayer';
import { useReducedMotion } from '../design-system/useReducedMotion';
import { warmWorkstationFonts } from '../workstation/fontWarmup';
import { loadStoredIdentityMode, loadStoredSelfName } from '../components/SettingsModal';
import { DIVERGENCE } from '../workstation/Nixie';
import { BinaryField } from './BinaryField';
import { COPY } from './copy.zh-CN';
import { GiantWord } from './GiantWord';
import { toneOf } from './tone';

/**
 * Login as a ritual, not a lock (product plan §1.1). Structure after the Amadeus window (design/references.md
 * R12): data wall, a black-and-white mass at the left, the wordmark, two rows and a round key.
 * USER ID shows who you are; the passcode is optional and only "keeps others from opening it casually".
 * Backend: GET /api/auth/state, POST /api/auth/login {passcode|null} → {status, easter_egg} ([210]).
 */
interface Props {
  worldline: 'steins_gate' | 'beta';
  onLoginSuccess: (opts: { easterEgg: boolean }) => void;
}

const DAYS = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
const pad = (n: number) => String(n).padStart(2, '0');

function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return { date: `${pad(now.getMonth() + 1)}/${pad(now.getDate())}`, day: DAYS[now.getDay()], time: `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}` };
}

export function HotLogin({ worldline, onLoginSuccess }: Props) {
  const reduced = useReducedMotion();
  const tone = toneOf(worldline);
  const clock = useClock();
  const [passcodeSet, setPasscodeSet] = useState<boolean | null>(null);
  const [stateNote, setStateNote] = useState<string | null>(null);
  const [passcode, setPasscode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errorSeq, setErrorSeq] = useState(0);
  const [burning, setBurning] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const rowRef = useRef<HTMLDivElement>(null);
  const succeeded = useRef(false);
  const identity = loadStoredIdentityMode();
  const selfName = loadStoredSelfName();
  const who = identity === 'self' ? (selfName || 'YOU') : COPY.login.okabe;

  useEffect(() => warmWorkstationFonts(), []);

  useEffect(() => {
    let cancelled = false;
    void apiFetch('/api/auth/state')
      .then(async (res) => {
        if (!res.ok) throw new Error(String(res.status));
        const body = (await res.json()) as { passcode_set?: boolean; passcode_error?: string };
        if (cancelled) return;
        setPasscodeSet(Boolean(body.passcode_set));
        if (body.passcode_error) setStateNote(COPY.login.stateFailed);
      })
      .catch(() => { if (!cancelled) { setPasscodeSet(false); setStateNote(COPY.login.stateFailed); } });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => { inputRef.current?.focus({ preventScroll: true }); }, [passcodeSet]);

  // Login BGM comes from the asset pack when installed; the workstation owns the channel after login.
  useEffect(() => {
    audioService.playBGM('/audio/bgm/Messenger main theme.ogg');
    return () => { if (!succeeded.current) audioService.stopBGM(); };
  }, []);

  const refuse = (msg: string) => {
    setError(msg);
    setErrorSeq((n) => n + 1);
    const row = rowRef.current;
    if (!reduced && row && typeof row.animate === 'function') {
      row.animate(
        [{ transform: 'none' }, { transform: 'translateX(-9px) skewX(-6deg)' }, { transform: 'translateX(6px)' }, { transform: 'translateX(-3px)' }, { transform: 'none' }],
        { duration: 380, easing: 'cubic-bezier(.36,.07,.19,.97)' },
      );
    }
  };

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const value = passcode.length ? passcode : null;
      const res = await apiFetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ passcode: value }),
      });
      if (res.ok) {
        const body = (await res.json().catch(() => ({}))) as { easter_egg?: boolean };
        succeeded.current = true;
        audioService.stopBGM();
        setBurning(true);
        window.setTimeout(() => onLoginSuccess({ easterEgg: Boolean(body.easter_egg) }), reduced ? 0 : 240);
        return;
      }
      if (res.status === 429) refuse(COPY.login.slow);
      else refuse(COPY.login.wrong);
      inputRef.current?.select();
    } catch {
      refuse(COPY.login.unreachable);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={`hwl${burning ? ' is-burning' : ''}`}
      data-wl={tone}
      onKeyDownCapture={() => audioPlayer.init()}
      onPointerDownCapture={() => audioPlayer.init()}
    >
      <div className="hwl-grid" aria-hidden="true" />
      <BinaryField tone={tone} reduced={reduced} density={9} className="hwl-bin" />
      <svg className="hwl-draft" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        <circle cx="1060" cy="420" r="300" />
        <circle cx="1060" cy="420" r="228" className="thin" />
        <path d="M1060 60 V800 M700 420 H1420" className="thin" />
        <path d="M640 840 A 600 600 0 0 1 1180 110" />
        {Array.from({ length: 36 }, (_, i) => {
          const a = (i / 36) * Math.PI * 2;
          const r0 = 300;
          const r1 = i % 3 === 0 ? 318 : 309;
          return <path key={i} d={`M${1060 + Math.cos(a) * r0} ${420 + Math.sin(a) * r0} L${1060 + Math.cos(a) * r1} ${420 + Math.sin(a) * r1}`} />;
        })}
      </svg>
      <div className="hwl-mass" aria-hidden="true">
        <span>AMADEUS</span>
      </div>
      <GiantWord text={COPY.login.ghost} lang="ja" outline className="hwl-ghost" />

      <header className="hwl-top">
        <span className="hwl-date"><b>{clock.date}</b><i>{clock.day}</i></span>
        <span className="hwl-time">{clock.time}</span>
        <span className="hwl-wl">{worldline === 'beta' ? 'β' : 'STEINS;GATE'} · {DIVERGENCE[worldline]}</span>
      </header>

      <form className="hwl-panel" onSubmit={(e) => { void submit(e); }} aria-label="AMADEUS 登录">
        <div className="hwl-mark">
          <h1 className="hwl-word">AMADEUS</h1>
          <small>{COPY.login.system}</small>
        </div>
        <div className="hwl-row is-id">
          <span className="hwl-label" id="hwl-id-label">{COPY.login.userId}</span>
          <output className="hwl-field" aria-labelledby="hwl-id-label">
            <b>{who}</b>
            <small>{identity === 'self' ? COPY.login.selfNote : COPY.login.okabeNote} · {COPY.login.identityHint}</small>
          </output>
        </div>
        <div className="hwl-row is-pass" ref={rowRef}>
          <label className="hwl-label" htmlFor="hwl-pass">{COPY.login.passcode}</label>
          <div className="hwl-field">
            <input
              id="hwl-pass"
              ref={inputRef}
              type="password"
              value={passcode}
              autoComplete="current-password"
              spellCheck={false}
              placeholder={passcodeSet ? COPY.login.passcodeOn : COPY.login.passcodeOff}
              onChange={(e) => { setPasscode(e.target.value); if (error) setError(null); }}
              disabled={busy || burning}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? 'hwl-error' : undefined}
            />
          </div>
          <button type="submit" className="hwl-go" aria-label={COPY.login.go} disabled={busy || burning || passcodeSet === null}>
            <svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="17" /><path d="M12 20h15M21 13.5l6.5 6.5-6.5 6.5" /></svg>
          </button>
        </div>
        <p className="hwl-error" id="hwl-error" role="alert" key={errorSeq}>{error ?? ''}</p>
        {stateNote ? <p className="hwl-note">{stateNote}</p> : null}
      </form>
      <footer className="hwl-foot" aria-hidden="true"><span>{COPY.login.press}</span></footer>
    </div>
  );
}
