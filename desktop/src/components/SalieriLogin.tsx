import { apiFetch, isPackagedTauri } from '../apiBase';
import React, { useState, useEffect, useRef } from 'react';
import './SalieriLogin.css';
import { audioService } from '../services/AudioService';
import { audioPlayer } from '../hooks/useAudioPlayer';
import { useReducedMotion } from '../design-system/useReducedMotion';
import { warmWorkstationFonts } from '../workstation/fontWarmup';
import { prewarmWorkstation } from '../workstation/prewarm';
import { Blueprint, type BlueprintHandle } from './login/Blueprint';
import { Wordmark, type WordmarkHandle } from './login/Wordmark';
import { DIVERGENCE } from '../workstation/Nixie';
import { KeyGlyph, Plate, PressFilter, Ruler } from './drafting/parts';

/**
 * Beats of the entrance the app listens for. draft: the pen starts across the sheet and the workstation is
 * drafted in behind it. docked: the wordmark has landed on the header's. gone: the terminal has let go.
 */
export type EntranceCue = 'draft' | 'docked' | 'gone' | 'skip';

interface SalieriLoginProps {
  /** easterEgg: the original's line was typed at the door ([210] says so only for a login it accepted). */
  onLoginSuccess: (result?: { easterEgg: boolean }) => void;
  /** Kept for API stability: the terminal is one style on both worldlines. */
  worldline?: 'steins_gate' | 'beta';
  onEntranceCue?: (cue: EntranceCue) => void;
  /** The workstation behind the terminal has drawn its first frames; the pen waits for it (STAGE_WAIT at most). */
  stageReady?: boolean;
  /** The workstation was drawn ahead under the terminal while the password was typed: there is nothing to wait for. */
  stagePrepared?: boolean;
}

/** Longest the pen waits for the workstation behind the terminal to be drawn before sweeping anyway. */
const STAGE_WAIT = 1400;
/** The key's tick is drawn before the workstation starts mounting behind the terminal, so nothing heavy lands on it. */
const OK_BEAT = 380;
/** After the handover, the key starts to go this long before the pen sweeps. */
const KEY_GO = 120;

/** Entrance timeline (ms after the server says yes). */
const T = { sweep: 330, sweepDur: 920, fly: 420, flyDur: 840, fade: 150 };
/** Header wordmark baseline inside its 1em line box (EB Garamond: ascent 1.007, descent .298, line-height 1). */
const HEADER_BASELINE = 0.8545;

/** Lower right, an easter egg: the title-screen lines of the original, verbatim (its English included, unedited). */
const VERSE = [
  'There is no end though there is a start in space.\u2014Infinity.',
  'It has own power, it ruins, and it goes though there is a start also in the star.\u2014Finite.',
  'Only the person who has wisdom can read the most foolish one from the history.',
  'The fish that lives in the sea doesn\'t know the world in the land.It also ruins and goes if they have wisdom.',
  'It is funnier that exceeds the speed of light than fish start living in the land.',
  'It can be said that this is an final ultimatum from the god to the people who can fight.',
];
/** Each line is typed out after the one before it (11 ms a character, a short pause between lines). */
const VERSE_AT = VERSE.map((_, i) => VERSE.slice(0, i).reduce((t, l) => t + l.length * 11 + 90, 0));

/** The password peek: an eye drawn in two lines. Shut while the password is masked; it opens to show it. */
const LID_OPEN = 'M3 9Q13 .6 23 9';
const LID_SHUT = 'M3 9Q13 16.4 23 9';
function Peek({ open }: { open: boolean }) {
  return (
    <svg className="sl-peek-eye" viewBox="0 0 26 18" aria-hidden="true" focusable="false">
      <path className="low" d="M3 9Q13 16.4 23 9" />
      <path className="lid" d={open ? LID_OPEN : LID_SHUT} />
      <path className="lash" d="M8 12.1l-1.1 2.3M13 13v2.6M18 12.1l1.1 2.3" />
      <circle className="iris" cx="13" cy="9" r="3.9" />
      <circle className="pupil" cx="13" cy="9" r="1.7" />
    </svg>
  );
}

export const SalieriLogin: React.FC<SalieriLoginProps> = ({ onLoginSuccess, onEntranceCue, worldline = 'steins_gate', stageReady = false, stagePrepared = false }) => {
  const [userId, setUserId] = useState('');
  const [password, setPassword] = useState('');
  // [210] optional startup passcode. Until the server says otherwise a passcode is assumed, as before.
  const [passcodeSet, setPasscodeSet] = useState(true);
  // The passcode record exists but cannot be read: the backend treats it as unset, and says why.
  const [passcodeUnreadable, setPasscodeUnreadable] = useState(false);
  const [peek, setPeek] = useState(false);
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

  const rootRef = useRef<HTMLDivElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const wordWrapRef = useRef<HTMLSpanElement>(null);
  const wordRef = useRef<WordmarkHandle>(null);
  const penRef = useRef<HTMLDivElement>(null);
  const backdropInRef = useRef<HTMLDivElement>(null);
  /** When the server said yes: the entrance's beats are timed from here, however long the stage took. */
  const exitAtRef = useRef(0);
  /** The server said yes; the terminal holds still, still "connecting", until the workstation behind it is drawn. */
  const [admitted, setAdmitted] = useState(false);
  const [stageWaitOver, setStageWaitOver] = useState(false);
  /** The workstation has been mounted for this login (onLoginSuccess called). */
  const [handedOff, setHandedOff] = useState(false);
  const stagePreparedRef = useRef(stagePrepared);
  stagePreparedRef.current = stagePrepared;
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
        const response = await apiFetch('/health', { signal: controller.signal });
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

  // Whether a startup passcode is set ([210]). Without one the field is left empty and Enter opens the terminal.
  useEffect(() => {
    if (backendStatus !== 'ready') return;
    let cancelled = false;
    void (async () => {
      try {
        const response = await apiFetch('/api/auth/state');
        if (!response.ok) return;
        const state = await response.json().catch(() => null) as { passcode_set?: unknown; passcode_error?: unknown } | null;
        if (!cancelled && typeof state?.passcode_set === 'boolean') setPasscodeSet(state.passcode_set);
        if (!cancelled) setPasscodeUnreadable(typeof state?.passcode_error === 'string');
      } catch {
        // Unknown: keep asking for the passcode; the login call itself still decides.
      }
    })();
    return () => { cancelled = true; };
  }, [backendStatus]);

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

  // While the terminal waits, the workstation's fonts and pictures load and decode, so the entrance does not have to.
  useEffect(() => warmWorkstationFonts(), []);
  useEffect(() => prewarmWorkstation(worldline), [worldline]);

  // Once the password is accepted the form folds into the key (handover). Usually the workstation was drawn ahead
  // under the terminal while the password was typed (App, `stagePrepared`): unlocking it and letting the key go are
  // then one beat (U 11-10). Otherwise it mounts now, and the key's dial turns, a motion only the compositor runs,
  // until its first frames are drawn (U 11-09: the first entrance after the app starts stuttered; U 11-10: a still
  // wait after the tick felt stuck). Then the key goes and the pen sweeps. STAGE_WAIT caps the wait.
  useEffect(() => {
    if (!admitted || reduced) return undefined;
    const id = window.setTimeout(() => setStageWaitOver(true), STAGE_WAIT);
    return () => window.clearTimeout(id);
  }, [admitted, reduced]);
  // Only this visit's workstation can say it is drawn: the word is heard once it has been handed the login. One drawn
  // ahead under the terminal (the usual case) needs no word: the key goes as soon as the form has folded into it.
  useEffect(() => {
    if (!handedOff || exiting || reduced || !(stageReady || stageWaitOver || stagePrepared)) return;
    setExiting(true);
    runEntrance();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handedOff, exiting, reduced, stageReady, stageWaitOver, stagePrepared]);

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
    const inner = backdropInRef.current;
    const canAnimate = typeof pen?.animate === 'function' && typeof backdrop?.animate === 'function' && typeof inner?.animate === 'function';
    // Beats keep their spacing; the form folded away during the handover, so that time comes off the pause before the
    // sweep, which then waits only for the key to start going.
    const lag = exitAtRef.current ? performance.now() - exitAtRef.current : 0;
    const sweepAt = Math.max(KEY_GO, T.sweep - lag);
    if (canAnimate) {
      const timing: KeyframeAnimationOptions = { duration: T.sweepDur, delay: sweepAt, easing: 'cubic-bezier(.62, 0, .3, 1)', fill: 'forwards' };
      // The paper is wiped from the left by sliding its window right while its contents slide left by as much: both
      // are transforms, so the compositor runs the wipe even while the page's main thread is busy (a clip-path
      // animation stalled with it on the first entrance after the app starts). They are handed to it now, with their
      // delay, so a busy main thread (the workstation just unlocked behind) cannot hold the sweep back either.
      animsRef.current.push(
        pen!.animate([{ transform: 'translateX(0)', opacity: 1 }, { transform: 'translateX(100vw)', opacity: 1 }], timing),
        backdrop!.animate([{ transform: 'translateX(0)' }, { transform: 'translateX(100vw)' }], timing),
        inner!.animate([{ transform: 'translateX(0)' }, { transform: 'translateX(-100vw)' }], timing),
      );
    }
    later(() => {
      plateRef.current?.tick(4);
      if (!canAnimate) { cue('draft'); cue('docked'); cue('gone'); return; }
      setSweeping(true);
      cue('draft');
    }, sweepAt);

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
    }, sweepAt + T.fly - T.sweep);
  };

  const handleInitAudio = () => {
    audioPlayer.init();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (backendStatus !== 'ready' || exiting || admitted) return;
    handleInitAudio();
    if (!userId) {
      showAuthError('请输入 USER ID', 'user');
      return;
    }
    if (userId !== 'Salieri') {
      showAuthError('无效的 USER ID', 'user');
      return;
    }
    if (!password && passcodeSet) {
      showAuthError('请输入访问密码', 'password');
      return;
    }

    setIsAuthenticating(true);
    setPeek(false);
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
      const response = await apiFetch('/api/auth/login', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ passcode: password || null }),
      });

      if (response.ok) {
        const result = await response.json().catch(() => null) as { easter_egg?: unknown } | null;
        accepted = true;
        audioService.playSFX('/audio/sfx/连接成功，进入页面2.ogg');
        audioService.stopBGM();
        succeededRef.current = true;
        (document.activeElement as HTMLElement | null)?.blur?.();
        setIntro(false);
        setAdmitted(true);
        exitAtRef.current = performance.now();
        const easterEgg = result?.easter_egg === true;
        if (reduced) {
          setExiting(true);
          onLoginSuccess({ easterEgg });
        } else {
          later(() => {
            setHandedOff(true);
            // Drawn ahead: unlocking it and letting the key go are one beat, not a wait for its word.
            if (stagePreparedRef.current) { setExiting(true); runEntrance(); }
            onLoginSuccess({ easterEgg });
          }, OK_BEAT);
        }
      } else if (response.status === 401 || response.status === 403) {
        showAuthError('密码错误', 'password');
      } else if (response.status === 429) {
        showAuthError('试得太快了，请稍等几秒', 'password');
      } else {
        const errorData = await response.json().catch(() => ({}));
        const detail = errorData.detail;
        showAuthError(typeof detail === 'string' ? detail : detail?.message || '访问被拒绝', 'password');
      }
    } catch (error) {
      showAuthError('系统连接已断开', 'password');
    } finally {
      if (!accepted) setIsAuthenticating(false);
    }
  };

  // Accepted: the loader stops and the key shows its tick at once; the form folds into the key (handover), whose dial
  // turns if the workstation behind it still has to be drawn, and then the pen sweeps.
  const busy = isAuthenticating && !admitted;
  const rootClass = [
    'sl-root',
    admitted && !reduced && 'handover',
    exiting && 'exiting',
    sweeping && 'sweeping',
    reduced && 'reduced',
    intro && 'intro',
  ].filter(Boolean).join(' ');

  return (
    <div
      ref={rootRef}
      className={rootClass}
      // The audio context is created on the first key or click, not inside the submit that starts the entrance.
      onKeyDownCapture={() => { if (!exiting) audioPlayer.init(); }}
      onPointerDownCapture={() => { if (!exiting) audioPlayer.init(); }}
      onPointerDown={(e) => {
        if (!exiting || reduced) return;
        // The skipping click is spent here: its mousedown must not then pull focus off the composer.
        e.preventDefault();
        animsRef.current.forEach((a) => a.cancel());
        cue('skip');
      }}
      onTransitionEnd={(e) => {
        if (exiting && reduced && e.target === e.currentTarget && e.propertyName === 'opacity') cue('gone');
      }}
    >
      <div ref={backdropRef} className="sl-backdrop">
        <div ref={backdropInRef} className="sl-backdrop-in">
        <div className="sl-grid" aria-hidden="true" />
        <div className="sl-texture has-image" aria-hidden="true" />
        <div className="sl-plate"><Blueprint ref={plateRef} reduced={reduced} busy={busy} divergence={DIVERGENCE[worldline]} /></div>
        <div className="sl-frame" aria-hidden="true">
          <div className="zones top">{[1, 2, 3, 4, 5, 6, 7, 8].map((n) => <span key={n}>{n}</span>)}</div>
          <div className="zones bottom">{[1, 2, 3, 4, 5, 6, 7, 8].map((n) => <span key={n}>{n}</span>)}</div>
          <div className="zones left">{['A', 'B', 'C', 'D', 'E', 'F'].map((n) => <span key={n}>{n}</span>)}</div>
          <div className="zones right">{['A', 'B', 'C', 'D', 'E', 'F'].map((n) => <span key={n}>{n}</span>)}</div>
        </div>
        {/* Whose paper this is, printed in the margin outside the border the way drafting stock carries its maker. */}
        <p className="sl-imprint" aria-hidden="true">VIKTOR CHONDRIA UNIV. · BRAIN SCIENCE INST.</p>
        <div className="sl-verse" aria-hidden="true">
          {VERSE.map((line, i) => (
            <p key={i} style={{ '--n': line.length, '--t': `${VERSE_AT[i]}ms` } as React.CSSProperties}>{line}</p>
          ))}
        </div>
        <p className="sl-note" aria-hidden="true">LOCAL LINK</p>
        <div className="sl-vignette" aria-hidden="true" />
        </div>
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
            <label className={`sl-label${focusField === 'user' || errorField === 'user' ? ' is-on' : ''}`} htmlFor="salieri-user"><Plate press="sl-press" /><span>USER ID</span></label>
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
            <label className={`sl-label${focusField === 'password' || errorField === 'password' ? ' is-on' : ''}`} htmlFor="salieri-pwd"><Plate press="sl-press" /><span>PASSWORD</span></label>
            <div className={`sl-slot${capsLock ? ' has-caps' : ''}`}>
              <input
                ref={pwdRef}
                id="salieri-pwd"
                type={peek ? 'text' : 'password'}
                className="sl-input password-input"
                value={password}
                onChange={(e) => { setPassword(e.target.value); if (!e.target.value) setPeek(false); plateRef.current?.tick(); }}
                onFocus={() => setFocusField('password')}
                onBlur={() => { setFocusField((f) => (f === 'password' ? null : f)); setCapsLock(false); }}
                onKeyDown={(e) => {
                  setCapsLock(e.getModifierState?.('CapsLock') ?? false);
                  // Alt+F8 shows or hides the password, as the system's own reveal button does.
                  if (e.altKey && e.key === 'F8' && password) { e.preventDefault(); setPeek((v) => !v); }
                }}
                onKeyUp={(e) => setCapsLock(e.getModifierState?.('CapsLock') ?? false)}
                disabled={isAuthenticating}
                spellCheck={false}
                autoComplete="off"
                aria-invalid={errorField === 'password' || undefined}
              />
              {capsLock && <span className="sl-caps" aria-hidden="true">CAPS</span>}
              <button
                type="button"
                className={`sl-peek${password ? ' is-shown' : ''}${peek ? ' is-open' : ''}`}
                tabIndex={-1}
                aria-label={peek ? '隐藏密码' : '显示密码'}
                aria-pressed={peek}
                aria-hidden={!password || undefined}
                title={peek ? '隐藏密码（Alt+F8）' : '显示密码（Alt+F8）'}
                disabled={isAuthenticating || !password}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => { setPeek((v) => !v); pwdRef.current?.focus(); }}
              >
                <Peek open={peek} />
              </button>
              <i className="sl-ul" aria-hidden="true" />
              <Ruler n={[...password].length} />
            </div>
            <button
              type="submit"
              className={`decrypt-btn sl-go${busy ? ' is-busy' : ''}${admitted ? ' is-ok' : ''}`}
              disabled={isAuthenticating || backendStatus !== 'ready'}
              title="CONNECT"
              aria-label="Decrypt and Connect"
            >
              <KeyGlyph />
            </button>
          </div>

          <div className="sl-msg-line">
            {admitted && <p className="sl-sr" role="status">已连接，正在打开工作台</p>}
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
            {!errorMsg && backendStatus === 'ready' && !passcodeSet && !exiting && (
              <p className="sl-msg is-note">
                {passcodeUnreadable ? '口令文件读不出来，这次不问口令' : '没有设口令，直接按 Enter 进入'}
              </p>
            )}
          </div>
        </form>
      </div>

      <PressFilter id="sl-press" className="sl-defs" />

      <div ref={penRef} className="sl-pen" aria-hidden="true">
        <i className="s1" /><i className="s2" /><i className="s3" /><i className="s4" /><i className="s5" />
      </div>
    </div>
  );
};
