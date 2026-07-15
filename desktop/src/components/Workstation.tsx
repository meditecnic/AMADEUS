import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useAmadeusWS } from '../hooks/useAmadeusWS';
import { StatusBar } from './StatusBar';
import { SettingsModal } from './SettingsModal';
import { SoundPanel } from './SoundPanel';
import { HistorySidebar } from './HistorySidebar';
import { TtsDebugPanel } from './TtsDebugPanel';
import AvatarViewer from './AvatarViewer';
import SpriteAnimator from './SpriteAnimator';
import { HudBadge } from './HudBadge';
import { useAudioPlayer, audioPlayer } from '../hooks/useAudioPlayer';
import { audioService } from '../services/AudioService';
import { CRTOverlay } from './CRTOverlay';
import { CircuitTrace } from './CircuitTrace';

interface WorkstationProps {
  worldline: 'steins_gate' | 'beta';
  onWorldlineChange: (wl: 'steins_gate' | 'beta') => void;
  onLogout: () => void;
}

export const Workstation: React.FC<WorkstationProps> = ({
  worldline,
  onWorldlineChange,
  onLogout,
}) => {
  const [systemPrompt, setSystemPrompt] = useState('');
  const [enableTts, setEnableTts] = useState(true);
  const [sovitsUrl, setSovitsUrl] = useState('http://127.0.0.1:9880');
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [inputText, setInputText] = useState('');
  const [apiKey, setApiKey] = useState(() => {
    try {
      return localStorage.getItem('amadeus_pc_api_key') || '';
    } catch (e) {
      return '';
    }
  });

  const [enableBgm, setEnableBgm] = useState(() => {
    try { return localStorage.getItem('amadeus_pc_enable_bgm') !== 'false'; } catch { return true; }
  });
  const [bgmVolume, setBgmVolume] = useState(() => {
    try { const v = localStorage.getItem('amadeus_pc_bgm_volume'); return v ? parseFloat(v) : 0.15; } catch { return 0.15; }
  });
  const [enableSfx, setEnableSfx] = useState(() => {
    try { return localStorage.getItem('amadeus_pc_enable_sfx') !== 'false'; } catch { return true; }
  });
  const [sfxVolume, setSfxVolume] = useState(() => {
    try { const v = localStorage.getItem('amadeus_pc_sfx_volume'); return v ? parseFloat(v) : 1.0; } catch { return 1.0; }
  });
  const [temperature, setTemperature] = useState(() => {
    try { const v = localStorage.getItem('amadeus_pc_temperature'); return v ? parseFloat(v) : 0.8; } catch { return 0.8; }
  });
  const [model, setModel] = useState<'deepseek-v4-flash' | 'deepseek-v4-pro'>(() =>
    (localStorage.getItem('amadeus_pc_model') as any) || 'deepseek-v4-flash');
  const [reasoningEffort, setReasoningEffort] = useState<'high' | 'xhigh'>(() =>
    (localStorage.getItem('amadeus_pc_reasoning_effort') as any) || 'high');
  const [audioError, setAudioError] = useState<string | null>(null);
  const [isSoundOpen, setIsSoundOpen] = useState(false);
  const [isHistoryCollapsed, setIsHistoryCollapsed] = useState(() => {
    try {
      const stored = localStorage.getItem('amadeus_pc_history_collapsed');
      if (stored !== null) return stored === 'true';
    } catch {}
    return false; // 默认展开，让三列布局立即可见
  });

  // Persist history collapsed state
  useEffect(() => {
    try { localStorage.setItem('amadeus_pc_history_collapsed', String(isHistoryCollapsed)); } catch {}
  }, [isHistoryCollapsed]);

  const TTS_DEBUG_ENABLED = (() => {
    try { return localStorage.getItem('amadeus_pc_debug_tts') === 'true'; } catch { return false; }
  })();

  // Sync AudioService with state
  useEffect(() => {
    audioService.setEnableBGM(enableBgm);
    audioService.setBGMVolume(bgmVolume);
    audioService.setEnableSFX(enableSfx);
    audioService.setSFXVolume(sfxVolume);
  }, [enableBgm, bgmVolume, enableSfx, sfxVolume]);

  // Start BGM on mount, switch on worldline change
  useEffect(() => {
    audioService.playBGMForWorldline(worldline, bgmVolume);
    return () => {
      audioService.stopBGM();
    };
  }, [worldline]);

  // Auto-clear audio error after 5s
  useEffect(() => {
    if (audioError) {
      const timer = window.setTimeout(() => setAudioError(null), 5000);
      return () => window.clearTimeout(timer);
    }
  }, [audioError]);

  // WebSocket hook connection management
  const {
    status,
    messages,
    isThinking,
    isSynthesizing,
    emotion,
    sendChat,
    sendDebugTts,
    updateConfig,
    wsRef,
  } = useAmadeusWS({
    apiKey,
    systemPrompt,
    temperature,
    worldline,
    enableTts,
    sovitsUrl,
    model,
    reasoningEffort,
    onAudioChunk: (_sentenceId, base64Data) => {
      audioPlayer.playBase64(base64Data);
    },
    onAudioDegraded: (reason: string) => {
      setAudioError(reason);
    },
  });

  const { currentVolume, isPlaying } = useAudioPlayer();

  const [glitchState, setGlitchState] = useState<'none' | 'light' | 'heavy'>('none');

  const triggerGlitch = useCallback((intensity: 'light' | 'heavy') => {
    setGlitchState(intensity);
    const duration = intensity === 'heavy' ? 250 : 120;
    window.setTimeout(() => {
      setGlitchState('none');
    }, duration);
  }, []);

  const prevEmotionRef = useRef(emotion);

  useEffect(() => {
    if (emotion !== prevEmotionRef.current) {
      if (emotion === 'tsundere') {
        triggerGlitch('heavy');
      } else {
        triggerGlitch('light');
      }
      prevEmotionRef.current = emotion;
    }
  }, [emotion, triggerGlitch]);

  // Play error SFX on WebSocket error
  useEffect(() => {
    if (status === 'ERROR') {
      audioService.playSFX('/assets/audio/gah.ogg', sfxVolume);
    }
  }, [status]);

  const handleSend = (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputText.trim()) return;
    audioService.playSFX('/audio/sfx/连接成功，进入页面1.ogg', sfxVolume);
    sendChat(inputText.trim());
    setInputText('');
  };

  const handleSaveSettings = (newSettings: {
    worldline: 'steins_gate' | 'beta';
    apiKey: string;
    systemPrompt: string;
    enableTts: boolean;
    sovitsUrl: string;
    enableBgm: boolean;
    bgmVolume: number;
    enableSfx: boolean;
    sfxVolume: number;
    temperature: number;
    model: 'deepseek-v4-flash' | 'deepseek-v4-pro';
    reasoningEffort: 'high' | 'xhigh';
  }) => {
    // 1. Sync React parent state if worldline changed
    if (newSettings.worldline !== worldline) {
      onWorldlineChange(newSettings.worldline);
    }
    
    // 2. Sync local states
    setApiKey(newSettings.apiKey);
    setSystemPrompt(newSettings.systemPrompt);
    setEnableTts(newSettings.enableTts);
    setSovitsUrl(newSettings.sovitsUrl);
    setEnableBgm(newSettings.enableBgm);
    setBgmVolume(newSettings.bgmVolume);
    setEnableSfx(newSettings.enableSfx);
    setSfxVolume(newSettings.sfxVolume);
    setTemperature(newSettings.temperature);
    setModel(newSettings.model);
    setReasoningEffort(newSettings.reasoningEffort);

    try {
      localStorage.setItem('amadeus_pc_api_key', newSettings.apiKey);
      localStorage.setItem('amadeus_pc_enable_bgm', String(newSettings.enableBgm));
      localStorage.setItem('amadeus_pc_bgm_volume', String(newSettings.bgmVolume));
      localStorage.setItem('amadeus_pc_enable_sfx', String(newSettings.enableSfx));
      localStorage.setItem('amadeus_pc_sfx_volume', String(newSettings.sfxVolume));
      localStorage.setItem('amadeus_pc_temperature', String(newSettings.temperature));
      localStorage.setItem('amadeus_pc_model', newSettings.model);
      localStorage.setItem('amadeus_pc_reasoning_effort', newSettings.reasoningEffort);
    } catch (e) {
      console.warn('LocalStorage save failed:', e);
    }

    // 3. Send config_update WS frame to backend
    updateConfig({
      apiKey: newSettings.apiKey,
      systemPrompt: newSettings.systemPrompt,
      worldline: newSettings.worldline,
      enableTts: newSettings.enableTts,
      sovitsUrl: newSettings.sovitsUrl,
      temperature: newSettings.temperature,
      model: newSettings.model,
      reasoningEffort: newSettings.reasoningEffort,
    });
  };

  const handleAudioInit = () => {
    audioPlayer.init();
  };

  return (
    <div className={`salieri-workstation-panel ${worldline === 'beta' ? 'worldline-beta' : ''}`} onMouseDown={handleAudioInit}>
      {/* Background layers */}
      <div
        className="workstation-bg-layer"
        style={{
          backgroundImage: worldline === 'beta'
            ? 'url(/assets/ui/bg_beta_butterfly.png)'
            : 'url(/assets/ui/bg_main.png)',
        }}
      />
      <div className="workstation-bg-overlay" />
      <div className="workstation-bg-reflection" />

      {/* SVG circuit trace decorative overlay */}
      <CircuitTrace opacity={0.15} zIndex={2} />

      {/* SVG feTurbulence noise overlay */}
      <div className="workstation-bg-noise" />

      {/* CRT vignette + chromatic aberration (z-index 50, above content, below modals) */}
      <CRTOverlay glitchState={glitchState} zIndex={50} />

      {worldline === 'beta' && (
        <div className="beta-divergence-watermark">1.130205%</div>
      )}

      <div className="salieri-workstation-inner">
        <div className="workstation-panel-frame-overlay" />
        {/* Top Header Controls */}
        <div className="workstation-header">
        <div className="left-brand">
          <span className="brand-title">Amadeus</span>
          <span className="brand-sub">SALIERI CONSOLE</span>
        </div>
        <div className="right-controls">
          <button
            className="ctrl-btn icon-btn"
            onClick={() => { audioService.playSFX('/audio/sfx/提示音1.ogg', sfxVolume); setIsSettingsOpen(true); }}
            title="系统设置"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </button>
          <button
            className="ctrl-btn icon-btn"
            onClick={() => { audioService.playSFX('/audio/sfx/提示音1.ogg', sfxVolume); setIsSoundOpen((v) => !v); }}
            title="音量控制"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
              <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07" />
            </svg>
          </button>
          <button
            className="ctrl-btn icon-btn"
            onClick={() => { audioService.playSFX('/audio/sfx/提示音1.ogg', sfxVolume); setIsHistoryCollapsed((v) => !v); }}
            title={isHistoryCollapsed ? '展开历史面板' : '折叠历史面板'}
          >
            <span style={{ fontSize: '14px', lineHeight: 1 }}>{isHistoryCollapsed ? '▶' : '◀'}</span>
          </button>
          <button
            className="ctrl-btn icon-btn disconnect-btn"
            onClick={onLogout}
            title="断开连接"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
              <polyline points="16 17 21 12 16 7" />
              <line x1="21" y1="12" x2="9" y2="12" />
            </svg>
          </button>
        </div>
      </div>

      {/* Main Grid Workspace */}
      <div className="workstation-grid">
        {/* Left: History Sidebar */}
        <HistorySidebar
          isCollapsed={isHistoryCollapsed}
          onToggle={() => setIsHistoryCollapsed((v) => !v)}
          messages={messages}
        />

        {/* Center: Dialogue Terminal */}
        <div className="grid-terminal border-beam">
          <div className="terminal-chat-viewport">
            {messages.length === 0 ? (
              <div className="empty-chat-tip">
                <div className="amadeus-logo-glitched" />
                <p className="terminal-welcome-msg">&gt; Amadeus 系统连接已建立。等待用户输入...</p>
              </div>
            ) : (
              <div className="messages-flow">
                {messages.map((msg) => (
                  <div key={msg.id} className={`message-card ${msg.role}`}>
                    <div className="avatar-label">
                      {msg.role === 'user' ? 'USER ID: OKABE' : 'AMADEUS: KURISU'}
                    </div>
                    <div className="message-content-box">
                      {msg.role === 'assistant' && msg.translation ? (
                        <>
                          <p className="chinese-main">{msg.translation}</p>
                          <p className="japanese-sub">（{msg.content}）</p>
                        </>
                      ) : (
                        <p className="japanese-raw">{msg.content}</p>
                      )}
                    </div>
                  </div>
                ))}
                {isThinking && (
                  <div className="message-card assistant thinking-bubble-active">
                    <div className="avatar-label" style={{ fontSize: '10px' }}>AMADEUS</div>
                    <div className="message-content-box thinking-card-box">
                      <div className="thinking-indicator-card">
                        <SpriteAnimator
                          spriteSrc={worldline === 'steins_gate' ? '/assets/ui/spinner_sg_20f.png' : '/assets/ui/spinner_beta_20f.png'}
                          cols={10}
                          rows={2}
                          frameWidth={worldline === 'beta' ? 54.5 : 54}
                          frameHeight={63}
                          frameCount={20}
                          fps={15}
                          loop={true}
                        />
                        <span className="thinking-text-glow">
                          Thinking<span className="thinking-cursor-blink">█</span>
                        </span>
                      </div>
                    </div>
                  </div>
                )}
                {isSynthesizing && !isThinking && (
                  <div className="status-synthesizing-tag">
                    <span className="speaking-dot animate-ping" />
                    正在合成语音...
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Bottom Chat Input Form */}
          <form className="terminal-input-bar" onSubmit={handleSend}>
            <span className="prompt-arrow">&gt;</span>
            <input
              type="text"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              className="chat-input-field"
              placeholder="输入终端指令或对话内容..."
              disabled={status !== 'READY' || isThinking || isSynthesizing || isPlaying}
            />
            {isPlaying && (
              <span className="tts-waiting-hint">正在播放语音…</span>
            )}
            {isPlaying && (
              <button
                type="button"
                className="interrupt-btn"
                onClick={() => {
                  audioService.playSFX('/audio/sfx/提示音1.ogg', sfxVolume);
                  wsRef.current?.send(JSON.stringify({ type: 'interrupt-signal' }));
                }}
                title="中断当前语音"
              >
                中断
              </button>
            )}
            <button
              type="submit"
              className="chat-send-btn"
              disabled={status !== 'READY' || !inputText.trim() || isThinking || isSynthesizing || isPlaying}
              aria-label="发送"
            />
          </form>
          <div className="model-bottom-bar" style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '6px 16px', borderTop: '1px solid #2a1500', background: '#0a0806' }}>
            <select
              value={model}
              onChange={(e) => {
                const v = e.target.value as 'deepseek-v4-flash' | 'deepseek-v4-pro';
                setModel(v);
                localStorage.setItem('amadeus_pc_model', v);
                updateConfig({ model: v });
              }}
              style={{ background: '#0c0806', color: '#ff6c00', border: '1px solid #5a2500', padding: '4px 8px', fontSize: 12, fontFamily: 'Share Tech Mono, monospace' }}
            >
              <option value="deepseek-v4-flash">V4 Flash</option>
              <option value="deepseek-v4-pro">V4 Pro</option>
            </select>
            <span style={{ color: '#5a2500', fontSize: 11, fontFamily: 'Share Tech Mono, monospace' }}>推理深度</span>
            <input
              type="range" min={0} max={1} step={1}
              value={reasoningEffort === 'xhigh' ? 1 : 0}
              onChange={(e) => {
                const v = e.target.value === '1' ? 'xhigh' : 'high';
                setReasoningEffort(v);
                localStorage.setItem('amadeus_pc_reasoning_effort', v);
                updateConfig({ reasoningEffort: v });
              }}
              style={{ '--val': `${(reasoningEffort === 'xhigh' ? 1 : 0) * 100}%`, width: 60 } as React.CSSProperties}
            />
            <span style={{ color: '#8b3c00', fontSize: 11, fontFamily: 'Share Tech Mono, monospace' }}>{reasoningEffort === 'xhigh' ? '极高' : '高'}</span>
          </div>
        </div>

        {/* Right: Avatar Viewer Panel */}
        <div className="grid-avatar-panel">
          <HudBadge worldline={worldline} status={status} />
          <AvatarViewer
            emotion={emotion}
            isSpeaking={isPlaying}
            volume={currentVolume}
            glitchState={glitchState}
          />
          {/* Thinking spinner is now rendered inside messages-flow */}
        </div>
      </div>

      {/* Audio Error Toast */}
      {audioError && (
        <div className="audio-error-toast">
          <span className="audio-error-msg">
            {audioError === 'action_only' && '> TTS skipped (action description)'}
            {audioError === 'user_disabled' && '> TTS disabled'}
            {audioError === 'service_error' && '> TTS service unavailable'}
            {audioError === 'unknown' && '> TTS error'}
            {![ 'action_only', 'user_disabled', 'service_error', 'unknown' ].includes(audioError) && `> TTS: ${audioError}`}
          </span>
        </div>
      )}

      {/* Bottom Status Bar */}
      <StatusBar worldline={worldline} connectionStatus={status} />
      </div>

      {/* Settings Modal overlay */}
      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        worldline={worldline}
        apiKey={apiKey}
        systemPrompt={systemPrompt}
        enableTts={enableTts}
        sovitsUrl={sovitsUrl}
        enableBgm={enableBgm}
        bgmVolume={bgmVolume}
        enableSfx={enableSfx}
        sfxVolume={sfxVolume}
        temperature={temperature}
        model={model}
        reasoningEffort={reasoningEffort}
        onSaveSettings={handleSaveSettings}
      />

      <SoundPanel
        isOpen={isSoundOpen}
        onClose={() => setIsSoundOpen(false)}
        enableBgm={enableBgm}
        bgmVolume={bgmVolume}
        enableSfx={enableSfx}
        sfxVolume={sfxVolume}
        enableTts={enableTts}
        onEnableBgmChange={setEnableBgm}
        onBgmVolumeChange={setBgmVolume}
        onEnableSfxChange={setEnableSfx}
        onSfxVolumeChange={setSfxVolume}
        onEnableTtsChange={setEnableTts}
      />

      {TTS_DEBUG_ENABLED && (
        <TtsDebugPanel
          sendDebugTts={sendDebugTts}
          isSynthesizing={isSynthesizing}
          isPlaying={isPlaying}
        />
      )}
    </div>
  );
};
export default Workstation;
