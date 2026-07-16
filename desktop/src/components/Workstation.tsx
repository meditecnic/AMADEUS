import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  getOrCreateAmadeusSessionId,
  useAmadeusWS,
  type Message,
} from '../hooks/useAmadeusWS';
import { StatusBar } from './StatusBar';
import { SettingsModal } from './SettingsModal';
import { SoundPanel } from './SoundPanel';
import { HistorySidebar, type ConversationSummary } from './HistorySidebar';
import { TtsDebugPanel } from './TtsDebugPanel';
import AvatarViewer from './AvatarViewer';
import SpriteAnimator from './SpriteAnimator';
import { HudBadge } from './HudBadge';
import { DivergenceMeter } from './DivergenceMeter';
import { useAudioPlayer, audioPlayer } from '../hooks/useAudioPlayer';
import { audioService } from '../services/AudioService';
import { CRTOverlay } from './CRTOverlay';
import { CircuitTrace } from './CircuitTrace';
import { BilingualMessage } from './BilingualMessage';
import { ForgetConversationModal } from './ForgetConversationModal';
import {
  ProviderControls,
  type ProviderMetadata,
} from './ProviderControls';

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
  const [apiKey, setApiKey] = useState('');
  const [credentialChecked, setCredentialChecked] = useState(false);
  const [providers, setProviders] = useState<ProviderMetadata[]>([]);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [sessionId] = useState(getOrCreateAmadeusSessionId);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [conversationSearch, setConversationSearch] = useState('');
  const [conversationBusy, setConversationBusy] = useState(false);
  const [forgetTarget, setForgetTarget] = useState<ConversationSummary | null>(null);

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
  const [model, setModel] = useState<string>(() =>
    localStorage.getItem('amadeus_pc_model') || 'deepseek-v4-flash');
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

  // Start the worldline playlist on mount, switch it when the line changes,
  // and resume it when BGM is enabled again in the settings panel.
  useEffect(() => {
    if (enableBgm) audioService.playBGMForWorldline(worldline, bgmVolume);
    else audioService.stopBGM();
    return () => {
      audioService.stopBGM();
    };
  }, [worldline, enableBgm]);

  // Auto-clear audio error after 5s
  useEffect(() => {
    if (audioError) {
      const timer = window.setTimeout(() => setAudioError(null), 5000);
      return () => window.clearTimeout(timer);
    }
  }, [audioError]);

  const loadConversations = useCallback(async () => {
    const response = await fetch(
      `/api/conversations?session_id=${encodeURIComponent(sessionId)}&worldline=${encodeURIComponent(worldline)}`
    );
    if (!response.ok) throw new Error(`conversation query failed: ${response.status}`);
    const rows = await response.json() as ConversationSummary[];
    setConversations(rows);
    const selected = rows.find((row) => row.is_selected);
    if (selected) {
      setActiveConversationId(selected.id);
      if (selected.model_id) setModel(selected.model_id);
    }
    return rows;
  }, [sessionId, worldline]);

  const loadProviders = useCallback(async () => {
    const response = await fetch('/api/providers');
    if (!response.ok) throw new Error(`provider query failed: ${response.status}`);
    const rows = await response.json() as ProviderMetadata[];
    setProviders(rows);
    return rows;
  }, []);

  // WebSocket hook connection management
  const {
    status,
    messages,
    isThinking,
    isSynthesizing,
    emotion,
    sendChat,
    stopVoice,
    cancelTurn,
    sendDebugTts,
    updateConfig,
    reconnect,
    switchConversation,
    wsRef,
  } = useAmadeusWS({
    sessionId,
    conversationId: activeConversationId,
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
    onTurnStarted: () => {
      audioPlayer.stopAll();
    },
    onTurnCompleted: () => {
      window.setTimeout(() => { void loadConversations(); }, 250);
    },
  });

  useEffect(() => {
    setActiveConversationId(null);
    void loadConversations().catch((error) => {
      console.warn('Conversation synchronization failed:', error);
    });
  }, [loadConversations]);

  useEffect(() => {
    let cancelled = false;
    const synchronizeCredential = async () => {
      let legacyKey = '';
      try {
        legacyKey = localStorage.getItem('amadeus_pc_api_key') || '';
      } catch {}
      try {
        if (legacyKey.trim()) {
          const response = await fetch('/api/providers/deepseek/credentials', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_key: legacyKey.trim() }),
          });
          if (!response.ok) throw new Error(`credential migration failed: ${response.status}`);
          try { localStorage.removeItem('amadeus_pc_api_key'); } catch {}
          if (!cancelled) {
            await loadProviders();
            reconnect();
          }
        } else {
          await loadProviders();
        }
      } catch (error) {
        console.warn('Credential synchronization failed:', error);
      } finally {
        if (!cancelled) setCredentialChecked(true);
      }
    };
    void synchronizeCredential();
    return () => { cancelled = true; };
  }, [loadProviders, reconnect]);

  const activeConversation = conversations.find((row) => row.id === activeConversationId);
  const activeProviderId = activeConversation?.provider_id || 'deepseek';
  const activeProvider = providers.find((provider) => provider.id === activeProviderId);
  const credentialConfigured = Boolean(
    activeProvider && (!activeProvider.credential_required || activeProvider.configured)
  );

  const loadProviderModels = useCallback(async (
    providerId: string,
    refresh = false,
  ) => {
    const provider = providers.find((row) => row.id === providerId);
    const fallback = [
      activeConversation?.model_id || '',
      provider?.default_model || '',
    ].filter(Boolean);
    if (
      !provider?.model_discovery ||
      (provider.credential_required && !provider.configured)
    ) {
      setAvailableModels(Array.from(new Set(fallback)));
      return;
    }
    setModelsLoading(true);
    try {
      const response = await fetch(
        `/api/providers/${encodeURIComponent(providerId)}/models?refresh=${refresh ? 'true' : 'false'}`
      );
      if (!response.ok) throw new Error(`model query failed: ${response.status}`);
      const payload = await response.json() as { models?: string[] };
      setAvailableModels(Array.from(new Set([...fallback, ...(payload.models || [])])));
    } catch (error) {
      setAvailableModels(Array.from(new Set(fallback)));
      console.warn('Provider model discovery failed:', error);
    } finally {
      setModelsLoading(false);
    }
  }, [activeConversation?.model_id, providers]);

  useEffect(() => {
    void loadProviderModels(activeProviderId);
  }, [activeProviderId, loadProviderModels]);

  const { currentVolume, isPlaying } = useAudioPlayer();

  // A missing API key is an onboarding state, not chat history. The socket can
  // emit more than one authentication-related notice while it reconnects;
  // keeping those notices out of the transcript prevents a blank workspace
  // from looking like a failed conversation.
  const needsApiKey = credentialChecked && Boolean(activeProvider) && !credentialConfigured;
  const visibleMessages = needsApiKey
    ? messages.filter((message) => !/(API\s*Key|未认证|缺失\s*API)/i.test(message.content))
    : messages;
  const emptyStateKind = needsApiKey ? 'setup' : status.toLowerCase();
  const emptyStateMessage = needsApiKey
    ? `> 请为 ${activeProvider?.display_name || activeProviderId} 配置 API Key 以建立连接。`
    : status === 'CONNECTING'
      ? '> 正在建立与 Amadeus 核心的连接，请稍候...'
      : status === 'CLOSED'
        ? '> 会话连接已关闭，系统将尝试重新连接。'
        : status === 'ERROR'
          ? '> 暂时无法连接，请检查后端服务与 API Key。'
          : '> Amadeus 系统连接已建立。等待用户输入...';

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
    // Missing credentials are an expected setup state, not a runtime failure.
    if (status === 'ERROR' && credentialConfigured) {
      audioService.playSFX('/assets/audio/gah.ogg', sfxVolume);
    }
  }, [status, credentialConfigured, sfxVolume]);

  const handleSend = (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputText.trim()) return;
    audioService.playSFX('/audio/sfx/连接成功，进入页面1.ogg', sfxVolume);
    sendChat(inputText.trim());
    setInputText('');
  };

  const selectConversation = async (conversation: ConversationSummary, force = false) => {
    if ((!force && conversation.id === activeConversationId) || conversationBusy) return;
    setConversationBusy(true);
    try {
      audioPlayer.stopAll();
      const response = await fetch(
        `/api/conversations/${encodeURIComponent(conversation.id)}/messages?session_id=${encodeURIComponent(sessionId)}&worldline=${encodeURIComponent(worldline)}`
      );
      if (!response.ok) throw new Error(`history query failed: ${response.status}`);
      const rows = await response.json() as Array<{
        role: string;
        content: string;
        translation?: string | null;
      }>;
      const history: Message[] = rows
        .filter((row) => row.role === 'user' || row.role === 'assistant')
        .map((row, index) => ({
          id: `history_${conversation.id}_${index}`,
          role: row.role as 'user' | 'assistant',
          content: row.role === 'assistant'
            ? row.content.replace(/^\[EMO:[^\]]+\]\s*/, '')
            : row.content,
          translation: row.role === 'assistant' ? row.translation || '' : undefined,
          streaming: false,
        }));
      switchConversation(conversation.id, history);
      setActiveConversationId(conversation.id);
      if (conversation.model_id) {
        setModel(conversation.model_id);
        localStorage.setItem('amadeus_pc_model', conversation.model_id);
      }
      setConversations((current) => current.map((row) => ({
        ...row,
        is_selected: row.id === conversation.id,
      })));
    } catch (error) {
      console.warn('Conversation switch failed:', error);
    } finally {
      setConversationBusy(false);
    }
  };

  const createConversation = async () => {
    if (conversationBusy) return;
    setConversationBusy(true);
    try {
      const active = conversations.find((row) => row.id === activeConversationId);
      const response = await fetch('/api/conversations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          worldline,
          provider_id: active?.provider_id || 'deepseek',
          model_id: active?.model_id || model,
        }),
      });
      if (!response.ok) throw new Error(`conversation create failed: ${response.status}`);
      const created = await response.json() as ConversationSummary;
      setConversationBusy(false);
      await selectConversation(created);
      window.setTimeout(() => { void loadConversations(); }, 250);
    } catch (error) {
      console.warn('Conversation creation failed:', error);
      setConversationBusy(false);
    }
  };

  const patchConversation = async (
    conversation: ConversationSummary,
    patch: Record<string, unknown>,
  ) => {
    const response = await fetch(
      `/api/conversations/${encodeURIComponent(conversation.id)}?session_id=${encodeURIComponent(sessionId)}&worldline=${encodeURIComponent(worldline)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      },
    );
    if (!response.ok) throw new Error(`conversation update failed: ${response.status}`);
    const updated = await response.json() as ConversationSummary;
    setConversations((current) => current.map((row) => (
      row.id === updated.id ? { ...row, ...updated } : row
    )));
    return updated;
  };

  const patchActiveConversationRuntime = async (patch: Record<string, unknown>) => {
    if (!activeConversation || conversationBusy || isThinking || isSynthesizing) return;
    setConversationBusy(true);
    try {
      const updated = await patchConversation(activeConversation, patch);
      if (!updated) return;
      setActiveConversationId(updated.id);
      if (updated.model_id) {
        setModel(updated.model_id);
        try { localStorage.setItem('amadeus_pc_model', updated.model_id); } catch {}
      }
      // Provider/model are authenticated and snapshotted when the socket opens.
      // Reusing a socket that previously failed authentication leaves it in the
      // server's unauthenticated state, so reload the durable conversation on a
      // fresh connection after either runtime setting changes.
      reconnect();
      const refreshedProviders = await loadProviders();
      const provider = refreshedProviders.find((row) => row.id === updated.provider_id);
      if (provider && !provider.configured) setIsSettingsOpen(true);
    } finally {
      setConversationBusy(false);
    }
  };

  const changeProvider = (providerId: string) => {
    void patchActiveConversationRuntime({ provider_id: providerId }).catch((error) => {
      console.warn('Conversation provider update failed:', error);
    });
  };

  const changeModel = (modelId: string) => {
    if (!modelId || modelId === activeConversation?.model_id) return;
    void patchActiveConversationRuntime({ model_id: modelId }).catch((error) => {
      console.warn('Conversation model update failed:', error);
    });
  };

  const renameConversation = (conversation: ConversationSummary) => {
    const title = window.prompt('输入新的会话标题', conversation.title)?.trim();
    if (!title || title === conversation.title) return;
    void patchConversation(conversation, { title }).catch((error) => {
      console.warn('Conversation rename failed:', error);
    });
  };

  const toggleConversationPin = (conversation: ConversationSummary) => {
    void patchConversation(conversation, { is_pinned: !conversation.is_pinned }).catch((error) => {
      console.warn('Conversation pin update failed:', error);
    });
  };

  const deleteConversation = async (conversation: ConversationSummary) => {
    if (!window.confirm(`删除会话“${conversation.title}”？长期记忆默认保留。`)) return;
    const response = await fetch(
      `/api/conversations/${encodeURIComponent(conversation.id)}?session_id=${encodeURIComponent(sessionId)}&worldline=${encodeURIComponent(worldline)}`,
      { method: 'DELETE' },
    );
    if (!response.ok) {
      console.warn(`Conversation deletion failed: ${response.status}`);
      return;
    }
    const rows = await loadConversations();
    if (conversation.id === activeConversationId) {
      const fallback = rows.find((row) => row.is_selected) || rows[0];
      if (fallback) await selectConversation(fallback, true);
    }
  };

  const forgetConversation = async (forgetLongTerm: boolean) => {
    if (!forgetTarget || conversationBusy) return;
    setConversationBusy(true);
    try {
      const response = await fetch(
        `/api/conversations/${encodeURIComponent(forgetTarget.id)}/forget?session_id=${encodeURIComponent(sessionId)}&worldline=${encodeURIComponent(worldline)}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ forget_long_term: forgetLongTerm }),
        },
      );
      if (!response.ok) throw new Error(`conversation forget failed: ${response.status}`);
      if (forgetTarget.id === activeConversationId) {
        audioPlayer.stopAll();
        switchConversation(forgetTarget.id, []);
      }
      setForgetTarget(null);
      await loadConversations();
    } catch (error) {
      console.warn('Conversation forget failed:', error);
    } finally {
      setConversationBusy(false);
    }
  };

  const handleStopVoice = () => {
    audioPlayer.stopAll();
    stopVoice();
  };

  const handleCancelTurn = () => {
    audioPlayer.stopAll();
    cancelTurn();
  };

  const handleSaveSettings = async (newSettings: {
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
    baseUrl: string;
    model: string;
    reasoningEffort: 'high' | 'xhigh';
  }) => {
    // 1. Sync React parent state if worldline changed
    if (newSettings.worldline !== worldline) {
      onWorldlineChange(newSettings.worldline);
    }
    
    // 2. Sync local states
    let providerConfigurationChanged = false;
    if (
      activeProvider?.base_url_configurable &&
      newSettings.baseUrl.trim() &&
      (
        newSettings.baseUrl.trim().replace(/\/+$/, '') !== activeProvider.base_url?.replace(/\/+$/, '') ||
        newSettings.model !== activeProvider.default_model
      )
    ) {
      const response = await fetch('/api/providers/custom/configuration', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          base_url: newSettings.baseUrl.trim(),
          default_model: newSettings.model.trim(),
        }),
      });
      if (!response.ok) {
        setAudioError('provider_config_save_failed');
        return;
      }
      providerConfigurationChanged = true;
      await loadProviders();
    }
    let credentialChanged = false;
    if (newSettings.apiKey.trim()) {
      const response = await fetch(`/api/providers/${encodeURIComponent(activeProviderId)}/credentials`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: newSettings.apiKey.trim() }),
      });
      if (!response.ok) {
        setAudioError('credential_save_failed');
        return;
      }
      credentialChanged = true;
      setCredentialChecked(true);
      try { localStorage.removeItem('amadeus_pc_api_key'); } catch {}
    }
    setApiKey('');
    setSystemPrompt(newSettings.systemPrompt);
    setEnableTts(newSettings.enableTts);
    setSovitsUrl(newSettings.sovitsUrl);
    setEnableBgm(newSettings.enableBgm);
    setBgmVolume(newSettings.bgmVolume);
    setEnableSfx(newSettings.enableSfx);
    setSfxVolume(newSettings.sfxVolume);
    setTemperature(newSettings.temperature);
    setReasoningEffort(newSettings.reasoningEffort);

    if (
      newSettings.worldline === worldline &&
      newSettings.model &&
      newSettings.model !== activeConversation?.model_id
    ) {
      await patchActiveConversationRuntime({ model_id: newSettings.model });
    }

    try {
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
    if (credentialChanged || providerConfigurationChanged) {
      await loadProviders();
      reconnect();
    } else {
      updateConfig({
        systemPrompt: newSettings.systemPrompt,
        worldline: newSettings.worldline,
        enableTts: newSettings.enableTts,
        sovitsUrl: newSettings.sovitsUrl,
        temperature: newSettings.temperature,
        reasoningEffort: newSettings.reasoningEffort,
      });
    }
  };

  const handleAudioInit = () => {
    audioPlayer.init();
  };

  return (
    <div
      className={`salieri-workstation-panel worldline-${worldline === 'beta' ? 'beta' : 'sg'}`}
      onMouseDown={handleAudioInit}
    >
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

      {/* Mechanical shell stays above the whole scene, never as a stretched background. */}
      <div className="workstation-panel-frame-overlay" aria-hidden="true" />

      {/* CRT vignette + chromatic aberration (z-index 50, above content, below modals) */}
      <CRTOverlay glitchState={glitchState} zIndex={50} />

      <div className="salieri-workstation-inner">
        {/* Top Header Controls */}
        <div className="workstation-header">
        <div className="left-brand" aria-label="Amadeus Salieri terminal">
          <span className="brand-index">AM-01</span>
          <div className="brand-lockup">
            <span className="brand-title">AMADEUS</span>
            <span className="brand-sub">SALIERI / NEURAL TERMINAL</span>
          </div>
        </div>
        <div className="header-telemetry" aria-label="World line telemetry">
          <HudBadge worldline={worldline} status={needsApiKey ? 'SETUP' : status} />
          <DivergenceMeter worldline={worldline} />
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
          <div className="sound-control-anchor">
            <button
              className={`ctrl-btn icon-btn ${isSoundOpen ? 'is-active' : ''}`}
              onClick={() => { audioService.playSFX('/audio/sfx/提示音1.ogg', sfxVolume); setIsSoundOpen((v) => !v); }}
              title="音量控制"
              aria-expanded={isSoundOpen}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07" />
              </svg>
            </button>
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
          </div>
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
          conversations={conversations}
          activeId={activeConversationId}
          searchQuery={conversationSearch}
          onSearchChange={setConversationSearch}
          onSelect={(conversation) => { void selectConversation(conversation); }}
          onNew={() => { void createConversation(); }}
          onRename={renameConversation}
          onTogglePin={toggleConversationPin}
          onDelete={(conversation) => { void deleteConversation(conversation); }}
          onForget={setForgetTarget}
          busy={conversationBusy}
        />

        {/* Center: Dialogue Terminal */}
        <div className="grid-terminal border-beam">
          <div className="terminal-chat-viewport">
            {visibleMessages.length === 0 ? (
              <div className="empty-chat-tip">
                <div className="amadeus-logo-glitched" />
                <p className={`terminal-welcome-msg ${emptyStateKind}`}>
                  {emptyStateMessage}
                </p>
                {needsApiKey && (
                  <button
                    type="button"
                    className="empty-chat-action"
                    onClick={() => setIsSettingsOpen(true)}
                  >
                    OPEN SYSTEM CONFIG
                  </button>
                )}
              </div>
            ) : (
              <div className="messages-flow">
                {visibleMessages.map((msg) => (
                  <div key={msg.id} className={`message-card ${msg.role}`}>
                    <div className="avatar-label">
                      {msg.role === 'user' ? 'USER ID: OKABE' : 'AMADEUS: KURISU'}
                    </div>
                    <div className="message-content-box">
                      {msg.role === 'assistant' ? (
                        <BilingualMessage
                          segments={msg.segments || []}
                          streaming={Boolean(msg.streaming)}
                          fallbackJa={msg.content}
                          fallbackZh={msg.translation}
                        />
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
                          frameRowOffsetY={worldline === 'steins_gate' ? 8 : 0}
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
                {(isThinking || isSynthesizing) && (
                  <div className="voice-control-capsule" role="group" aria-label="Current response controls">
                    <span className="voice-control-signal">β SIGNAL</span>
                    <button type="button" onClick={handleStopVoice} disabled={!isSynthesizing}>
                      STOP VOICE
                    </button>
                    <button type="button" onClick={handleCancelTurn}>
                      CANCEL TURN
                    </button>
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
            >
              <span>SEND</span>
            </button>
          </form>
          <div className="model-bottom-bar">
            <ProviderControls
              providers={providers}
              activeProviderId={activeProviderId}
              activeModelId={activeConversation?.model_id || model}
              models={availableModels}
              disabled={conversationBusy || isThinking || isSynthesizing}
              loadingModels={modelsLoading}
              onProviderChange={changeProvider}
              onModelChange={changeModel}
              onRefreshModels={() => { void loadProviderModels(activeProviderId, true); }}
              onConfigureCredential={() => setIsSettingsOpen(true)}
            />
            <span className="reasoning-label">推理深度</span>
            <div className="reasoning-toggle" role="group" aria-label="推理深度">
              {(['high', 'xhigh'] as const).map((effort) => (
                <button
                  type="button"
                  key={effort}
                  className={reasoningEffort === effort ? 'active' : ''}
                  onClick={() => {
                    setReasoningEffort(effort);
                    localStorage.setItem('amadeus_pc_reasoning_effort', effort);
                    updateConfig({ reasoningEffort: effort });
                  }}
                >
                  {effort === 'xhigh' ? 'XHIGH' : 'HIGH'}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Right: Avatar Viewer Panel */}
        <div className="grid-avatar-panel">
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
      <StatusBar worldline={worldline} connectionStatus={needsApiKey ? 'SETUP' : status} />
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
        providerDisplayName={activeProvider?.display_name || activeProviderId}
        providerBaseUrl={activeProvider?.base_url}
        baseUrlConfigurable={Boolean(activeProvider?.base_url_configurable)}
        model={model}
        modelOptions={availableModels}
        reasoningEffort={reasoningEffort}
        onSaveSettings={handleSaveSettings}
      />

      {forgetTarget && (
        <ForgetConversationModal
          title={forgetTarget.title}
          busy={conversationBusy}
          onClose={() => { if (!conversationBusy) setForgetTarget(null); }}
          onConfirm={(forgetLongTerm) => { void forgetConversation(forgetLongTerm); }}
        />
      )}

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
