import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useAmadeusWS } from '../hooks/useAmadeusWS';
import { StatusBar } from './StatusBar';
import { SettingsModal } from './SettingsModal';
import { SoundPanel } from './SoundPanel';
import { HistoryPanel } from './HistoryPanel';
import AvatarViewer from './AvatarViewer';
import SpriteAnimator from './SpriteAnimator';
import { HudBadge } from './HudBadge';
import { useAudioPlayer, audioPlayer } from '../hooks/useAudioPlayer';
import { audioService } from '../services/AudioService';

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
  const [audioError, setAudioError] = useState<string | null>(null);
  const [isSoundOpen, setIsSoundOpen] = useState(false);
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);

  // Sync AudioService with state
  useEffect(() => {
    audioService.setEnableBGM(enableBgm);
    audioService.setBGMVolume(bgmVolume);
    audioService.setEnableSFX(enableSfx);
    audioService.setSFXVolume(sfxVolume);
  }, [enableBgm, bgmVolume, enableSfx, sfxVolume]);

  // Start BGM on mount
  useEffect(() => {
    audioService.playBGM('/audio/bgm/Amadeus.ogg', bgmVolume);
    return () => {
      audioService.stopBGM();
    };
  }, []);

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
    activeTool,
    activeNotice,
    emotion,
    sendChat,
    updateConfig,
  } = useAmadeusWS({
    apiKey,
    systemPrompt,
    temperature: 0.8,
    worldline,
    enableTts,
    sovitsUrl,
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

    try {
      localStorage.setItem('amadeus_pc_api_key', newSettings.apiKey);
      localStorage.setItem('amadeus_pc_enable_bgm', String(newSettings.enableBgm));
      localStorage.setItem('amadeus_pc_bgm_volume', String(newSettings.bgmVolume));
      localStorage.setItem('amadeus_pc_enable_sfx', String(newSettings.enableSfx));
      localStorage.setItem('amadeus_pc_sfx_volume', String(newSettings.sfxVolume));
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
      temperature: 0.8,
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

      {worldline === 'beta' && (
        <div className="beta-divergence-watermark">1.130205%</div>
      )}

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
            onClick={() => { audioService.playSFX('/audio/sfx/提示音1.ogg', sfxVolume); setIsSoundOpen((v) => !v); setIsHistoryOpen(false); }}
            title="音量控制"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
              <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07" />
            </svg>
          </button>
          <button
            className="ctrl-btn icon-btn"
            onClick={() => { audioService.playSFX('/audio/sfx/提示音1.ogg', sfxVolume); setIsHistoryOpen((v) => !v); setIsSoundOpen(false); }}
            title="会话历史"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
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
        {/* Left Side: Sidebar Placeholders */}
        <div className="grid-sidebar">
          <div className="sidebar-section">
            <h3 className="section-title">会话历史</h3>
            <div className="sidebar-placeholder">
              <span className="blink-txt">&gt; 历史记录离线</span>
              <p className="placeholder-note">Tauri SQLite 数据库连接已激活。历史已同步。</p>
            </div>
          </div>
          <div className="sidebar-section">
            <h3 className="section-title">系统预设</h3>
            <div className="sidebar-placeholder">
              <span>&gt; 基础记忆保留模式</span>
              <p className="placeholder-note">牧瀬红莉栖大脑模块拷贝激活。</p>
            </div>
          </div>
        </div>

        {/* Center: Dialogue Terminal */}
        <div className="grid-terminal">
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
                  <div className="message-card assistant loading-state">
                    <div className="avatar-label">AMADEUS: KURISU</div>
                    <div className="thinking-loader">
                      <span className="loader-dot" />
                      <span className="loader-dot" />
                      <span className="loader-dot" />
                      <span className="thinking-txt">
                        {activeTool === 'web_search'
                          ? '正在联网检索外部数据库...'
                          : (activeNotice || '正在调用记忆模块...')}
                      </span>
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
            <button
              type="submit"
              className="chat-send-btn"
              disabled={status !== 'READY' || !inputText.trim() || isThinking || isSynthesizing || isPlaying}
            >
              发送
            </button>
          </form>
        </div>

        {/* Right: Avatar Viewer Panel */}
        <div className="grid-avatar-panel">
          <AvatarViewer
            emotion={emotion}
            isSpeaking={isPlaying}
            volume={currentVolume}
            glitchState={glitchState}
          />
          {isThinking && (
            <div className="thinking-spinner-container">
              <SpriteAnimator
                spriteSrc={worldline === 'steins_gate' ? '/assets/ui/spinner_sg_20f.png' : '/assets/ui/spinner_beta_20f.png'}
                cols={10}
                rows={2}
                frameWidth={worldline === 'beta' ? 54.5 : undefined}
                frameHeight={worldline === 'beta' ? 56 : undefined}
                fps={12}
                loop={true}
                style={
                  worldline === 'beta'
                    ? { filter: 'drop-shadow(0 0 6px rgba(255, 69, 48, 0.7))' }
                    : undefined
                }
              />
              <span className="thinking-spinner-label">思考中...</span>
            </div>
          )}
        </div>
      </div>

      {/* HUD Badge */}
      <HudBadge worldline={worldline} status={status} />

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

      <HistoryPanel
        isOpen={isHistoryOpen}
        onClose={() => setIsHistoryOpen(false)}
        messages={messages}
      />
    </div>
  );
};
export default Workstation;
