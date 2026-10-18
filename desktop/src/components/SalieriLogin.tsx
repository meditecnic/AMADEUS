import { apiUrl, isPackagedTauri } from '../apiBase';
import React, { useState, useEffect, useLayoutEffect, useRef } from 'react';
import './SalieriLogin.css';
import { audioService } from '../services/AudioService';
import { audioPlayer } from '../hooks/useAudioPlayer';
import { useReducedMotion } from '../design-system/useReducedMotion';
import { Blueprint, type BlueprintHandle } from './login/Blueprint';
import { Wordmark, type WordmarkHandle } from './login/Wordmark';

/**
 * Beats of the entrance the app listens for. draft: the pen starts across the sheet and the workstation is
 * drafted in behind it. docked: the wordmark has landed on the header's. gone: the terminal has let go.
 */
export type EntranceCue = 'draft' | 'docked' | 'gone' | 'skip';

interface SalieriLoginProps {
  onLoginSuccess: () => void;
  /** Kept for API stability: the terminal is one style on both worldlines. */
  worldline?: 'steins_gate' | 'beta';
  onEntranceCue?: (cue: EntranceCue) => void;
}

/** Entrance timeline (ms after the server says yes). */
const T = { sweep: 330, sweepDur: 920, fly: 420, flyDur: 840, fade: 150 };
/** Header wordmark baseline inside its 1em line box (EB Garamond: ascent 1.007, descent .298, line-height 1). */
const HEADER_BASELINE = 0.8545;

const DAYS = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
const pad = (n: number) => String(n).padStart(2, '0');
function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return {
    date: `${now.getFullYear()}.${pad(now.getMonth() + 1)}.${pad(now.getDate())} ${DAYS[now.getDay()]}`,
    time: `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`,
  };
}

/** Decorative copy set in the sheet's lower right, like a title menu nobody can pick from. */
const VERSE = [
  'What is observed is never entirely lost.',
  'A memory is only a record that learned to wait.',
  'She does not remember the world \u2014 she remembers you.',
  'Every conversation ends; the record begins again.',
];

const CUT = 9;
/**
 * A field label's nameplate: a drafted plate with a cut corner, an engraved inner rule and crop marks, printed
 * slightly off register. Measured to the label so every line lands on a whole pixel. On focus the edge is drawn
 * first, then the plate is inked in (see .lp-* in the stylesheet).
 */
function Plate() {
  const ref = useRef<SVGSVGElement>(null);
  const [box, setBox] = useState<{ w: number; h: number } | null>(null);
  useLayoutEffect(() => {
    const host = ref.current?.parentElement;
    if (!host) return;
    const read = () => {
      const w = host.offsetWidth;
      const h = host.offsetHeight;
      if (w && h) setBox((b) => (b && b.w === w && b.h === h ? b : { w, h }));
    };
    read();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(read);
    ro.observe(host);
    return () => ro.disconnect();
  }, []);
  const w = box?.w ?? 0;
  const h = box?.h ?? 0;
  const shape = `M${CUT} 0H${w}V${h}H0V${CUT}Z`;
  const i = 2.5;
  const inner = `M${CUT + 1} ${i}H${w - i}V${h - i}H${i}V${CUT + 1}Z`;
  const o = 3;
  const l = 6;
  const marks = [
    `M${w + o} 0h${l}M${w} ${-o}v${-l}`,
    `M${w + o} ${h}h${l}M${w} ${h + o}v${l}`,
    `M${-o} ${h}h${-l}M0 ${h + o}v${l}`,
    `M${CUT / 2 - 1.8} ${CUT / 2 - 1.8}l-4 -4`,
  ].join('');
  return (
    <svg ref={ref} className="lp" width={w} height={h} aria-hidden="true" focusable="false">
      {box && (
        <>
          <path className="lp-reg r" d={shape} transform="translate(-1 0)" />
          <path className="lp-reg b" d={shape} transform="translate(1 .5)" />
          <path className="lp-fill" d={shape} filter="url(#sl-press)" />
          <path className="lp-inner" d={inner} />
          <path className="lp-ghost" d={shape} />
          <path className="lp-edge" d={shape} pathLength={1} />
          <path className="lp-marks" d={marks} />
        </>
      )}
    </svg>
  );
}

/** A ruler under each field: one division per character, the nib at the end of the measurement. */
function Ruler({ n }: { n: number }) {
  return (
    <span className="sl-ruler" aria-hidden="true" style={{ '--n': Math.min(n, 48) } as React.CSSProperties}>
      {n > 0 && <i key={n} className="fresh" />}
      <b className="nib" />
    </span>
  );
}

export const SalieriLogin: React.FC<SalieriLoginProps> = ({ onLoginSuccess, onEntranceCue }) => {
  const [userId, setUserId] = useState('');
  const [password, setPassword] = useState('');
  const [errorMsg, setErrorMsg] = useState('');
  // Which field the error belongs to; the message text is localized or a raw backend detail.
  const [errorField, setErrorField] = useState<'user' | 'password' | null>(null);
  const [errorSeq, setErrorSeq] = useState(0);
  const [isAuthenticating, setIsAuthenticating] = useState(false);
  const [backendStatus, setBackendStatus] = useState<'starting' | 'ready' | 'timeout'>(isPackagedTauri ? 'starting' : 'ready');
  const [focusField, setFocusField] = useState<'user' | 'password' | null>(null);
  const [capsLock, setCapsLock] = useState(false);
  const [exiting, setExiting] = useState(false);
  const [sweeping, setSweeping] = useState(false);
  const [flying, setFlying] = useState(false);
  const [glitch, setGlitch] = useState(0);
  const reduced = useReducedMotion();
  const [intro, setIntro] = useState(() => !reduced);
  const clock = useClock();

  const rootRef = useRef<HTMLDivElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const wordWrapRef = useRef<HTMLSpanElement>(null);
  const wordRef = useRef<WordmarkHandle>(null);
  const penRef = useRef<HTMLDivElement>(null);
  const plateRef = useRef<BlueprintHandle>(null);
  const userRowRef = useRef<HTMLDivElement>(null);
  const pwdRowRef = useRef<HTMLDivElement>(null);
  const pwdRef = useRef<HTMLInputElement>(null);
  const userRef = useRef<HTMLInputElement>(null);
  const refocusRef = useRef<'user' | 'password' | null>(null);
  const errTimeoutRef = useRef<number | null>(null);
  const succeededRef = useRef(false);
  const timersRef = useRef<number[]>([]);
  const animsRef = useRef<Animation[]>([]);
  const cueRef = useRef(onEntranceCue);
  cueRef.current = onEntranceCue;
  const goneRef = useRef(false);
  const cue = (c: EntranceCue) => {
    if (goneRef.current) return;
    if (c === 'gone' || c === 'skip') goneRef.current = true;
    cueRef.current?.(c);
  };
  const later = (fn: () => void, ms: number) => { timersRef.current.push(window.setTimeout(fn, ms)); };

  useEffect(() => () => {
    timersRef.current.forEach((id) => window.clearTimeout(id));
    animsRef.current.forEach((a) => a.cancel());
  }, []);

  // The sheet is drafted once per visit; afterwards every line simply stays put.
  useEffect(() => {
    if (!intro) return;
    const id = window.setTimeout(() => setIntro(false), 3400);
    return () => window.clearTimeout(id);
  }, [intro]);

  useEffect(() => {
    if (!isPackagedTauri) return;
    let stopped = false;
    let retryTimer: number | undefined;
    const controller = new AbortController();
    const deadline = window.setTimeout(() => {
      stopped = true;
      controller.abort();
      setBackendStatus('timeout');
    }, 30000);

    const checkHealth = async () => {
      try {
        const response = await fetch(apiUrl('/health'), { signal: controller.signal });
        if (stopped) return;
        if (response.ok) {
          stopped = true;
          window.clearTimeout(deadline);
          setBackendStatus('ready');
          return;
        }
      } catch {
        // 本地服务启动期间连接可能尚未建立，按间隔重试。
      }
      if (!stopped) retryTimer = window.setTimeout(() => { void checkHealth(); }, 1000);
    };

    void checkHealth();
    return () => {
      stopped = true;
      controller.abort();
      window.clearTimeout(deadline);
      window.clearTimeout(retryTimer);
    };
  }, []);

  // 统一错误设置与 3s 自动清除并复原函数
  const showAuthError = (msg: string, field: 'user' | 'password') => {
    if (errTimeoutRef.current) {
      window.clearTimeout(errTimeoutRef.current);
    }
    setErrorMsg(msg);
    setErrorField(field);
    setErrorSeq((n) => n + 1);
    refocusRef.current = field;
    plateRef.current?.jam();
    if (!reduced) setGlitch((n) => n + 1);
    const row = field === 'user' ? userRowRef.current : pwdRowRef.current;
    if (!reduced && typeof row?.animate === 'function') {
      // A damped refusal: the row is pushed back and settles, it does not wobble forever.
      row.animate(
        [{ transform: 'none' }, { transform: 'translateX(-9px)' }, { transform: 'translateX(7px)' }, { transform: 'translateX(-4px)' }, { transform: 'translateX(2px)' }, { transform: 'none' }],
        { duration: 420, easing: 'cubic-bezier(.36, .07, .19, .97)' },
      );
    }
    errTimeoutRef.current = window.setTimeout(() => {
      setErrorMsg('');
      setErrorField(null);
      errTimeoutRef.current = null;
    }, 3000);
  };

  // A refusal hands the keyboard back to the field at fault (it was disabled while the check ran), ready to retype.
  useEffect(() => {
    const field = refocusRef.current;
    if (!field || isAuthenticating) return;
    refocusRef.current = null;
    const input = field === 'user' ? userRef.current : pwdRef.current;
    if (!input || input.disabled) return;
    input.focus({ preventScroll: true });
    input.select();
  }, [errorSeq, isAuthenticating]);

  useEffect(() => {
    if (!glitch) return;
    const id = window.setTimeout(() => setGlitch(0), 380);
    return () => window.clearTimeout(id);
  }, [glitch]);

  // 卸载组件时清理定时器，防内存泄漏
  useEffect(() => {
    return () => {
      if (errTimeoutRef.current) {
        window.clearTimeout(errTimeoutRef.current);
      }
    };
  }, []);

  // Login-screen BGM. After a successful login the workstation owns the channel, so leaving must not silence it.
  useEffect(() => {
    audioService.playBGM('/audio/bgm/Messenger main theme.ogg');
    return () => {
      if (!succeededRef.current) audioService.stopBGM();
    };
  }, []);

  // USER ID 打字机自动录入（等输入栏画出来后开始）；每个字符让齿轮走一齿。写完后把光标交给密码栏。
  useEffect(() => {
    let index = 0;
    const targetText = 'Salieri';
    let current = '';
    let typeTimer: number | undefined;
    setUserId('');

    const startTimer = window.setTimeout(() => { typeTimer = window.setInterval(() => {
      if (index < targetText.length) {
        current += targetText.charAt(index);
        setUserId(current);
        plateRef.current?.tick();
        index++;
      } else {
        window.clearInterval(typeTimer);
        const active = document.activeElement;
        if (!active || active === document.body) pwdRef.current?.focus();
      }
    }, 50); }, reduced ? 0 : 1050);

    return () => { window.clearTimeout(startTimer); window.clearInterval(typeTimer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A slight parallax between the paper and the drawings on it.
  useEffect(() => {
    const root = rootRef.current;
    if (!root || reduced) return;
    let raf = 0;
    const move = (e: PointerEvent) => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        root.style.setProperty('--px', ((e.clientX / window.innerWidth) * 2 - 1).toFixed(3));
        root.style.setProperty('--py', ((e.clientY / window.innerHeight) * 2 - 1).toFixed(3));
      });
    };
    window.addEventListener('pointermove', move);
    return () => { window.removeEventListener('pointermove', move); cancelAnimationFrame(raf); };
  }, [reduced]);

  // Entrance: any deliberate key or click skips straight into the workstation.
  useEffect(() => {
    if (!exiting || reduced) return;
    const skip = (e: KeyboardEvent) => {
      if (e.repeat || e.isComposing) return;
      animsRef.current.forEach((a) => a.cancel());
      cue('skip');
    };
    window.addEventListener('keydown', skip, true);
    return () => window.removeEventListener('keydown', skip, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exiting, reduced]);

  // Reduced motion: a short opacity fade only, with a fallback in case transitionend never arrives.
  useEffect(() => {
    if (!exiting || !reduced) return;
    const id = window.setTimeout(() => cue('gone'), 400);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exiting, reduced]);

  /**
   * The same pen that drafted the wordmark crosses the whole sheet; behind it the workstation is drafted in,
   * and the wordmark flies up to become the header's own.
   */
  const runEntrance = () => {
    const pen = penRef.current;
    const backdrop = backdropRef.current;
    const canAnimate = typeof pen?.animate === 'function' && typeof backdrop?.animate === 'function';
    later(() => {
      plateRef.current?.tick(4);
      if (!canAnimate) { cue('draft'); cue('docked'); cue('gone'); return; }
      setSweeping(true);
      const timing: KeyframeAnimationOptions = { duration: T.sweepDur, easing: 'cubic-bezier(.62, 0, .3, 1)', fill: 'forwards' };
      animsRef.current.push(
        pen!.animate([{ transform: 'translateX(0)' }, { transform: 'translateX(100vw)' }], timing),
        backdrop!.animate([{ clipPath: 'inset(0 0 0 0)' }, { clipPath: 'inset(0 0 0 100%)' }], timing),
      );
      cue('draft');
    }, T.sweep);

    later(() => {
      const wrap = wordWrapRef.current;
      const from = wordRef.current?.measure();
      const head = document.querySelector<HTMLElement>('.ws2-wordmark');
      const firstCh = head?.querySelector<HTMLElement>('.ch') ?? head;
      const land = () => {
        cue('docked');
        if (wrap && typeof wrap.animate === 'function') {
          const fade = wrap.animate([{ opacity: 1 }, { opacity: 0 }], { duration: T.fade, fill: 'forwards' });
          animsRef.current.push(fade);
          fade.onfinish = () => cue('gone');
        } else cue('gone');
      };
      if (!wrap || !from || !firstCh || typeof wrap.animate !== 'function') {
        later(land, Math.max(0, T.sweep + T.sweepDur - T.fly));
        return;
      }
      const box = wrap.getBoundingClientRect();
      const ch = firstCh.getBoundingClientRect();
      const fs = parseFloat(getComputedStyle(head!).fontSize) || 21;
      const k = fs / 1000 / from.unit;
      const ox = from.x - box.left;
      const oy = from.y - box.top;
      const dx = ch.left - from.x;
      const dy = ch.top + HEADER_BASELINE * fs - from.y;
      setFlying(true);
      wrap.style.transformOrigin = `${ox}px ${oy}px`;
      const fly = wrap.animate(
        [
          { transform: 'none' },
          { transform: `translate(${dx * 0.12}px, ${dy * 0.04 + 6}px) scale(${1 - (1 - k) * 0.06})`, offset: 0.22 },
          { transform: `translate(${dx}px, ${dy}px) scale(${k})` },
        ],
        { duration: T.flyDur, easing: 'cubic-bezier(.66, 0, .18, 1)', fill: 'forwards' },
      );
      animsRef.current.push(fly);
      fly.onfinish = land;
    }, T.fly);
  };

  const handleInitAudio = () => {
    audioPlayer.init();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (backendStatus !== 'ready' || exiting) return;
    handleInitAudio();
    if (!userId) {
      showAuthError('请输入 USER ID', 'user');
      return;
    }
    if (userId !== 'Salieri') {
      showAuthError('无效的 USER ID', 'user');
      return;
    }
    if (!password) {
      showAuthError('请输入访问密码', 'password');
      return;
    }

    setIsAuthenticating(true);
    // Once the user has asked, the title is finished: the pen is needed for the check, not the drawing.
    setIntro(false);
    if (errTimeoutRef.current) {
      window.clearTimeout(errTimeoutRef.current);
      errTimeoutRef.current = null;
    }
    setErrorMsg('');
    setErrorField(null);

    let accepted = false;
    try {
      const response = await fetch(apiUrl('/api/auth/salieri'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ password }),
      });

      if (response.ok) {
        accepted = true;
        audioService.playSFX('/audio/sfx/连接成功，进入页面2.ogg');
        audioService.stopBGM();
        succeededRef.current = true;
        (document.activeElement as HTMLElement | null)?.blur?.();
        setIntro(false);
        setExiting(true);
        onLoginSuccess();
        if (!reduced) runEntrance();
      } else if (response.status === 401 || response.status === 403) {
        showAuthError('密码错误', 'password');
      } else {
        const errorData = await response.json().catch(() => ({}));
        showAuthError(errorData.detail || '访问被拒绝', 'password');
      }
    } catch (error) {
      showAuthError('系统连接已断开', 'password');
    } finally {
      if (!accepted) setIsAuthenticating(false);
    }
  };

  const busy = isAuthenticating && !exiting;
  const rootClass = [
    'sl-root',
    exiting && 'exiting',
    sweeping && 'sweeping',
    reduced && 'reduced',
    intro && 'intro',
  ].filter(Boolean).join(' ');

  return (
    <div
      ref={rootRef}
      className={rootClass}
      onPointerDown={() => {
        if (!exiting || reduced) return;
        animsRef.current.forEach((a) => a.cancel());
        cue('skip');
      }}
      onTransitionEnd={(e) => {
        if (exiting && reduced && e.target === e.currentTarget && e.propertyName === 'opacity') cue('gone');
      }}
    >
      <div ref={backdropRef} className="sl-backdrop">
        <div className="sl-grid" aria-hidden="true" />
        <div className="sl-plate"><Blueprint ref={plateRef} reduced={reduced} busy={busy} /></div>
        <div className="sl-frame" aria-hidden="true">
          <div className="zones top">{[1, 2, 3, 4, 5, 6, 7, 8].map((n) => <span key={n}>{n}</span>)}</div>
          <div className="zones bottom">{[1, 2, 3, 4, 5, 6, 7, 8].map((n) => <span key={n}>{n}</span>)}</div>
          <div className="zones left">{['A', 'B', 'C', 'D', 'E', 'F'].map((n) => <span key={n}>{n}</span>)}</div>
          <div className="zones right">{['A', 'B', 'C', 'D', 'E', 'F'].map((n) => <span key={n}>{n}</span>)}</div>
        </div>
        <div className="sl-tblock" aria-hidden="true">
          <span className="k">DATE</span><span className="v">{clock.date}</span>
          <span className="k">TIME</span><span className="v">{clock.time}</span>
        </div>
        <div className="sl-verse" aria-hidden="true">
          {VERSE.map((line, i) => (
            <p key={i} style={{ '--n': line.length, '--i': i } as React.CSSProperties}>{line}</p>
          ))}
        </div>
        <p className="sl-note" aria-hidden="true">LOCAL LINK · 记忆仅保存在这台设备上</p>
        <div className="sl-vignette" aria-hidden="true" />
      </div>

      <div className="sl-center">
        <h1 className="sl-title">
          <span ref={wordWrapRef} className="sl-word">
            <Wordmark
              ref={wordRef}
              className={[intro && 'is-intro', busy && 'is-busy', glitch && 'is-glitch', exiting && 'is-ink', flying && 'is-flying'].filter(Boolean).join(' ')}
            />
          </span>
        </h1>
        <div className="sl-dim" aria-hidden="true"><i /><i /></div>

        <form onSubmit={handleSubmit} className="sl-form salieri-login-form" noValidate>
          <div ref={userRowRef} className={`sl-row user${errorField === 'user' ? ' is-error' : ''}`}>
            <label className={`sl-label${focusField === 'user' || errorField === 'user' ? ' is-on' : ''}`} htmlFor="salieri-user"><Plate /><span>USER ID</span></label>
            <div className="sl-slot">
              <input
                ref={userRef}
                id="salieri-user"
                type="text"
                className="sl-input user-id-input"
                value={userId}
                onChange={(e) => { setUserId(e.target.value); plateRef.current?.tick(); }}
                onFocus={() => setFocusField('user')}
                onBlur={() => setFocusField((f) => (f === 'user' ? null : f))}
                disabled={isAuthenticating}
                spellCheck={false}
                autoComplete="off"
                aria-invalid={errorField === 'user' || undefined}
              />
              <i className="sl-ul" aria-hidden="true" />
              <Ruler n={userId.length} />
            </div>
          </div>

          <div ref={pwdRowRef} className={`sl-row pwd${errorField === 'password' ? ' is-error' : ''}`}>
            <label className={`sl-label${focusField === 'password' || errorField === 'password' ? ' is-on' : ''}`} htmlFor="salieri-pwd"><Plate /><span>PASSWORD</span></label>
            <div className="sl-slot">
              <input
                ref={pwdRef}
                id="salieri-pwd"
                type="password"
                className="sl-input password-input"
                value={password}
                onChange={(e) => { setPassword(e.target.value); plateRef.current?.tick(); }}
                onFocus={() => setFocusField('password')}
                onBlur={() => { setFocusField((f) => (f === 'password' ? null : f)); setCapsLock(false); }}
                onKeyDown={(e) => setCapsLock(e.getModifierState?.('CapsLock') ?? false)}
                onKeyUp={(e) => setCapsLock(e.getModifierState?.('CapsLock') ?? false)}
                disabled={isAuthenticating}
                spellCheck={false}
                autoComplete="off"
                aria-invalid={errorField === 'password' || undefined}
              />
              {capsLock && <span className="sl-caps" aria-hidden="true">CAPS</span>}
              <i className="sl-ul" aria-hidden="true" />
              <Ruler n={[...password].length} />
            </div>
            <button
              type="submit"
              className={`decrypt-btn sl-go${busy ? ' is-busy' : ''}${exiting ? ' is-ok' : ''}`}
              disabled={isAuthenticating || backendStatus !== 'ready'}
              title="CONNECT"
              aria-label="Decrypt and Connect"
            >
              <svg viewBox="0 0 64 64" aria-hidden="true">
                <circle cx="32" cy="32" r="30.5" className="halo" />
                <g className="ticks">
                  {Array.from({ length: 24 }, (_, i) => <path key={i} d={`M32 4.2v${i % 6 === 0 ? 4.2 : 2.4}`} transform={`rotate(${i * 15} 32 32)`} />)}
                </g>
                <circle cx="32" cy="32" r="23" className="ring" pathLength={1} />
                <circle cx="32" cy="32" r="23" className="disc" />
                <path d="M23.5 32h16M34 26.5l5.5 5.5-5.5 5.5" className="arrow" />
                <path d="M24 32.5l5.5 5.5 11-12" className="check" pathLength={1} />
              </svg>
            </button>
          </div>

          <div className="sl-msg-line">
            {backendStatus !== 'ready' && (
              <p className="sl-msg backend-status-banner" role="status">
                <i aria-hidden="true" />{backendStatus === 'starting' ? '正在启动本地服务…' : '本地服务没有启动，请重新打开 Amadeus'}
              </p>
            )}
            {errorMsg && (
              <p key={errorSeq} className="sl-msg is-error" role="alert">
                <i aria-hidden="true" />{errorMsg}
              </p>
            )}
          </div>
        </form>
      </div>

      {/* Letterpress: a little ink spread at the edges and paper grain through the ink. */}
      <svg className="sl-defs" width="0" height="0" aria-hidden="true" focusable="false">
        <filter id="sl-press" x="-4%" y="-20%" width="108%" height="140%" colorInterpolationFilters="sRGB">
          <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves={2} seed={7} result="n" />
          <feDisplacementMap in="SourceGraphic" in2="n" scale={1.3} xChannelSelector="R" yChannelSelector="G" result="d" />
          <feGaussianBlur in="d" stdDeviation={0.3} result="s" />
          <feColorMatrix in="n" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0.3 0 0 0 0.74" result="g" />
          <feComposite in="s" in2="g" operator="in" />
        </filter>
      </svg>

      <div ref={penRef} className="sl-pen" aria-hidden="true">
        <i className="s1" /><i className="s2" /><i className="s3" /><i className="s4" /><i className="s5" />
      </div>
    </div>
  );
};
