import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  getOrCreateAmadeusSessionId,
  useAmadeusWS,
  type Message,
} from '../hooks/useAmadeusWS';
import { StatusBar } from './StatusBar';
import {
  CHAT_TEMPERATURE_DEFAULT,
  CHAT_TEMPERATURE_MAX,
  clampChatTemperature,
  loadStoredIdentityMode,
  loadStoredSelfName,
  normalizeIdentityMode,
  persistIdentityMode,
  persistSelfName,
  SettingsModal,
  type CredentialStatus,
  type IdentityMode,
  type WebSearchProviderId,
  type WebSearchProviderInfo,
  type WebSearchSettingsSnapshot,
} from './SettingsModal';
import { SoundMixerRail } from './SoundMixerRail';
import { HistorySidebar, type ConversationSummary } from './HistorySidebar';
import { TtsDebugPanel } from './TtsDebugPanel';
import AvatarViewer from './AvatarViewer';
import SpriteAnimator from './SpriteAnimator';
import { HudBadge } from './HudBadge';
import { DivergenceMeter } from './DivergenceMeter';
import { useAudioPlayer, audioPlayer } from '../hooks/useAudioPlayer';
import { audioService } from '../services/AudioService';
import { CRTOverlay } from './CRTOverlay';
import { BilingualMessage } from './BilingualMessage';
import { ForgetConversationModal } from './ForgetConversationModal';
import { MemorySpace } from '../memory-space/MemorySpace';
import '../memory-space/MemorySpace.css';
import {
  ProviderControls,
  resolveThinkingValue,
  type ModelCatalogEntry,
  type ModelDiscoveryStatus,
  type ProviderMetadata,
} from './ProviderControls';

interface WorkstationProps {
  worldline: 'steins_gate' | 'beta';
  onWorldlineChange: (wl: 'steins_gate' | 'beta') => void;
  onLogout: () => void;
}

/**
 * User message language tag: only when content includes real Japanese kana.
 * Hiragana / Katakana (incl. halfwidth). Han-only CJK (e.g. Chinese) must NOT
 * force lang="ja" — document root is zh-CN.
 */
export function contentHasJapaneseKana(text: string): boolean {
  return /[\u3040-\u309F\u30A0-\u30FF\uFF66-\uFF9D]/.test(text);
}

/** Q14: map known tool ids to Title Case labels; never render raw unknown tool names. */
function activityOperationLabel(activeTool: string | null | undefined): string | null {
  if (!activeTool) return null;
  if (activeTool === 'web_search') return 'Web Search';
  return null;
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
  const [isMemoryOpen, setIsMemoryOpen] = useState(false);
  const [inputText, setInputText] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [webSearchApiKey, setWebSearchApiKey] = useState('');
  const [webSearchConfigured, setWebSearchConfigured] = useState(false);
  const [webSearchCredentialStatus, setWebSearchCredentialStatus] = useState<CredentialStatus>('checking');
  const [webSearchActiveProvider, setWebSearchActiveProvider] = useState<WebSearchProviderId>('tavily');
  const [webSearchProviders, setWebSearchProviders] = useState<Partial<Record<WebSearchProviderId, WebSearchProviderInfo>>>({});
  const [webSearchFallbackNote, setWebSearchFallbackNote] = useState(
    '请至少配置一个搜索提供商并设为当前使用。',
  );
  const [credentialChecked, setCredentialChecked] = useState(false);
  const [credentialStatus, setCredentialStatus] = useState<CredentialStatus>('checking');
  const [providers, setProviders] = useState<ProviderMetadata[]>([]);
  const [availableModels, setAvailableModels] = useState<ModelCatalogEntry[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsDiscoveryStatus, setModelsDiscoveryStatus] = useState<ModelDiscoveryStatus>('idle');
  const providerModelsRequestEpochRef = useRef(0);
  const [sessionId] = useState(getOrCreateAmadeusSessionId);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [conversationSearch, setConversationSearch] = useState('');
  const [conversationBusy, setConversationBusy] = useState(false);
  const [forgetTarget, setForgetTarget] = useState<ConversationSummary | null>(null);
  const transcriptViewportRef = useRef<HTMLDivElement>(null);
  const previousWorldlineRef = useRef(worldline);
  const committedWorldlineRef = useRef(worldline);
  const pendingWorldlineRollbackRef = useRef<'steins_gate' | 'beta' | null>(null);
  const suppressedWorldlineEffectRef = useRef<'steins_gate' | 'beta' | null>(null);
  const latestWorldlineRef = useRef(worldline);
  const conversationRequestRef = useRef(0);
  const pendingConversationModelRef = useRef<string | null>(null);

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
  const [voiceVolume, setVoiceVolume] = useState(() => {
    try {
      const v = localStorage.getItem('amadeus_pc_voice_volume');
      return v ? Math.max(0, Math.min(1, parseFloat(v))) : 1.0;
    } catch { return 1.0; }
  });
  const [temperatureMigrationNotice, setTemperatureMigrationNotice] = useState(() => {
    try {
      return sessionStorage.getItem('amadeus_pc_temperature_migrated_notice') === '1';
    } catch {
      return false;
    }
  });
  const [temperature, setTemperature] = useState(() => {
    try {
      const raw = localStorage.getItem('amadeus_pc_temperature');
      if (raw == null || raw === '') return CHAT_TEMPERATURE_DEFAULT;
      const parsed = parseFloat(raw);
      if (!Number.isFinite(parsed)) return CHAT_TEMPERATURE_DEFAULT;
      if (parsed > CHAT_TEMPERATURE_MAX) {
        const clamped = CHAT_TEMPERATURE_MAX;
        try {
          localStorage.setItem('amadeus_pc_temperature', String(clamped));
          sessionStorage.setItem('amadeus_pc_temperature_migrated_notice', '1');
        } catch { /* ignore quota / private mode */ }
        return clamped;
      }
      return clampChatTemperature(parsed);
    } catch {
      return CHAT_TEMPERATURE_DEFAULT;
    }
  });
  /** Default for *new* conversations only (Q20-A). Active conversation mode is immutable (Q21). */
  const [defaultIdentityMode, setDefaultIdentityMode] = useState<IdentityMode>(() =>
    loadStoredIdentityMode(),
  );
  /** Slice B: app-level self-mode call name (validated in SettingsModal; empty = unset). */
  const [selfName, setSelfName] = useState<string>(() => loadStoredSelfName());
  const [model, setModel] = useState<string>(() =>
    localStorage.getItem('amadeus_pc_model') || 'deepseek-flash');
  const [draftProviderId, setDraftProviderId] = useState<string>(() => {
    try { return localStorage.getItem('amadeus_pc_provider') || 'deepseek'; } catch { return 'deepseek'; }
  });
  const [reasoningEffort, setReasoningEffort] = useState<string | null>(() =>
    localStorage.getItem('amadeus_pc_reasoning_effort'));
  const [audioError, setAudioError] = useState<string | null>(null);
  const [sessionError, setSessionError] = useState<{
    kind: 'session' | 'worldline';
    code: string;
  } | null>(null);
  const [isSoundOpen, setIsSoundOpen] = useState(false);
  const soundTriggerRef = useRef<HTMLButtonElement>(null);
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

  // Temperature init may write the migration flag after the notice state constructor ran.
  useEffect(() => {
    try {
      if (sessionStorage.getItem('amadeus_pc_temperature_migrated_notice') === '1') {
        setTemperatureMigrationNotice(true);
      }
    } catch { /* ignore */ }
  }, []);

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

  // Auto-clear audio / session surface errors after 5s (independent channels).
  useEffect(() => {
    if (audioError) {
      const timer = window.setTimeout(() => setAudioError(null), 5000);
      return () => window.clearTimeout(timer);
    }
  }, [audioError]);

  useEffect(() => {
    if (sessionError) {
      const timer = window.setTimeout(() => setSessionError(null), 5000);
      return () => window.clearTimeout(timer);
    }
  }, [sessionError]);

  latestWorldlineRef.current = worldline;

  const loadConversations = useCallback(async () => {
    const requestId = ++conversationRequestRef.current;
    const requestedWorldline = worldline;
    const response = await fetch(
      `/api/conversations?session_id=${encodeURIComponent(sessionId)}&worldline=${encodeURIComponent(requestedWorldline)}`
    );
    if (!response.ok) throw new Error(`conversation query failed: ${response.status}`);
    const rows = await response.json() as ConversationSummary[];
    if (
      requestId !== conversationRequestRef.current
      || requestedWorldline !== latestWorldlineRef.current
    ) return [];
    setConversations(rows);
    return rows;
  }, [sessionId, worldline]);

  const providersRequestEpochRef = useRef(0);
  const loadProviders = useCallback(async () => {
    const requestEpoch = ++providersRequestEpochRef.current;
    const response = await fetch('/api/providers');
    if (!response.ok) throw new Error(`provider query failed: ${response.status}`);
    const rows = await response.json() as ProviderMetadata[];
    if (providersRequestEpochRef.current !== requestEpoch) return rows;
    setProviders(rows);
    return rows;
  }, []);

  useEffect(() => {
    if (
      providers.length === 0
      || providers.some((provider) => provider.id === draftProviderId)
    ) return;
    const fallback = (
      providers.find((provider) => provider.id === 'deepseek')
      || providers[0]
    );
    if (!fallback) return;
    setDraftProviderId(fallback.id);
    setModel(fallback.default_model);
    try {
      localStorage.setItem('amadeus_pc_provider', fallback.id);
      localStorage.setItem('amadeus_pc_model', fallback.default_model);
    } catch {}
  }, [draftProviderId, providers]);

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
    stopVoice,
    cancelTurn,
    sendDebugTts,
    updateConfig,
    reconnect,
    switchConversation,
    switchWorldline,
  } = useAmadeusWS({
    sessionId,
    conversationId: activeConversationId,
    systemPrompt,
    temperature,
    worldline,
    identityMode: defaultIdentityMode,
    selfName,
    enableTts,
    sovitsUrl,
    providerId: (
      activeConversationId
        ? conversations.find((row) => row.id === activeConversationId)?.provider_id
        : null
    ) || draftProviderId,
    model,
    reasoningEffort,
    onAudioChunk: (_sentenceId, base64Data) => {
      audioPlayer.playBase64(base64Data);
    },
    onAudioDegraded: (reason: string) => {
      setAudioError(reason);
    },
    onTurnStarted: (conversationId) => {
      audioPlayer.stopAll();
      if (conversationId) {
        setActiveConversationId(conversationId);
        setConversations((current) => current.map((row) => ({
          ...row,
          is_selected: row.id === conversationId,
        })));
        pendingConversationModelRef.current = null;
        window.setTimeout(() => { void loadConversations(); }, 250);
      }
    },
    onConversationSwitched: ({ conversationId }) => {
      setConversationBusy(false);
      setActiveConversationId(conversationId);
      setConversations((current) => current.map((row) => ({
        ...row,
        is_selected: row.id === conversationId,
      })));
      const pendingModel = pendingConversationModelRef.current;
      if (pendingModel) {
        setModel(pendingModel);
        try { localStorage.setItem('amadeus_pc_model', pendingModel); } catch {}
      }
      pendingConversationModelRef.current = null;
    },
    onConversationSwitchError: ({ code }) => {
      pendingConversationModelRef.current = null;
      setConversationBusy(false);
      // Do not use the TTS toast channel for session lifecycle failures.
      setSessionError({ kind: 'session', code });
    },
    onWorldlineSwitched: ({ worldline: switchedWorldline }) => {
      committedWorldlineRef.current = switchedWorldline;
      pendingWorldlineRollbackRef.current = null;
      setConversationBusy(false);
      setActiveConversationId(null);
      pendingConversationModelRef.current = null;
      setConversations((current) => current.map((row) => ({
        ...row,
        is_selected: false,
      })));
    },
    onWorldlineSwitchError: ({ code }) => {
      const rollback = pendingWorldlineRollbackRef.current
        || committedWorldlineRef.current;
      pendingWorldlineRollbackRef.current = null;
      if (worldline !== rollback) {
        suppressedWorldlineEffectRef.current = rollback;
        onWorldlineChange(rollback);
      }
      setConversationBusy(false);
      // Keep audioError untouched so a concurrent TTS failure is not overwritten.
      setSessionError({ kind: 'worldline', code });
    },
    onTurnCompleted: () => {
      window.setTimeout(() => { void loadConversations(); }, 250);
    },
    onConfigApplied: ({ reasoningEffort: appliedEffort }) => {
      if (appliedEffort === undefined) return;
      setReasoningEffort(appliedEffort);
      // Resolve provider/model from the same sources as the hook params (these
      // locals are declared after this call site).
      const scopedProviderId = (
        activeConversationId
          ? conversations.find((row) => row.id === activeConversationId)?.provider_id
          : null
      ) || draftProviderId;
      const scopedModelId = (
        activeConversationId
          ? conversations.find((row) => row.id === activeConversationId)?.model_id
          : null
      ) || model;
      try {
        if (appliedEffort) {
          localStorage.setItem('amadeus_pc_reasoning_effort', appliedEffort);
          localStorage.setItem(
            `amadeus_pc_reasoning_effort_${scopedProviderId}_${scopedModelId}`,
            appliedEffort,
          );
        } else {
          localStorage.removeItem('amadeus_pc_reasoning_effort');
          localStorage.removeItem(
            `amadeus_pc_reasoning_effort_${scopedProviderId}_${scopedModelId}`,
          );
        }
      } catch {
        // LocalStorage may be blocked; session state still tracks the applied value.
      }
    },
  });

  useEffect(() => {
    setActiveConversationId(null);
    void loadConversations().catch((error) => {
      console.warn('Conversation synchronization failed:', error);
    });
  }, [loadConversations]);

  useEffect(() => {
    if (previousWorldlineRef.current === worldline) return;
    if (suppressedWorldlineEffectRef.current === worldline) {
      suppressedWorldlineEffectRef.current = null;
      previousWorldlineRef.current = worldline;
      return;
    }
    const rollbackWorldline = committedWorldlineRef.current;
    previousWorldlineRef.current = worldline;
    pendingWorldlineRollbackRef.current = rollbackWorldline;
    audioPlayer.stopAll();
    setConversationBusy(true);
    setActiveConversationId(null);
    pendingConversationModelRef.current = null;
    if (!switchWorldline(worldline) && pendingWorldlineRollbackRef.current !== null) {
      pendingWorldlineRollbackRef.current = null;
      suppressedWorldlineEffectRef.current = rollbackWorldline;
      onWorldlineChange(rollbackWorldline);
      setConversationBusy(false);
    }
  }, [onWorldlineChange, switchWorldline, worldline]);

  useEffect(() => {
    let cancelled = false;
    const synchronizeCredential = async () => {
      setCredentialStatus('checking');
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
        if (!cancelled) setCredentialStatus('error');
        console.warn('Credential synchronization failed:', error);
      } finally {
        if (!cancelled) setCredentialChecked(true);
      }
    };
    void synchronizeCredential();
    return () => { cancelled = true; };
  }, [loadProviders, reconnect]);

  const applyWebSearchSettings = useCallback((settings: WebSearchSettingsSnapshot) => {
    const legacyProvider = (settings as WebSearchSettingsSnapshot & { provider?: WebSearchProviderId }).provider;
    const active = (settings.active_provider || legacyProvider || 'tavily') as WebSearchProviderId;
    const providers = settings.providers || {};
    setWebSearchActiveProvider(active);
    setWebSearchProviders(providers);
    if (settings.fallback_note) setWebSearchFallbackNote(settings.fallback_note);
    const activeConfigured = Boolean(providers[active]?.configured);
    const anyConfigured = Boolean(
      settings.configured
      || activeConfigured
      || Object.values(providers).some((row) => row?.configured),
    );
    setWebSearchConfigured(activeConfigured || anyConfigured);
    setWebSearchCredentialStatus(activeConfigured ? 'configured' : 'not_configured');
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadWebSearchSettings = async () => {
      setWebSearchCredentialStatus('checking');
      try {
        const response = await fetch('/api/search/settings');
        if (!response.ok) throw new Error(`web search settings query failed: ${response.status}`);
        const settings = await response.json() as WebSearchSettingsSnapshot & { provider?: WebSearchProviderId };
        if (!cancelled) applyWebSearchSettings(settings);
      } catch (error) {
        if (!cancelled) setWebSearchCredentialStatus('error');
        console.warn('Web Search credential synchronization failed:', error);
      }
    };
    void loadWebSearchSettings();
    return () => { cancelled = true; };
  }, [applyWebSearchSettings]);

  const activeConversation = conversations.find((row) => row.id === activeConversationId);
  const activeProviderId = activeConversation?.provider_id || draftProviderId;
  const activeProvider = providers.find((provider) => provider.id === activeProviderId);
  const activeModelId = activeConversation?.model_id || model;
  /** Q21: HUD label follows the active conversation's immutable mode; drafts inherit the
   *  settings default (the mode a materialized draft receives). Unknown/missing → okabe. */
  const activeIdentityMode = normalizeIdentityMode(
    activeConversation ? activeConversation.identity_mode : defaultIdentityMode,
  );
  /** S4-DS speaker contract: OKABE or configured self name only — never USER ID / user. */
  const userHudLabel = activeIdentityMode === 'self'
    ? (selfName.trim() ? selfName.trim() : '——')
    : 'OKABE';


  const credentialConfigured = Boolean(
    activeProvider && (!activeProvider.credential_required || activeProvider.configured)
  );

  useEffect(() => {
    if (!credentialChecked || !activeProvider || credentialStatus === 'saving') return;
    setCredentialStatus(
      activeProvider.credential_required && !activeProvider.configured
        ? 'not_configured'
        : 'configured',
    );
  }, [activeProvider, credentialChecked, credentialStatus]);
  const activeModelCapability = availableModels.find(
    (row) => row.id === activeModelId,
  ) || (
    activeProvider?.default_model_capability?.id === activeModelId
      ? activeProvider.default_model_capability
      : null
  );

  useEffect(() => {
    const key = `amadeus_pc_reasoning_effort_${activeProviderId}_${activeModelId}`;
    let scoped: string | null = null;
    try {
      scoped = localStorage.getItem(key);
    } catch {
      // Fall through to the model-catalog default.
    }
    setReasoningEffort(resolveThinkingValue(
      activeModelCapability?.thinking_control,
      scoped,
      reasoningEffort,
    ));
  }, [
    activeModelCapability,
    activeModelId,
    activeProviderId,
  ]);

  const loadProviderModels = useCallback(async (
    providerId: string,
    refresh = false,
  ) => {
    const requestEpoch = ++providerModelsRequestEpochRef.current;
    const provider = providers.find((row) => row.id === providerId);
    const fallback: ModelCatalogEntry[] = [];
    if (provider?.catalog?.length) {
      fallback.push(...provider.catalog);
    } else if (provider?.default_model_capability) {
      fallback.push(provider.default_model_capability);
    }
    const selectedModel = activeConversation?.model_id || model;
    if (selectedModel && !fallback.some((row) => row.id === selectedModel)) {
      const userCustom = providerId === 'custom' || providerId.startsWith('custom:');
      const providerEnabled = provider?.enabled !== false;
      fallback.push({
        id: selectedModel,
        display_name: selectedModel,
        lifecycle: userCustom && providerEnabled ? 'unverified' : 'unavailable',
        // Disabled custom connections must never appear selectable.
        callable: Boolean(userCustom && providerEnabled),
        source: 'dynamic',
        thinking_control: null,
      });
    }
    if (
      !provider?.model_discovery ||
      (provider.credential_required && !provider.configured)
    ) {
      if (providerModelsRequestEpochRef.current === requestEpoch) {
        setAvailableModels(fallback);
        setModelsLoading(false);
        setModelsDiscoveryStatus(
          provider?.credential_required && !provider.configured
            ? 'credential_missing'
            : 'idle',
        );
      }
      return;
    }
    setModelsLoading(true);
    try {
      const response = await fetch(
        `/api/providers/${encodeURIComponent(providerId)}/models?refresh=${refresh ? 'true' : 'false'}`
      );
      if (providerModelsRequestEpochRef.current !== requestEpoch) return;
      if (response.status === 409) {
        setAvailableModels(fallback);
        setModelsDiscoveryStatus('credential_missing');
        return;
      }
      if (!response.ok) throw new Error(`model query failed: ${response.status}`);
      const payload = await response.json() as {
        catalog?: ModelCatalogEntry[];
        discovery_status?: ModelDiscoveryStatus;
        id_only?: boolean;
      };
      const catalog = payload.catalog?.length ? [...payload.catalog] : fallback;
      if (
        payload.catalog
        && selectedModel
        && !catalog.some((row) => row.id === selectedModel)
      ) {
        catalog.push({
          id: selectedModel,
          request_id: selectedModel,
          display_name: selectedModel,
          lifecycle: 'unavailable',
          callable: false,
          source: 'dynamic',
          thinking_control: null,
        });
      }
      setAvailableModels(catalog);
      setModelsDiscoveryStatus(
        payload.id_only ? 'id_only' : (payload.discovery_status || 'live'),
      );
    } catch (error) {
      if (providerModelsRequestEpochRef.current === requestEpoch) {
        setAvailableModels((current) => (current.length ? current : fallback));
        setModelsDiscoveryStatus('failed');
        console.warn('Provider model discovery failed:', error);
      }
    } finally {
      if (providerModelsRequestEpochRef.current === requestEpoch) {
        setModelsLoading(false);
      }
    }
  }, [activeConversation?.model_id, model, providers]);

  useEffect(() => {
    void loadProviderModels(activeProviderId);
  }, [activeProviderId, loadProviderModels]);

  const { currentVolume, isPlaying } = useAudioPlayer();

  useEffect(() => {
    audioPlayer.setVoiceVolume?.(voiceVolume);
  }, [voiceVolume]);

  const handleVoiceVolumeChange = useCallback((value: number) => {
    const next = Math.max(0, Math.min(1, value));
    setVoiceVolume(next);
    try { localStorage.setItem('amadeus_pc_voice_volume', String(next)); } catch {}
  }, []);

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

  // Q14 single activity surface: known tools override Thinking; unknown tools fall back.
  // Raw activeNotice strings are never used as visible labels.
  const operationLabel = activityOperationLabel(activeTool) ?? (isThinking ? 'Thinking' : null);
  const activityBusy = Boolean(operationLabel || activeNotice);

  const [glitchState, setGlitchState] = useState<'none' | 'light' | 'heavy'>('none');

  const triggerGlitch = useCallback((intensity: 'light' | 'heavy') => {
    setGlitchState(intensity);
    const duration = intensity === 'heavy' ? 250 : 120;
    window.setTimeout(() => {
      setGlitchState('none');
    }, duration);
  }, []);

  const NEAR_BOTTOM_THRESHOLD_PX = 72;
  const stickToBottomRef = useRef(true);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);

  const prefersReducedMotion = useCallback(() => (
    typeof window !== 'undefined'
    && Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)
  ), []);

  const measureTranscriptScroll = useCallback(() => {
    const viewport = transcriptViewportRef.current;
    if (!viewport) return { nearBottom: true, overflow: false };
    const distance = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
    return {
      nearBottom: distance <= NEAR_BOTTOM_THRESHOLD_PX,
      overflow: viewport.scrollHeight > viewport.clientHeight + 1,
    };
  }, []);

  const refreshJumpToLatestVisibility = useCallback((messageCount: number) => {
    if (messageCount <= 0) {
      setShowJumpToLatest(false);
      return;
    }
    const { nearBottom, overflow } = measureTranscriptScroll();
    setShowJumpToLatest(overflow && !nearBottom);
  }, [measureTranscriptScroll]);

  const scrollTranscriptToLatest = useCallback((options?: {
    force?: boolean;
    smooth?: boolean;
  }) => {
    const viewport = transcriptViewportRef.current;
    if (!viewport) return;
    // force:true (send / thinking / jump) re-enables auto-follow.
    // force:false only scrolls when the caller already knows we are sticky.
    if (options?.force ?? true) {
      stickToBottomRef.current = true;
    }
    const useSmooth = Boolean(options?.smooth) && !prefersReducedMotion();
    const apply = () => {
      const top = viewport.scrollHeight;
      if (useSmooth && typeof viewport.scrollTo === 'function') {
        viewport.scrollTo({ top, behavior: 'smooth' });
      } else {
        viewport.scrollTop = top;
      }
    };
    apply();
    window.requestAnimationFrame?.(apply);
    setShowJumpToLatest(false);
  }, [prefersReducedMotion]);

  const handleTranscriptScroll = useCallback(() => {
    const { nearBottom, overflow } = measureTranscriptScroll();
    stickToBottomRef.current = nearBottom;
    setShowJumpToLatest(overflow && !nearBottom && messages.length > 0);
  }, [measureTranscriptScroll, messages.length]);

  // Conditional auto-follow: only when the user remains near the bottom.
  useEffect(() => {
    if (!stickToBottomRef.current) {
      refreshJumpToLatestVisibility(messages.length);
      return;
    }
    scrollTranscriptToLatest({ force: false, smooth: false });
  }, [messages, isThinking, isSynthesizing, scrollTranscriptToLatest, refreshJumpToLatestVisibility]);

  // Sending / thinking still force-jumps to the latest area (approved send behavior).
  useEffect(() => {
    if (isThinking) {
      scrollTranscriptToLatest({ force: true, smooth: false });
    }
  }, [isThinking, scrollTranscriptToLatest]);

  const handleJumpToLatest = useCallback(() => {
    scrollTranscriptToLatest({ force: true, smooth: true });
  }, [scrollTranscriptToLatest]);

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

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputText.trim()) return;
    // Turn text still gates send via isThinking/isSynthesizing. When only leftover
    // voice is playing, stop the existing playback path then start the new Turn.
    if (isPlaying) {
      audioPlayer.stopAll();
      stopVoice();
    }
    // Draft materialize (no conversation_id) always stamps okabe on the backend.
    // When the settings default is self, pre-create via API so mode is correct.
    // Okabe default keeps the existing draft path (zero regression for most sessions).
    if (!activeConversationId && normalizeIdentityMode(defaultIdentityMode) === 'self') {
      const created = await createConversation('self');
      if (!created) return;
    }
    audioService.playSFX('/audio/sfx/连接成功，进入页面1.ogg', sfxVolume);
    sendChat(inputText.trim());
    scrollTranscriptToLatest();
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
      pendingConversationModelRef.current = conversation.model_id || null;
      if (!switchConversation(conversation.id, history)) {
        pendingConversationModelRef.current = null;
        setConversationBusy(false);
      }
    } catch (error) {
      console.warn('Conversation switch failed:', error);
      setConversationBusy(false);
    }
  };

  const createConversation = async (
    identityMode: IdentityMode = defaultIdentityMode,
  ): Promise<ConversationSummary | null> => {
    if (conversationBusy) return null;
    setConversationBusy(true);
    try {
      const active = conversations.find((row) => row.id === activeConversationId);
      const mode = normalizeIdentityMode(identityMode);
      const response = await fetch('/api/conversations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          worldline,
          provider_id: active?.provider_id || draftProviderId,
          model_id: active?.model_id || model,
          // Never infer from nickname/session — only settings default or explicit UI.
          identity_mode: mode,
        }),
      });
      if (!response.ok) throw new Error(`conversation create failed: ${response.status}`);
      const created = await response.json() as ConversationSummary;
      setConversationBusy(false);
      await selectConversation(created);
      window.setTimeout(() => { void loadConversations(); }, 250);
      return created;
    } catch (error) {
      console.warn('Conversation creation failed:', error);
      setConversationBusy(false);
      return null;
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
    if (!activeConversation) {
      if (!providerId || providerId === draftProviderId) return;
      const provider = providers.find((row) => row.id === providerId);
      const nextModel = provider?.default_model || model;
      setDraftProviderId(providerId);
      setModel(nextModel);
      try {
        localStorage.setItem('amadeus_pc_provider', providerId);
        localStorage.setItem('amadeus_pc_model', nextModel);
      } catch {}
      window.setTimeout(reconnect, 0);
      if (provider?.credential_required && !provider.configured) {
        setIsSettingsOpen(true);
      }
      return;
    }
    void patchActiveConversationRuntime({ provider_id: providerId }).catch((error) => {
      console.warn('Conversation provider update failed:', error);
    });
  };

  const changeModel = (modelId: string) => {
    if (!modelId || modelId === activeConversation?.model_id) return;
    if (!activeConversation) {
      if (modelId === model) return;
      setModel(modelId);
      try { localStorage.setItem('amadeus_pc_model', modelId); } catch {}
      window.setTimeout(reconnect, 0);
      return;
    }
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
    let waitingForSwitchAck = false;
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
        waitingForSwitchAck = switchConversation(forgetTarget.id, []);
      }
      setForgetTarget(null);
      await loadConversations();
    } catch (error) {
      console.warn('Conversation forget failed:', error);
    } finally {
      if (!waitingForSwitchAck) setConversationBusy(false);
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
    webSearchApiKey: string;
    webSearchProvider: WebSearchProviderId;
    systemPrompt: string;
    enableTts: boolean;
    sovitsUrl: string;
    enableBgm: boolean;
    bgmVolume: number;
    enableSfx: boolean;
    sfxVolume: number;
    temperature: number;
    identityMode: IdentityMode;
    selfName: string;
    baseUrl: string;
    model: string;
    reasoningEffort: string | null;
  }) => {
    setCredentialStatus('saving');
    try {
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
      const endpoint = activeProviderId === 'custom'
        ? '/api/providers/custom/configuration'
        : `/api/providers/custom-profiles/${encodeURIComponent(activeProviderId)}`;
      const response = await fetch(endpoint, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          base_url: newSettings.baseUrl.trim(),
          default_model: newSettings.model.trim(),
          display_name: activeProvider.display_name,
        }),
      });
      if (!response.ok) {
        setAudioError('provider_config_save_failed');
        setCredentialStatus('error');
        throw new Error('provider_config_save_failed');
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
        setCredentialStatus('error');
        throw new Error('credential_save_failed');
      }
      credentialChanged = true;
      setCredentialChecked(true);
      try { localStorage.removeItem('amadeus_pc_api_key'); } catch {}
    }
    if (newSettings.webSearchApiKey.trim()) {
      const response = await fetch('/api/search/credential', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: newSettings.webSearchProvider,
          api_key: newSettings.webSearchApiKey.trim(),
        }),
      });
      if (!response.ok) {
        setWebSearchCredentialStatus('error');
        throw new Error('web_search_credential_save_failed');
      }
      const payload = await response.json() as WebSearchSettingsSnapshot & {
        provider?: WebSearchProviderId;
        configured?: boolean;
      };
      applyWebSearchSettings({
        active_provider: (payload.active_provider || newSettings.webSearchProvider) as WebSearchProviderId,
        providers: payload.providers || webSearchProviders,
        configured: payload.configured,
        fallback_note: webSearchFallbackNote,
      });
      setWebSearchActiveProvider(newSettings.webSearchProvider);
    } else if (newSettings.webSearchProvider !== webSearchActiveProvider) {
      setWebSearchActiveProvider(newSettings.webSearchProvider);
    }
    setWebSearchApiKey('');
    setApiKey('');
    setSystemPrompt(newSettings.systemPrompt);
    setEnableTts(newSettings.enableTts);
    setSovitsUrl(newSettings.sovitsUrl);
    setEnableBgm(newSettings.enableBgm);
    setBgmVolume(newSettings.bgmVolume);
    setEnableSfx(newSettings.enableSfx);
    setSfxVolume(newSettings.sfxVolume);
    const safeTemperature = clampChatTemperature(newSettings.temperature);
    setTemperature(safeTemperature);
    const nextIdentityMode = normalizeIdentityMode(newSettings.identityMode);
    setDefaultIdentityMode(nextIdentityMode);
    // Slice B: already validated by SettingsModal (reserved/overlong never arrive here).
    setSelfName(newSettings.selfName);
    setReasoningEffort(newSettings.reasoningEffort);

    if (
      newSettings.worldline === worldline &&
      newSettings.model &&
      newSettings.model !== activeConversation?.model_id
    ) {
      if (activeConversation) {
        await patchActiveConversationRuntime({ model_id: newSettings.model });
      } else {
        setModel(newSettings.model);
      }
    }

    try {
      localStorage.setItem('amadeus_pc_enable_bgm', String(newSettings.enableBgm));
      localStorage.setItem('amadeus_pc_bgm_volume', String(newSettings.bgmVolume));
      localStorage.setItem('amadeus_pc_enable_sfx', String(newSettings.enableSfx));
      localStorage.setItem('amadeus_pc_sfx_volume', String(newSettings.sfxVolume));
      localStorage.setItem('amadeus_pc_temperature', String(safeTemperature));
      persistIdentityMode(nextIdentityMode);
      persistSelfName(newSettings.selfName);
      localStorage.setItem('amadeus_pc_model', newSettings.model);
      if (newSettings.reasoningEffort) {
        localStorage.setItem('amadeus_pc_reasoning_effort', newSettings.reasoningEffort);
      } else {
        localStorage.removeItem('amadeus_pc_reasoning_effort');
      }
    } catch (e) {
      console.warn('LocalStorage save failed:', e);
    }

    // 3. Send config_update WS frame to backend
    if (credentialChanged || providerConfigurationChanged) {
      await loadProviders();
      reconnect();
    } else {
      const configPatch: Parameters<typeof updateConfig>[0] = {
        systemPrompt: newSettings.systemPrompt,
        enableTts: newSettings.enableTts,
        sovitsUrl: newSettings.sovitsUrl,
        temperature: safeTemperature,
        reasoningEffort: newSettings.reasoningEffort,
        identityMode: nextIdentityMode,
        selfName: newSettings.selfName,
      };
      if (newSettings.worldline === worldline) {
        configPatch.worldline = newSettings.worldline;
      }
      updateConfig(configPatch);
    }
    setCredentialStatus(newSettings.apiKey.trim() || credentialConfigured ? 'configured' : 'not_configured');
    } catch (error) {
      setCredentialStatus('error');
      throw error;
    }
  };

  const handleSelectWebSearchProvider = async (provider: WebSearchProviderId) => {
    setWebSearchCredentialStatus('saving');
    const response = await fetch('/api/search/settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active_provider: provider }),
    });
    if (!response.ok) {
      setWebSearchCredentialStatus('error');
      throw new Error('web_search_provider_switch_failed');
    }
    const settings = await response.json() as WebSearchSettingsSnapshot;
    applyWebSearchSettings(settings);
  };

  const handleClearWebSearchCredential = async (provider: WebSearchProviderId) => {
    setWebSearchCredentialStatus('saving');
    const response = await fetch(
      `/api/search/credential?provider=${encodeURIComponent(provider)}`,
      { method: 'DELETE' },
    );
    if (!response.ok) {
      setWebSearchCredentialStatus('error');
      throw new Error('web_search_credential_clear_failed');
    }
    const payload = await response.json() as WebSearchSettingsSnapshot & {
      provider?: WebSearchProviderId;
    };
    setWebSearchApiKey('');
    applyWebSearchSettings({
      active_provider: (payload.active_provider || webSearchActiveProvider) as WebSearchProviderId,
      providers: payload.providers || {},
      fallback_note: webSearchFallbackNote,
    });
  };

  const handleAudioInit = () => {
    audioPlayer.init();
  };

  return (
    <div
      className={`salieri-workstation-panel worldline-${worldline === 'beta' ? 'beta' : 'sg'}${isMemoryOpen ? ' memory-space-open' : ''}`}
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

      {/* Restrained grain only — no full-screen circuit/constellation pattern. */}
      <div className="workstation-bg-noise" />

      {/* Mechanical shell stays above the whole scene, never as a stretched background. */}
      <div className="workstation-panel-frame-overlay" aria-hidden="true" />

      {/* CRT vignette + chromatic aberration (z-index 50, above content, below modals) */}
      <CRTOverlay glitchState={glitchState} zIndex={50} />

      <div className="salieri-workstation-inner">
        {/* Top Header Controls — brand | audio zone | telemetry | utilities */}
        <div className="workstation-header">
        <div className="left-brand" aria-label="AMADEUS">
          <span className="brand-marker" aria-hidden="true" />
          <div className="brand-lockup">
            <span className="brand-title">AMADEUS</span>
          </div>
          <button
            type="button"
            className="memory-aperture-btn"
            aria-label="MEMORY"
            title="MEMORY"
            onClick={() => setIsMemoryOpen(true)}
          >
            MEMORY
          </button>
        </div>
        <div
          className={`header-audio-zone${isSoundOpen ? ' is-open' : ''}`}
          data-header-audio-zone="true"
        >
          <SoundMixerRail
            isOpen={isSoundOpen}
            onClose={() => setIsSoundOpen(false)}
            panelId="sound-mixer-rail"
            triggerRef={soundTriggerRef}
            enableBgm={enableBgm}
            bgmVolume={bgmVolume}
            enableSfx={enableSfx}
            sfxVolume={sfxVolume}
            voiceVolume={voiceVolume}
            onEnableBgmChange={setEnableBgm}
            onBgmVolumeChange={setBgmVolume}
            onEnableSfxChange={setEnableSfx}
            onSfxVolumeChange={setSfxVolume}
            onVoiceVolumeChange={handleVoiceVolumeChange}
          />
        </div>
        <div className="header-telemetry" aria-label="World line telemetry">
          <HudBadge worldline={worldline} status={needsApiKey ? 'SETUP' : status} />
          <DivergenceMeter worldline={worldline} />
        </div>
        <div className="right-controls">
          <div className="workstation-utilities" data-workstation-utilities="true">
            <button
              type="button"
              className="ctrl-btn icon-btn"
              onClick={() => { audioService.playSFX('/audio/sfx/提示音1.ogg', sfxVolume); setIsSettingsOpen(true); }}
              title="系统设置"
              aria-label="系统设置"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
            </button>
            <div className="sound-control-anchor">
              <button
                type="button"
                ref={soundTriggerRef}
                className={`ctrl-btn icon-btn ${isSoundOpen ? 'is-active' : ''}`}
                onClick={() => { audioService.playSFX('/audio/sfx/提示音1.ogg', sfxVolume); setIsSoundOpen((v) => !v); }}
                title="音量控制"
                aria-label="音量控制"
                aria-expanded={isSoundOpen}
                {...(isSoundOpen ? { 'aria-controls': 'sound-mixer-rail' } : {})}
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                  <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07" />
                </svg>
              </button>
            </div>
            <button
              type="button"
              className="ctrl-btn icon-btn disconnect-btn"
              onClick={onLogout}
              title="断开连接"
              aria-label="断开连接"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                <polyline points="16 17 21 12 16 7" />
                <line x1="21" y1="12" x2="9" y2="12" />
              </svg>
            </button>
          </div>
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
          onNew={() => { void createConversation(defaultIdentityMode); }}
          onRename={renameConversation}
          onTogglePin={toggleConversationPin}
          onDelete={(conversation) => { void deleteConversation(conversation); }}
          onForget={setForgetTarget}
          busy={conversationBusy}
        />

        {/* Center: Dialogue Terminal */}
        <div className="grid-terminal border-beam">
          <div
            ref={transcriptViewportRef}
            className="terminal-chat-viewport"
            data-testid="transcript-viewport"
            onScroll={handleTranscriptScroll}
          >
            {visibleMessages.length === 0 ? (
              <div className="empty-chat-tip">
                <div className={`amadeus-logo-glitched worldline-logo-${worldline}`} />
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
                  <div key={msg.id} className={`message-card ${msg.role} ${msg.systemError ? 'system-error-card' : ''}`}>
                    {msg.systemError ? (
                      <p className="system-error-content" role="alert">{msg.content}</p>
                    ) : (
                      <>
                        {msg.role === 'user' ? (
                          <div className="avatar-label speaker-user" data-testid="speaker-user">
                            {userHudLabel}
                          </div>
                        ) : (
                          <div className="avatar-label speaker-assistant" data-testid="speaker-assistant">
                            <span className="speaker-primary" data-testid="speaker-assistant-primary">
                              KURISU
                            </span>
                            <span className="speaker-secondary" data-testid="speaker-assistant-secondary">
                              Amadeus
                            </span>
                          </div>
                        )}
                        <div className="message-content-box">
                          {msg.role === 'assistant' ? (
                            <>
                              <BilingualMessage
                                segments={msg.segments || []}
                                streaming={Boolean(msg.streaming)}
                                fallbackJa={msg.content}
                                fallbackZh={msg.translation}
                              />
                            {msg.streaming && isThinking && operationLabel && (
                              <div className="thinking-indicator-inline" aria-busy={activityBusy}>
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
                                <span
                                  className="thinking-text-glow"
                                  data-testid="activity-operation-label"
                                >
                                  {operationLabel}<span className="thinking-cursor-blink">▌</span>
                                </span>
                              </div>
                            )}
                            {msg.streaming && isSynthesizing && !isThinking && (
                              <div className="status-synthesizing-inline">
                                <span className="speaking-dot animate-ping" />
                                正在合成语音…
                              </div>
                            )}
                            </>
                          ) : (
                            <p
                              className="japanese-raw"
                              {...(contentHasJapaneseKana(msg.content) ? { lang: 'ja' as const } : {})}
                            >
                              {msg.content}
                            </p>
                          )}
                        </div>
                      </>
                    )}
                  </div>
                ))}
                {(isThinking || isSynthesizing) && (
                  <div className="voice-control-capsule" role="group" aria-label="Current response controls">
                    <span className="voice-control-signal">{worldline === 'beta' ? 'β SIGNAL' : 'SG SIGNAL'}</span>
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

          {showJumpToLatest && (
            <button
              type="button"
              className="jump-to-latest-btn"
              data-testid="jump-to-latest"
              aria-label="跳到最新"
              onClick={handleJumpToLatest}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  handleJumpToLatest();
                }
              }}
            >
              <svg
                className="jump-to-latest-chevron"
                aria-hidden="true"
                viewBox="0 0 24 24"
                width="16"
                height="16"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                {/* Full double-chevron: upper and lower arcs both present */}
                <path d="M7 7l5 5 5-5" />
                <path d="M7 13l5 5 5-5" />
              </svg>
            </button>
          )}

          {/* Bottom Chat Input Form */}
          <form className="terminal-input-bar" onSubmit={handleSend}>
            <span className="prompt-arrow">&gt;</span>
            <input
              type="text"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              className="chat-input-field"
              placeholder="输入终端指令或对话内容..."
              disabled={status !== 'READY' || isThinking || isSynthesizing}
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
                  handleStopVoice();
                }}
                title="中断当前语音"
              >
                中断
              </button>
            )}
            <button
              type="submit"
              className="chat-send-btn"
              disabled={status !== 'READY' || !inputText.trim() || isThinking || isSynthesizing}
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
              discoveryStatus={modelsDiscoveryStatus}
              thinkingValue={reasoningEffort}
              onProviderChange={changeProvider}
              onModelChange={changeModel}
              onThinkingChange={(value) => {
                setReasoningEffort(value);
                try {
                  localStorage.setItem('amadeus_pc_reasoning_effort', value);
                  localStorage.setItem(
                    `amadeus_pc_reasoning_effort_${activeProviderId}_${activeModelId}`,
                    value,
                  );
                } catch {}
                updateConfig({ reasoningEffort: value });
              }}
              onRefreshModels={() => { void loadProviderModels(activeProviderId, true); }}
              onConfigureCredential={() => setIsSettingsOpen(true)}
            />
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

      {/* Session / worldline lifecycle errors (not TTS). */}
      {sessionError && (
        <div className="audio-error-toast session-error-toast" data-testid="session-error-toast" role="status">
          <span className="audio-error-msg">
            {sessionError.kind === 'session'
              ? `> Session: ${sessionError.code}`
              : `> Worldline: ${sessionError.code}`}
          </span>
        </div>
      )}

      {/* Audio Error Toast — real TTS/audio degradation only. */}
      {audioError && (
        <div className="audio-error-toast" data-testid="audio-error-toast">
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

      {isMemoryOpen ? (
        <MemorySpace
          sessionId={sessionId}
          chatWorldline={worldline}
          chatIdentityMode={activeIdentityMode}
          onClose={() => setIsMemoryOpen(false)}
        />
      ) : null}

      {/* Settings Modal overlay */}
      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        worldline={worldline}
        sessionId={sessionId}
        apiKey={apiKey}
        credentialConfigured={credentialConfigured}
        credentialStatus={credentialStatus}
        webSearchApiKey={webSearchApiKey}
        webSearchConfigured={webSearchConfigured}
        webSearchStatus={webSearchCredentialStatus}
        webSearchActiveProvider={webSearchActiveProvider}
        webSearchProviders={webSearchProviders}
        webSearchFallbackNote={webSearchFallbackNote}
        onSelectWebSearchProvider={handleSelectWebSearchProvider}
        systemPrompt={systemPrompt}
        enableTts={enableTts}
        sovitsUrl={sovitsUrl}
        enableBgm={enableBgm}
        bgmVolume={bgmVolume}
        enableSfx={enableSfx}
        sfxVolume={sfxVolume}
        temperature={temperature}
        temperatureMigrationNotice={temperatureMigrationNotice}
        onDismissTemperatureMigrationNotice={() => {
          setTemperatureMigrationNotice(false);
          try { sessionStorage.removeItem('amadeus_pc_temperature_migrated_notice'); } catch { /* ignore */ }
        }}
        identityMode={defaultIdentityMode}
        selfName={selfName}
        providerDisplayName={activeProvider?.display_name || activeProviderId}
        providerBaseUrl={activeProvider?.base_url}
        baseUrlConfigurable={Boolean(activeProvider?.base_url_configurable)}
        model={model}
        modelOptions={availableModels}
        reasoningEffort={reasoningEffort}
        customProfiles={providers
          .filter((row) => row.source === 'user_config' || row.id === 'custom' || row.id.startsWith('custom:'))
          .map((row) => ({
            id: row.id,
            display_name: row.display_name,
            default_model: row.default_model,
            base_url: row.base_url,
            enabled: row.enabled !== false,
            configured: Boolean(row.configured),
          }))}
        onCreateCustomProfile={async (input) => {
          const response = await fetch('/api/providers/custom-profiles', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(input),
          });
          if (!response.ok) throw new Error('profile_create_failed');
          const created = await response.json() as { provider_id: string; default_model?: string };
          await loadProviders();
          setDraftProviderId(created.provider_id);
          if (created.default_model) setModel(created.default_model);
          try {
            localStorage.setItem('amadeus_pc_provider', created.provider_id);
            if (created.default_model) {
              localStorage.setItem('amadeus_pc_model', created.default_model);
            }
          } catch {}
          reconnect();
        }}
        onDisableCustomProfile={async (providerId) => {
          const response = await fetch(
            `/api/providers/custom-profiles/${encodeURIComponent(providerId)}`,
            { method: 'DELETE' },
          );
          if (!response.ok) throw new Error('profile_disable_failed');
          // Keep the current selection so the user sees「已停用/不可用」until
          // they explicitly pick another provider — never auto-jump to DeepSeek.
          await loadProviders();
          void loadProviderModels(providerId, true);
        }}
        onSaveSettings={handleSaveSettings}
        onClearWebSearchCredential={handleClearWebSearchCredential}
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
