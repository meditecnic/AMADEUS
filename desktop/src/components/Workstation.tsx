import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  getOrCreateAmadeusSessionId,
  useAmadeusWS,
  type Message,
} from '../hooks/useAmadeusWS';
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
import { HistorySidebar, type ConversationSummary } from './HistorySidebar';
import { TtsDebugPanel } from './TtsDebugPanel';
import AvatarViewer from './AvatarViewer';
import SpriteAnimator from './SpriteAnimator';
import { packAsset } from '../assetPack';
import { useAudioPlayer, audioPlayer } from '../hooks/useAudioPlayer';
import { audioService } from '../services/AudioService';
import { BilingualMessage } from './BilingualMessage';
import { Atmosphere } from '../workstation/Atmosphere';
import { DraftWord } from '../workstation/DraftWord';
import { DIVERGENCE, Nixie } from '../workstation/Nixie';
import { SoundChannel, SystemMenu, WorldlinePanel, type MenuSection } from '../workstation/SystemMenu';
import { ShiftOverlay } from '../workstation/ShiftOverlay';
import { ConfirmDialog } from '../workstation/ConfirmDialog';
import { ConnectionCheckPanel, chatCheckMessage, requestChatCheck, type ChatCheckState } from '../workstation/ConnectionCheck';
import { ConnectionList } from '../workstation/ConnectionList';
import { SendSignal } from '../workstation/SendSignal';
import { VoiceScope } from '../workstation/VoiceScope';
import { BootSequence } from '../workstation/BootSequence';
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
  const [memoryClosing, setMemoryClosing] = useState(false);
  const [memoryOrigin, setMemoryOrigin] = useState({ x: 0, y: 0 });
  const closeMemory = () => {
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (reduce) { setIsMemoryOpen(false); return; }
    setMemoryClosing(true);
    window.setTimeout(() => {
      setIsMemoryOpen(false);
      setMemoryClosing(false);
      document.querySelector<HTMLButtonElement>('.ws2 button[aria-label="MEMORY"]')?.focus();
    }, 280);
  };
  const [inputText, setInputText] = useState('');
  // Each worldline keeps its own unsent draft; a failed switch rolls the prop back, which restores it too.
  const draftsByWorldlineRef = useRef<Partial<Record<string, string>>>({});
  const draftWorldlineRef = useRef(worldline);
  useEffect(() => {
    const previous = draftWorldlineRef.current;
    if (previous === worldline) return;
    draftsByWorldlineRef.current[previous] = inputText;
    setInputText(draftsByWorldlineRef.current[worldline] ?? '');
    draftWorldlineRef.current = worldline;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [worldline]);
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
  const [forgetError, setForgetError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ConversationSummary | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [renameTarget, setRenameTarget] = useState<ConversationSummary | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);
  const transcriptViewportRef = useRef<HTMLDivElement>(null);
  const previousWorldlineRef = useRef(worldline);
  const committedWorldlineRef = useRef(worldline);
  const pendingWorldlineRollbackRef = useRef<'steins_gate' | 'beta' | null>(null);
  const suppressedWorldlineEffectRef = useRef<'steins_gate' | 'beta' | null>(null);
  const latestWorldlineRef = useRef(worldline);
  const conversationRequestRef = useRef(0);
  const pendingConversationModelRef = useRef<{ model: string; provider: string | null } | null>(null);

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
  /** Muting her voice keeps the level for restore and does not touch TTS/autoplay preferences. */
  const [voiceMuted, setVoiceMuted] = useState(() => {
    try { return localStorage.getItem('amadeus_pc_voice_muted') === 'true'; } catch { return false; }
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
  const [conversationCreateError, setConversationCreateError] = useState<string | null>(null);
  const [sessionError, setSessionError] = useState<{
    kind: 'session' | 'worldline';
    code: string;
  } | null>(null);
  const [menuSection, setMenuSection] = useState<MenuSection>('worldline');
  const [shiftConfirm, setShiftConfirm] = useState<'steins_gate' | 'beta' | null>(null);
  const [pendingModel, setPendingModel] = useState<string | null>(null);
  const wsRootRef = useRef<HTMLDivElement>(null);
  const speakingQueueRef = useRef<number[]>([]);
  const [speakingId, setSpeakingId] = useState<number | null>(null);
  // Title card once per app session (skipped under reduced motion and where matchMedia is unavailable, e.g. tests).
  const [bootPhase, setBootPhase] = useState<'draft' | 'dock' | 'done'>(() => {
    try {
      if (sessionStorage.getItem('amadeus_booted') === '1') return 'done';
      if (!window.matchMedia || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return 'done';
      return 'draft';
    } catch { return 'done'; }
  });
  const handleBootDock = useCallback(() => setBootPhase('dock'), []);
  const handleBootDone = useCallback(() => {
    try { sessionStorage.setItem('amadeus_booted', '1'); } catch {}
    setBootPhase('done');
  }, []);
  const [shift, setShift] = useState<{ from: 'steins_gate' | 'beta'; to: 'steins_gate' | 'beta' } | null>(null);
  const systemButtonRef = useRef<HTMLButtonElement>(null);
  const openMenu = useCallback((section?: MenuSection) => {
    if (section) setMenuSection(section);
    setIsSettingsOpen(true);
  }, []);
  const closeMenu = useCallback(() => setIsSettingsOpen(false), []);
  /** Lands on the connections page with focus on either the key field or the first usable other connection. */
  const openConnectionsAt = useCallback((target: 'key' | 'list') => {
    openMenu('connections');
    const selector = target === 'key'
      ? '[data-testid="credential-input"]'
      : '.ws2-conn-row:not(.is-active):not(:disabled)';
    // A conversation's provider switch lands asynchronously, so the key field may appear a moment later.
    let tries = 0;
    const attempt = () => {
      const el = document.querySelector<HTMLElement>(selector);
      if (el) {
        // Scroll only the menu body: scrolling the clipped `.ws2` root would shift the whole page.
        el.focus({ preventScroll: true });
        const body = el.closest<HTMLElement>('.ws2-sys-body');
        if (body) body.scrollTop += el.getBoundingClientRect().top - body.getBoundingClientRect().top - body.clientHeight / 2;
      } else if (++tries < 20) {
        window.setTimeout(attempt, 100);
      }
    };
    window.setTimeout(attempt, 100);
  }, [openMenu]);
  // Chat verification results are local and keyed by provider + model + config version.
  const [chatChecks, setChatChecks] = useState<Record<string, ChatCheckState>>({});
  const [configVersions, setConfigVersions] = useState<Record<string, number>>({});
  const configVersionsRef = useRef(configVersions);
  configVersionsRef.current = configVersions;
  const checkKey = (providerId: string, modelId: string, version = configVersionsRef.current[providerId] ?? 0) =>
    `${providerId}::${modelId}::${version}`;
  const runChatCheck = useCallback(async (providerId: string, modelId: string, version?: number): Promise<ChatCheckState | null> => {
    if (!providerId || !modelId) return null;
    const key = checkKey(providerId, modelId, version);
    setChatChecks((current) => ({ ...current, [key]: { status: 'checking' } }));
    const result = await requestChatCheck(providerId, modelId);
    setChatChecks((current) => ({ ...current, [key]: result }));
    // Only a result for the configuration that is still current may admit anything.
    return key === checkKey(providerId, modelId) ? result : null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* Chat-selector admission is stored by the backend (`selector_admission`): a new connection is pending until
   * its first chat-check passes or the user enables it by hand. */
  const pendingProviders = providers.filter((row) => row.selector_admission === 'pending').map((row) => row.id);
  const manualProviders = providers.filter((row) => row.selector_admission === 'manual').map((row) => row.id);
  const [modelApplyError, setModelApplyError] = useState<string | null>(null);
  /** Address, key or model changes invalidate earlier results for that provider. */
  const bumpConfigVersion = useCallback((providerId: string) => {
    const next = (configVersionsRef.current[providerId] ?? 0) + 1;
    configVersionsRef.current = { ...configVersionsRef.current, [providerId]: next };
    setConfigVersions(configVersionsRef.current);
    return next;
  }, []);
  const [isHistoryCollapsed, setIsHistoryCollapsed] = useState(() => {
    // Narrow windows open with the rail so the floating list never covers the conversation on arrival.
    if (typeof window !== 'undefined' && window.innerWidth < 1240) return true;
    try {
      const stored = localStorage.getItem('amadeus_pc_history_collapsed');
      if (stored !== null) return stored === 'true';
    } catch {}
    return false;
  });
  // Shrinking the window below the overlay breakpoint folds the list back into the rail instead of covering the reading column.
  useEffect(() => {
    let wide = window.innerWidth >= 1240;
    const onResize = () => {
      const nowWide = window.innerWidth >= 1240;
      if (wide && !nowWide) setIsHistoryCollapsed(true);
      wide = nowWide;
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  // Below the breakpoint the open list is a drawer over the reading column: a press outside it (or the header) folds it away.
  useEffect(() => {
    if (isHistoryCollapsed) return;
    const onDown = (event: PointerEvent) => {
      if (window.innerWidth >= 1240) return;
      if ((event.target as Element | null)?.closest?.('.ws2-side, .ws2-top, .ws2-dialog-scrim')) return;
      setIsHistoryCollapsed(true);
    };
    document.addEventListener('pointerdown', onDown);
    return () => document.removeEventListener('pointerdown', onDown);
  }, [isHistoryCollapsed]);

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

  /** false when the connection changed since `version` was read (409) or the write failed. */
  const writeAdmission = useCallback(async (
    providerId: string,
    admission: 'pending' | 'admitted' | 'manual',
    version: string | null | undefined,
  ): Promise<boolean> => {
    if (!version) return false;
    try {
      const response = await fetch(`/api/providers/custom-profiles/${encodeURIComponent(providerId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ selector_admission: admission, expected_configuration_version: version }),
      });
      return response.ok;
    } catch {
      return false;
    } finally {
      await loadProviders().catch(() => undefined);
    }
  }, [loadProviders]);

  // One-time move of the pre-backend admission lists; the keys are dropped only after every write succeeded.
  const admissionMigratedRef = useRef(false);
  useEffect(() => {
    if (admissionMigratedRef.current || providers.length === 0) return;
    admissionMigratedRef.current = true;
    const read = (key: string): string[] => {
      try { const raw = JSON.parse(localStorage.getItem(key) || '[]'); return Array.isArray(raw) ? raw.filter((x) => typeof x === 'string') : []; } catch { return []; }
    };
    const legacy = [
      ...read('amadeus_pc_pending_providers').map((id) => [id, 'pending'] as const),
      ...read('amadeus_pc_manual_providers').map((id) => [id, 'manual'] as const),
    ];
    if (legacy.length === 0) return;
    void (async () => {
      let ok = true;
      for (const [id, admission] of legacy) {
        const row = providers.find((p) => p.id === id);
        if (!row || row.enabled === false || row.selector_admission !== 'admitted') continue;
        if (!(await writeAdmission(id, admission, row.configuration_version))) ok = false;
      }
      if (!ok) return;
      try {
        localStorage.removeItem('amadeus_pc_pending_providers');
        localStorage.removeItem('amadeus_pc_manual_providers');
      } catch {}
    })();
  }, [providers, writeAdmission]);

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

  // No stored model (cleared storage / older bug wrote '') → use the draft connection's default, not the global
  // 'deepseek-flash' placeholder, which a custom connection's server rejects.
  const modelWasStoredRef = useRef(Boolean(localStorage.getItem('amadeus_pc_model')));
  // Older builds stored only the model when opening a conversation, leaving pairs like (Mock, glm-5.2) that the
  // server rejects. Checked once against the first provider list; live discovery may legitimately add models
  // outside the static catalog, so only a model owned by another provider's catalog counts as foreign.
  const storedPairCheckedRef = useRef(false);
  useEffect(() => {
    const provider = providers.find((row) => row.id === draftProviderId);
    let foreignModel = false;
    if (provider && !storedPairCheckedRef.current) {
      storedPairCheckedRef.current = true;
      foreignModel = !provider.catalog?.some((entry) => entry.id === model)
        && providers.some((row) => row.id !== provider.id && row.catalog?.some((entry) => entry.id === model));
    }
    if (modelWasStoredRef.current && model.trim() && !foreignModel) return;
    if (!provider?.default_model) return;
    modelWasStoredRef.current = true;
    if (provider.default_model === model) return;
    setModel(provider.default_model);
    try { localStorage.setItem('amadeus_pc_model', provider.default_model); } catch {}
    window.setTimeout(() => reconnect(), 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model, draftProviderId, providers]);

  // WebSocket hook connection management
  const {
    status,
    sessionState,
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
    resetToDraft,
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
    onAudioChunk: (sentenceId, base64Data) => {
      // Chunks play in arrival order; the head of this queue is the sentence being heard.
      speakingQueueRef.current.push(sentenceId);
      setSpeakingId(speakingQueueRef.current[0] ?? null);
      audioPlayer.playBase64(base64Data, () => {
        speakingQueueRef.current.shift();
        setSpeakingId(speakingQueueRef.current[0] ?? null);
      });
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
      const pending = pendingConversationModelRef.current;
      if (pending) {
        setModel(pending.model);
        try { localStorage.setItem('amadeus_pc_model', pending.model); } catch {}
        // Stored model and provider must move together, or the next draft pairs this model with another provider.
        if (pending.provider) {
          setDraftProviderId(pending.provider);
          try { localStorage.setItem('amadeus_pc_provider', pending.provider); } catch {}
        }
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
  // stopAll() drops queued chunks without their onEnded; after a quiet moment the speaking queue is stale.
  useEffect(() => {
    if (isPlaying) return;
    const t = window.setTimeout(() => { speakingQueueRef.current = []; setSpeakingId(null); }, 800);
    return () => window.clearTimeout(t);
  }, [isPlaying]);

  useEffect(() => {
    // setVoiceVolume persists the level, so mute goes through setMuted to keep the level for restore.
    audioPlayer.setVoiceVolume?.(voiceVolume);
    audioPlayer.setMuted?.(voiceMuted);
    try { localStorage.setItem('amadeus_pc_voice_muted', String(voiceMuted)); } catch {}
  }, [voiceVolume, voiceMuted]);

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
    // After switching away, a "missing key" error about the previous connection no longer describes anything.
    : messages.filter((message) => {
      const missing = /缺失\s*(\S+)\s*API\s*Key/i.exec(message.content);
      return !missing || missing[1] === activeProviderId;
    });
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
    if (!inputText.trim() || conversationBusy || sessionState === 'rejected' || sessionState === 'unknown' || providerGone) return;
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
      pendingConversationModelRef.current = conversation.model_id
        ? { model: conversation.model_id, provider: conversation.provider_id || null }
        : null;
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
    setConversationCreateError(null);
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
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        const code = body?.detail?.code;
        throw new Error(code === 'model_unavailable'
          ? '新建对话失败：当前模型不可用，请重新选择模型。'
          : `新建对话失败（HTTP ${response.status}），请重试。`);
      }
      const created = await response.json() as ConversationSummary;
      setConversationBusy(false);
      await selectConversation(created);
      window.setTimeout(() => { void loadConversations(); }, 250);
      return created;
    } catch (error) {
      console.warn('Conversation creation failed:', error);
      setConversationCreateError(error instanceof Error ? error.message : '新建对话失败，请重试。');
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
    // A list query sent before this write would land afterwards with the old provider/model.
    conversationRequestRef.current += 1;
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
      // The remembered model and provider must move together, or the next draft pairs a model with the wrong provider.
      if (updated.provider_id) {
        setDraftProviderId(updated.provider_id);
        try { localStorage.setItem('amadeus_pc_provider', updated.provider_id); } catch {}
      }
      // Provider/model are authenticated and snapshotted when the socket opens.
      // Reusing a socket that previously failed authentication leaves it in the
      // server's unauthenticated state, so reload the durable conversation on a
      // fresh connection after either runtime setting changes.
      reconnect();
      const refreshedProviders = await loadProviders();
      const provider = refreshedProviders.find((row) => row.id === updated.provider_id);
      // Keyless connections (local endpoints) are never "configured"; only a missing required key needs the menu.
      if (provider?.credential_required && !provider.configured) openConnectionsAt('key');
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
      if (provider?.credential_required && !provider.configured) openConnectionsAt('key');
      return;
    }
    void patchActiveConversationRuntime({ provider_id: providerId }).catch((error) => {
      console.warn('Conversation provider update failed:', error);
    });
  };

  const changeModel = (modelId: string) => {
    if (isThinking || isSynthesizing) {
      // The running turn keeps the model it started with; the choice applies from the next turn.
      setPendingModel(modelId === (activeConversation?.model_id || model) ? null : modelId);
      return;
    }
    if (!modelId || modelId === activeConversation?.model_id) return;
    if (!activeConversation) {
      if (modelId === model) return;
      setModel(modelId);
      try { localStorage.setItem('amadeus_pc_model', modelId); } catch {}
      window.setTimeout(reconnect, 0);
      return;
    }
    const previous = activeConversation.model_id || model;
    setModelApplyError(null);
    void patchActiveConversationRuntime({ model_id: modelId }).catch((error) => {
      console.warn('Conversation model update failed:', error);
      setModelApplyError(`没能切换到 ${modelId}，这段对话仍在使用 ${previous}。`);
    });
  };

  /** Put a connection into the chat selector and select it for the draft, as creation used to do unconditionally. */
  const admitProvider = async (
    providerId: string,
    modelId: string,
    version: string | null | undefined,
    admission: 'admitted' | 'manual' = 'admitted',
  ) => {
    // `version` is the one the check started from, so a late pass never admits an edited connection.
    if (!(await writeAdmission(providerId, admission, version))) return;
    setDraftProviderId(providerId);
    setModel(modelId);
    try {
      localStorage.setItem('amadeus_pc_provider', providerId);
      localStorage.setItem('amadeus_pc_model', modelId);
    } catch {}
    reconnect();
  };
  const enableProviderManually = (providerId: string, modelId: string, version: string | null | undefined) => {
    void admitProvider(providerId, modelId, version, 'manual');
  };

  // Apply a model chosen mid-turn once the turn has fully finished.
  useEffect(() => {
    if (!pendingModel || isThinking || isSynthesizing || conversationBusy) return;
    const next = pendingModel;
    setPendingModel(null);
    changeModel(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingModel, isThinking, isSynthesizing, conversationBusy]);

  // A pending choice belongs to the conversation it was made in; never carry it across conversations or worldlines.
  const pendingScopeRef = useRef<{ conversation: string | null; worldline: string }>({ conversation: activeConversationId, worldline });
  useEffect(() => {
    const previous = pendingScopeRef.current;
    const movedConversation = previous.conversation !== null && previous.conversation !== activeConversationId;
    if (movedConversation || previous.worldline !== worldline) setPendingModel(null);
    pendingScopeRef.current = { conversation: activeConversationId, worldline };
  }, [activeConversationId, worldline]);

  const renameConversation = (conversation: ConversationSummary) => {
    setRenameError(null);
    setRenameValue(conversation.title);
    setRenameTarget(conversation);
  };

  const confirmRenameConversation = async () => {
    const conversation = renameTarget;
    const title = renameValue.trim();
    if (!conversation || renameBusy) return;
    if (!title || title === conversation.title) { setRenameTarget(null); return; }
    setRenameBusy(true);
    setRenameError(null);
    try {
      await patchConversation(conversation, { title });
      setRenameTarget(null);
    } catch (error) {
      console.warn('Conversation rename failed:', error);
      setRenameError('重命名失败，请重试。');
    } finally {
      setRenameBusy(false);
    }
  };

  const toggleConversationPin = (conversation: ConversationSummary) => {
    void patchConversation(conversation, { is_pinned: !conversation.is_pinned }).catch((error) => {
      console.warn('Conversation pin update failed:', error);
    });
  };

  const deleteConversation = (conversation: ConversationSummary) => {
    setDeleteError(null);
    setDeleteTarget(conversation);
  };

  const confirmDeleteConversation = async () => {
    const conversation = deleteTarget;
    if (!conversation || deleteBusy) return;
    setDeleteBusy(true);
    setDeleteError(null);
    const wasActive = conversation.id === activeConversationId;
    try {
      if (wasActive && (isThinking || isSynthesizing)) handleCancelTurn();
      const response = await fetch(
        `/api/conversations/${encodeURIComponent(conversation.id)}?session_id=${encodeURIComponent(sessionId)}&worldline=${encodeURIComponent(worldline)}`,
        { method: 'DELETE' },
      );
      if (!response.ok) throw new Error(`conversation delete failed: ${response.status}`);
      setDeleteTarget(null);
      await loadConversations();
      if (wasActive) {
        // Q51: deleting the open conversation returns to a blank draft; never silently open another one.
        audioPlayer.stopAll();
        setActiveConversationId(null);
        resetToDraft?.();
        window.setTimeout(reconnect, 0);
      }
    } catch (error) {
      console.warn('Conversation deletion failed:', error);
      setDeleteError('删除失败，会话仍然保留。请稍后重试。');
    } finally {
      setDeleteBusy(false);
    }
  };

  const forgetConversation = async (forgetLongTerm: boolean) => {
    if (!forgetTarget || conversationBusy) return;
    setConversationBusy(true);
    setForgetError(null);
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
      setForgetError('清空失败，聊天记录仍然保留。请稍后重试。');
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
        (newSettings.model && newSettings.model !== activeProvider.default_model)
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
          // An untouched model field arrives empty; keep the connection's own default.
          default_model: newSettings.model.trim() || activeProvider.default_model,
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
      if (newSettings.model) localStorage.setItem('amadeus_pc_model', newSettings.model);
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
        enableTts: newSettings.enableTts,
        sovitsUrl: newSettings.sovitsUrl,
        temperature: safeTemperature,
        reasoningEffort: newSettings.reasoningEffort,
        identityMode: nextIdentityMode,
        selfName: newSettings.selfName,
      };
      // An unchanged prompt must not ride along. The server treats a real
      // persona change as a history reset and would drop an in-flight reply.
      if (newSettings.systemPrompt !== systemPrompt) {
        configPatch.systemPrompt = newSettings.systemPrompt;
      }
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

  /** Visual shift → existing worldline switch path. Leftover voice is stopped first so no audio crosses lines. */
  const startShift = (to: 'steins_gate' | 'beta') => {
    if (shift || to === worldline) return;
    // Leftover voice fades out (well before the commit point of the animation), then stops.
    if (isPlaying) {
      const fade = audioPlayer.fadeOut?.(400);
      if (fade) void fade.then(() => handleStopVoice());
      else handleStopVoice();
    }
    closeMenu();
    setShift({ from: worldline, to });
  };

  const now = new Date();
  const dateLabel = `${now.getMonth() + 1}/${now.getDate()}`;
  const dayLabel = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'][now.getDay()];
  // A socket that the server rejected (bad model/credentials) is not online even though it is open.
  const sessionRejected = sessionState === 'rejected';
  // ONLINE means the server said session.ready; an open-but-unconfirmed socket is still connecting.
  // (undefined = test doubles without the field.)
  const sessionPending = status === 'READY' && sessionState === 'unknown';
  // The shift animation ends on its own clock; until the server confirms the target line we say so.
  const awaitingWorldline = conversationBusy && committedWorldlineRef.current !== worldline;
  const online = status === 'READY' && !needsApiKey && !sessionRejected && !sessionPending && !awaitingWorldline;
  const connectionLabel = awaitingWorldline ? 'SYNC · 等待目标世界线确认'
    : online ? 'ONLINE'
    : needsApiKey ? 'OFFLINE · 待配置'
      : sessionRejected ? 'OFFLINE · 会话被拒绝'
        : status === 'CONNECTING' || sessionPending ? 'OFFLINE · 连接中' : 'OFFLINE';
  const presence = isThinking ? 'thinking' : isPlaying ? 'speaking' : isSynthesizing ? 'voicing' : online ? 'listening' : 'offline';
  const presenceLabel = { thinking: '思考中', speaking: '说话中', voicing: '准备语音', listening: '聆听中', offline: '未连接' }[presence];
  const thinkingSprite = packAsset(worldline === 'steins_gate' ? 'assets/ui/spinner_sg_20f.png' : 'assets/ui/spinner_beta_20f.png');
  const lastAssistantId = [...visibleMessages].reverse().find((msg) => msg.role === 'assistant' && !msg.systemError)?.id;
  // An old conversation whose connection was removed keeps its history but must be re-pointed by the user.
  const providerGone = Boolean(activeConversation) && providers.length > 0 && (!activeProvider || activeProvider.enabled === false);
  const sendBlocked = status !== 'READY' || sessionState === 'rejected' || sessionState === 'unknown' || isThinking || isSynthesizing || conversationBusy || providerGone;

  return (
    <div
      ref={wsRootRef}
      className={`ws2${isHistoryCollapsed ? ' rail' : ''}${isSettingsOpen ? ' menu-open' : ''}${isMemoryOpen ? ' memory-space-open' : ''}${bootPhase !== 'done' ? ` boot-${bootPhase}` : ''}`}
      data-wl={worldline === 'beta' ? 'beta' : 'sg'}
      onMouseDown={handleAudioInit}
    >
      <Atmosphere worldline={worldline} />
      {bootPhase !== 'done' && <BootSequence worldline={worldline} onDock={handleBootDock} onDone={handleBootDone} />}
      <SendSignal
        rootRef={wsRootRef}
        userCount={messages.filter((m) => m.role === 'user').length}
        lastIsUser={messages[messages.length - 1]?.role === 'user'}
      />

      <header className="ws2-top">
        <button
          type="button"
          className="ws2-icon-btn"
          onClick={() => setIsHistoryCollapsed((v) => !v)}
          aria-label={isHistoryCollapsed ? '展开会话列表' : '收起会话列表'}
          aria-expanded={!isHistoryCollapsed}
        >
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="2" y="2.5" width="12" height="11" fill="none" stroke="currentColor" strokeWidth="1.2" /><path d="M6 2.5v11" stroke="currentColor" strokeWidth="1.2" /></svg>
        </button>
        <div className="ws2-wordmark" aria-label="AMADEUS"><DraftWord key={worldline} text="AMADEUS" step={90} /></div>
        <span className="ws2-date">
          <span className="d">{dateLabel}</span><b>{dayLabel}</b>
        </span>
        <span className="ws2-title">{activeConversation?.title ? <span className="t">{activeConversation.title}</span> : null}</span>

        <div className="ws2-top-right">
          <button
            type="button"
            className="ws2-memory-btn"
            aria-label="MEMORY"
            title="MEMORY"
            onClick={(event) => {
              const r = event.currentTarget.querySelector('svg')?.getBoundingClientRect() ?? event.currentTarget.getBoundingClientRect();
              setMemoryOrigin({ x: r.left + r.width / 2, y: r.top + r.height / 2 });
              setMemoryClosing(false);
              setIsMemoryOpen(true);
            }}
          >
            <svg className="ws2-engram" viewBox="0 0 32 32" width="22" height="22" aria-hidden="true">
              <circle cx="16" cy="16" r="13" fill="none" stroke="currentColor" strokeWidth="1" strokeDasharray="62 20" transform="rotate(-40 16 16)" />
              <circle cx="16" cy="16" r="8.5" fill="none" stroke="var(--ws-accent)" strokeWidth="1.2" strokeDasharray="36 18" transform="rotate(130 16 16)" />
              <circle cx="16" cy="16" r="2.2" fill="var(--ws-accent-2)" />
            </svg>
            <span className="ws2-memory-zh" aria-hidden="true">记忆</span>
            <span className="ws2-memory-en" aria-hidden="true">MEMORY</span>
          </button>
          <div className="ws2-booth-mid">
            <div className="ws2-meter">
              <span className="ws2-wl-tag">{worldline === 'beta' ? 'β WORLDLINE' : 'STEINS;GATE'}</span>
              <Nixie value={DIVERGENCE[worldline]} size="sm" />
            </div>
            <div
              className={`ws2-online${online ? ' is-online' : ''}${awaitingWorldline ? ' is-sync' : ''}`}
              role="status"
              title={connectionLabel}
              data-status={needsApiKey ? 'SETUP' : awaitingWorldline ? 'SYNC' : sessionRejected ? 'REJECTED' : status}
            >
              <span className="ws2-online-dot" aria-hidden="true" />{connectionLabel}
            </div>
          </div>
          <div className="ws2-booth-end">
          <button
            type="button"
            ref={systemButtonRef}
            className={`ws2-sys-btn${isSettingsOpen ? ' on' : ''}`}
            onClick={() => {
              audioService.playSFX('/audio/sfx/提示音1.ogg', sfxVolume);
              if (isSettingsOpen) closeMenu(); else openMenu();
            }}
            title="系统设置"
            aria-label="系统设置"
            aria-expanded={isSettingsOpen}
          >
            {isSettingsOpen ? <>返回<kbd>Esc</kbd></> : '系统'}
          </button>
          <button type="button" className="ws2-icon-btn" onClick={onLogout} title="断开连接" aria-label="断开连接">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><polyline points="16 17 21 12 16 7" /><line x1="21" y1="12" x2="9" y2="12" /></svg>
          </button>
          </div>
        </div>
      </header>

      <nav className="ws2-side" aria-label="会话" inert={isSettingsOpen}>
        <HistorySidebar
          isCollapsed={isHistoryCollapsed}
          onToggle={() => setIsHistoryCollapsed((v) => !v)}
          conversations={conversations}
          activeId={activeConversationId}
          searchQuery={conversationSearch}
          onSearchChange={setConversationSearch}
          onSelect={(conversation) => {
            if (window.innerWidth < 1240) setIsHistoryCollapsed(true);
            void selectConversation(conversation);
          }}
          onNew={() => { void createConversation(defaultIdentityMode); }}
          onRename={renameConversation}
          onTogglePin={toggleConversationPin}
          onDelete={(conversation) => { void deleteConversation(conversation); }}
          onForget={(conversation) => { setForgetError(null); setForgetTarget(conversation); }}
          busy={conversationBusy}
        />
      </nav>

      <section className="ws2-talk" inert={isSettingsOpen}>
        <div className="ws2-transcript-wrap">
          <div
            ref={transcriptViewportRef}
            className="terminal-chat-viewport ws2-transcript"
            data-testid="transcript-viewport"
            onScroll={handleTranscriptScroll}
          >
            {visibleMessages.length === 0 ? (
              // An empty conversation is a fresh observation log, not a terminal prompt.
              <div className="ws2-empty">
                <div className={`ws2-empty-card ${emptyStateKind}`}>
                  <small className="ws2-empty-kicker">OBSERVATION LOG · {dateLabel} ({dayLabel})</small>
                  <h2 className="ws2-empty-head">
                    {needsApiKey ? '尚未接通' : status === 'READY' ? '新的观测记录' : status === 'CONNECTING' ? '正在接通' : '暂时无法接通'}
                  </h2>
                  <p className={`ws2-empty-msg ${emptyStateKind}`}>
                    {status === 'READY' && !needsApiKey
                      ? '说第一句话吧。她的日语原文与中文字幕会一并记在这里。'
                      : emptyStateMessage.replace(/^>\s*/, '')}
                  </p>
                  {needsApiKey && (
                    <div className="ws2-empty-actions">
                      <button type="button" className="ws2-empty-action" onClick={() => openConnectionsAt('key')}>
                        填写密钥
                      </button>
                      <button type="button" className="ws2-empty-action is-quiet" onClick={() => openConnectionsAt('list')}>
                        换一个接入
                      </button>
                    </div>
                  )}
                  <div className="ws2-empty-rule" aria-hidden="true">
                    <i /><span>{worldline === 'beta' ? 'β' : 'STEINS;GATE'} · {DIVERGENCE[worldline]}%</span>
                  </div>
                </div>
              </div>
            ) : (
              <div className="ws2-lines">
                {visibleMessages.map((msg) => (
                  msg.systemError ? (
                    <article key={msg.id} className="ws2-line ws2-line-error">
                      <p className="system-error-content" role="alert">{msg.content}</p>
                    </article>
                  ) : msg.role === 'user' ? (
                    <article key={msg.id} className="ws2-line ws2-line-user">
                      <header><span className="ws2-who" data-testid="speaker-user">{userHudLabel}</span></header>
                      <p
                        className="ws2-user-text"
                        {...(contentHasJapaneseKana(msg.content) ? { lang: 'ja' as const } : {})}
                      >
                        {msg.content}
                      </p>
                    </article>
                  ) : (
                    <article key={msg.id} className="ws2-line ws2-line-kurisu">
                      <header>
                        <span className="ws2-who" data-testid="speaker-assistant">
                          <span data-testid="speaker-assistant-primary">KURISU</span>
                          <span className="ws2-who-sub" data-testid="speaker-assistant-secondary">Amadeus</span>
                        </span>
                      </header>
                      <BilingualMessage
                        layout="paragraph"
                        segments={msg.segments || []}
                        streaming={Boolean(msg.streaming)}
                        fallbackJa={msg.content}
                        fallbackZh={msg.translation}
                        translationScope={msg.translationScope}
                        speakingId={isPlaying && msg.id === lastAssistantId ? speakingId : null}
                      />
                      {msg.streaming && isThinking && operationLabel && (
                        <div className="ws2-thinking" aria-busy={activityBusy}>
                          {thinkingSprite ? <SpriteAnimator
                            spriteSrc={thinkingSprite}
                            cols={10}
                            rows={2}
                            frameWidth={worldline === 'beta' ? 54.5 : 54}
                            frameHeight={63}
                            frameRowOffsetY={worldline === 'steins_gate' ? 8 : 0}
                            frameCount={20}
                            fps={15}
                            loop={true}
                          /> : <span className="ws2-thinking-ring" aria-hidden="true" />}
                          <span className="ws2-thinking-label" data-testid="activity-operation-label">{operationLabel}</span>
                        </div>
                      )}
                      {msg.streaming && isSynthesizing && !isThinking && (
                        <div className="ws2-voicing">正在合成语音…</div>
                      )}
                      {msg.id === lastAssistantId && (isThinking || isSynthesizing || isPlaying) && (
                        <div className="ws2-actions is-latest" role="group" aria-label="Current response controls">
                          <button type="button" onClick={handleStopVoice} disabled={!isSynthesizing && !isPlaying}>停止本句</button>
                          <button type="button" onClick={handleCancelTurn}>停止本轮</button>
                        </div>
                      )}
                    </article>
                  )
                ))}
              </div>
            )}
          </div>
          {showJumpToLatest && (
            <button
              type="button"
              className="ws2-to-latest"
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
              回到最新 ↓
            </button>
          )}
        </div>

        {providerGone && (
          <div className="ws2-provider-gone" role="status">
            这段对话原来使用的「{activeProvider?.display_name || '接入'}」已被移除。聊天记录仍在；请在下方重新选择模型后继续，不会自动换到其他厂商。
          </div>
        )}
        {needsApiKey && !providerGone && visibleMessages.length > 0 && (
          <div className="ws2-key-missing" role="status">
            <span>「{activeProvider?.display_name || activeProviderId}」还没有密钥，她现在听不到你。</span>
            <button type="button" onClick={() => openConnectionsAt('key')}>填写密钥</button>
            <button type="button" onClick={() => openConnectionsAt('list')}>换一个接入</button>
          </div>
        )}
        <form className="ws2-composer" onSubmit={handleSend}>
          <textarea
            rows={2}
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                e.currentTarget.form?.requestSubmit();
              }
            }}
            className="chat-input-field"
            placeholder="对红莉栖说点什么……"
            aria-label="输入消息"
            disabled={sendBlocked}
          />
          <div className="ws2-composer-row">
            <div className="ws2-model">
              <ProviderControls
                providers={providers.filter((row) => (
                  // Removed and not-yet-admitted connections leave the selector; the current one stays visible as the history source.
                  (row.enabled !== false && !pendingProviders.includes(row.id)) || row.id === activeProviderId
                ))}
                activeProviderId={activeProviderId}
                activeModelId={activeConversation?.model_id || model}
                models={availableModels}
                disabled={conversationBusy}
                lockProvider={isThinking || isSynthesizing}
                searchable
                onManageConnections={() => openMenu('connections')}
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
                onConfigureCredential={() => openMenu('connections')}
              />
            </div>
            {(() => {
              const state = chatChecks[checkKey(activeProviderId, activeModelId)];
              const label = !state ? '所选模型尚未测试'
                : state.status === 'checking' ? '正在测试连接…'
                  : state.status === 'ok' ? '连接测试已通过' : `连接测试未通过：${chatCheckMessage(state.code)}`;
              return (
                <button
                  type="button"
                  className={`ws2-check-dot ${state?.status ?? 'untested'}`}
                  title={label}
                  aria-label={label}
                  onClick={() => openMenu('connections')}
                />
              );
            })()}

            <button
              type="submit"
              className="ws2-send"
              disabled={sendBlocked || !inputText.trim()}
              aria-label="发送"
            >
              发送
            </button>
          </div>
          {(pendingModel || modelApplyError) && (
            <div className="ws2-composer-notes">
              {pendingModel && (
                <span className="ws2-pending" role="status" title="本轮仍使用发起时的模型">下一轮：{pendingModel}</span>
              )}
              {modelApplyError && (
                <span className="ws2-apply-error" role="alert">
                  {modelApplyError}
                  <button type="button" aria-label="关闭提示" onClick={() => setModelApplyError(null)}>×</button>
                </span>
              )}
            </div>
          )}
        </form>
      </section>

      <section className={`ws2-stage presence-${presence}`}>
        <div className="ws2-seam" aria-hidden="true" />
        {/* Her name set like the original's chapter cards: black Mincho over drafting instruments, redrawn on each shift. */}
        <div className="ws2-chapter" key={`chapter-${worldline}`} aria-hidden="true">
          <svg className="ws2-chapter-draft" viewBox="0 0 240 640" preserveAspectRatio="xMaxYMin meet">
            <path pathLength={1} d="M232 120 A 200 200 0 0 0 52 320" />
            <path pathLength={1} d="M232 120 V 320 H 52" />
            <path pathLength={1} d="M232 60 V 612 M222 612 H 240" />
            {Array.from({ length: 28 }, (_, i) => <path key={i} pathLength={1} d={`M232 ${80 + i * 19} h ${i % 5 === 0 ? -9 : -5}`} className="tick" />)}
            <circle pathLength={1} cx="96" cy="196" r="17" /><circle pathLength={1} cx="96" cy="196" r="6" />
            <path pathLength={1} d="M70 196 H 122 M96 170 V 222" />
            <circle pathLength={1} cx="172" cy="236" r="30" /><circle pathLength={1} cx="172" cy="236" r="24" className="thin" />
            <path pathLength={1} d="M26 596 L 206 356" />
            {Array.from({ length: 16 }, (_, i) => <path key={i} pathLength={1} d={`M${38 + i * 10.6} ${580 - i * 14.1} l 4 3`} className="tick" />)}
            <circle pathLength={1} cx="44" cy="572" r="9" /><path pathLength={1} d="M30 572 H 58 M44 558 V 586" />
          </svg>
          <div className="ws2-chapter-set">
            <div className="ws2-chapter-kana" lang="ja">
              {['まき', 'せ', 'く', 'り', 'す'].map((reading, i) => <span key={i} className={i === 2 ? 'giv' : undefined}>{reading}</span>)}
            </div>
            <div className="ws2-chapter-jp" lang="ja">牧瀬紅莉栖</div>
            <div className="ws2-chapter-en"><i>Amadeus</i></div>
          </div>
        </div>
        {/* Amadeus window: a dot-matrix field behind her that stirs while she thinks and pulses while she speaks. */}
        <div className="ws2-matrix" aria-hidden="true" />
        
        <AvatarViewer
          variant="stage"
          emotion={emotion}
          isSpeaking={isPlaying}
          volume={currentVolume}
          glitchState={glitchState}
        />
        <div className="ws2-plate">
          <VoiceScope
            level={currentVolume}
            volume={voiceVolume}
            muted={voiceMuted}
            speaking={isPlaying}
            onVolume={handleVoiceVolumeChange}
            onToggleMute={() => setVoiceMuted((m) => !m)}
            onStop={() => { audioService.playSFX('/audio/sfx/提示音1.ogg', sfxVolume); handleStopVoice(); }}
          />
          <div className="ws2-presence" role="status"><i aria-hidden="true" />{presenceLabel}</div>
        </div>
        {isSettingsOpen && (() => {
          const latest = visibleMessages.find((msg) => msg.id === lastAssistantId);
          const zh = latest
            ? ((latest.segments || []).map((segment) => segment.zh).join('') || latest.translation || latest.content)
            : '';
          return (
            <div className="ws2-stage-preview">
              <div className="head"><span className="who">KURISU</span><span className="state"><i aria-hidden="true" />{presenceLabel}</span></div>
              <p>{isThinking ? '……' : zh || '……'}</p>
              {(isPlaying || isSynthesizing) && (
                <button type="button" onClick={handleStopVoice}>■ 停止本句</button>
              )}
            </div>
          );
        })()}
      </section>

      <div className="ws2-toasts">
        {sessionError && (
          <div className="audio-error-toast session-error-toast" data-testid="session-error-toast" role="status">
            <span className="audio-error-msg">
              {sessionError.kind === 'session' ? '会话同步失败' : '世界线切换失败，已留在原来的世界线'}
            </span>
            <code className="ws2-toast-code">{sessionError.code}</code>
          </div>
        )}
        {conversationCreateError && (
          <div className="audio-error-toast session-error-toast" role="alert">
            <span className="audio-error-msg">{conversationCreateError}</span>
          </div>
        )}
        {audioError && (
          <div className={`audio-error-toast${audioError === 'action_only' || audioError === 'user_disabled' ? ' info' : ''}`} data-testid="audio-error-toast">
            <span className="audio-error-msg">
              {audioError === 'action_only' && '这句是动作描写，不朗读。'}
              {audioError === 'user_disabled' && '自动朗读已关闭。'}
              {(audioError === 'service_error' || audioError === 'service_unavailable') && '语音服务暂时不可用，这一轮只显示文字。'}
              {audioError === 'unknown' && '语音出了点问题，这一轮只显示文字。'}
              {!['action_only', 'user_disabled', 'service_error', 'service_unavailable', 'unknown'].includes(audioError) && '语音未能播放，这一轮只显示文字。'}
            </span>
            {!['action_only', 'user_disabled'].includes(audioError) && <code className="ws2-toast-code">TTS · {audioError}</code>}
          </div>
        )}
      </div>

      {isMemoryOpen ? (
        // Memory opens as an iris from the engram key and closes back into it; Memory itself is unchanged.
        <div
          className={`ws2-memory-layer${memoryClosing ? ' closing' : ''}`}
          style={{ '--mx': `${memoryOrigin.x}px`, '--my': `${memoryOrigin.y}px` } as React.CSSProperties}
        >
          <MemorySpace
            sessionId={sessionId}
            chatWorldline={worldline}
            chatIdentityMode={activeIdentityMode}
            onClose={closeMemory}
          />
        </div>
      ) : null}

      <SystemMenu
        open={isSettingsOpen}
        section={menuSection}
        onSection={setMenuSection}
        onClose={closeMenu}
        returnFocusRef={systemButtonRef}
      >
        {/* Fixed slots keep the embedded SettingsModal mounted across sections, so unsaved drafts survive. */}
        <div className={`ws2-conn${menuSection === 'connections' ? ' is-split' : ''}`}>
        {menuSection === 'connections' && (
          <ConnectionList
            providers={providers}
            activeProviderId={activeProviderId}
            checkFor={(row) => chatChecks[checkKey(row.id, row.id === activeProviderId ? activeModelId : row.default_model || '')]}
            locked={isThinking || isSynthesizing || conversationBusy}
            onSelect={changeProvider}
          />
        )}
        <div className="ws2-conn-detail">
        {menuSection === 'worldline' && (
          <WorldlinePanel
            worldline={worldline}
            busy={Boolean(shift)}
            onArm={() => audioService.playSFX('/audio/sfx/提示音1.ogg', sfxVolume)}
            onShift={(to) => {
              if (isThinking || isSynthesizing || isPlaying) setShiftConfirm(to);
              else startShift(to);
            }}
          />
        )}
        {menuSection === 'connections' && (
          <>
            <ConnectionCheckPanel
              providerName={activeProvider?.display_name || activeProviderId}
              modelId={activeModelId}
              state={chatChecks[checkKey(activeProviderId, activeModelId)]}
              manual={manualProviders.includes(activeProviderId)}
              onCheck={() => { void runChatCheck(activeProviderId, activeModelId); }}
            />
            {providers
              .filter((row) => pendingProviders.includes(row.id) && row.enabled !== false && row.id !== activeProviderId)
              .map((row) => {
                const modelId = row.default_model || '';
                const version = row.configuration_version;
                return (
                  <ConnectionCheckPanel
                    key={row.id}
                    providerName={row.display_name}
                    modelId={modelId}
                    state={chatChecks[checkKey(row.id, modelId)]}
                    pending
                    onCheck={() => {
                      void runChatCheck(row.id, modelId).then((result) => {
                        if (result?.status === 'ok') void admitProvider(row.id, modelId, version);
                      });
                    }}
                    onManualEnable={() => enableProviderManually(row.id, modelId, version)}
                  />
                );
              })}
          </>
        )}
        {menuSection === 'sound' && (
          <div className="ws2-sound-panel">
            <SoundChannel
              name="角色语音"
              hint="红莉栖的日语语音"
              volume={voiceVolume}
              onVolume={handleVoiceVolumeChange}
              enabled={!voiceMuted}
              onEnabled={(on) => setVoiceMuted(!on)}
            />
            <SoundChannel name="背景音乐" hint="BGM" volume={bgmVolume} onVolume={setBgmVolume} enabled={enableBgm} onEnabled={setEnableBgm} />
            <SoundChannel name="界面音效" hint="送信 · 菜单 · 提示" volume={sfxVolume} onVolume={setSfxVolume} enabled={enableSfx} onEnabled={setEnableSfx} />
            <div className="ws2-m-group">语音合成</div>
          </div>
        )}
        <div className="ws2-settings-host" hidden={menuSection === 'worldline'}>
      <SettingsModal
        variant="embedded"
        tab={menuSection === 'connections' ? 'connections' : menuSection === 'sound' ? 'voice' : 'basic'}
        isOpen={isSettingsOpen}
        onClose={closeMenu}
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
        providerDefaultModel={activeProvider?.default_model}
        credentialRequired={activeProvider?.credential_required !== false}
        baseUrlConfigurable={Boolean(activeProvider?.base_url_configurable)}
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
          // The backend creates it as pending; it joins the chat selector once the check passes or it is enabled by hand.
          const created = await response.json() as { provider_id: string; default_model?: string; configuration_version?: string | null };
          await loadProviders();
          if (!created.default_model) return;
          const result = await runChatCheck(created.provider_id, created.default_model, bumpConfigVersion(created.provider_id));
          if (result?.status === 'ok') await admitProvider(created.provider_id, created.default_model, created.configuration_version);
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
        onSaveSettings={async (settings) => {
          await handleSaveSettings(settings);
          if (menuSection !== 'connections') return;
          // Saving connection details invalidates the old result and verifies the selected model once.
          const version = bumpConfigVersion(activeProviderId);
          void runChatCheck(activeProviderId, settings.model || activeModelId, version);
        }}
        onClearWebSearchCredential={handleClearWebSearchCredential}
      />
        </div>
        </div>
        </div>
      </SystemMenu>

      {shift && (
        <ShiftOverlay
          from={shift.from}
          to={shift.to}
          onCommit={() => onWorldlineChange(shift.to)}
          onDone={() => setShift(null)}
        />
      )}

      {shiftConfirm && (
        <ConfirmDialog
          title="停止本轮并跃迁？"
          confirmLabel="停止本轮并跃迁"
          onConfirm={() => {
            const to = shiftConfirm;
            setShiftConfirm(null);
            // Generation is cancelled outright; her voice is left to startShift, which fades it before stopping.
            if (isThinking || isSynthesizing) cancelTurn();
            startShift(to);
          }}
          onCancel={() => setShiftConfirm(null)}
        >
          <p>她还在回复。跃迁到{shiftConfirm === 'steins_gate' ? ' STEINS;GATE ' : ' β 世界线'}会先停下这一轮回复；取消则留在当前世界线，继续听她说完。</p>
        </ConfirmDialog>
      )}

      {renameTarget && (
        <ConfirmDialog
          title="重命名会话"
          confirmLabel="保存"
          focusCancel={false}
          busy={renameBusy}
          error={renameError}
          onConfirm={() => { void confirmRenameConversation(); }}
          onCancel={() => { setRenameTarget(null); setRenameError(null); }}
        >
          <input
            className="ws2-dialog-input"
            aria-label="会话标题"
            autoFocus
            value={renameValue}
            maxLength={80}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); void confirmRenameConversation(); }
            }}
          />
        </ConfirmDialog>
      )}

      {deleteTarget && (
        <ConfirmDialog
          title="删除这段对话？"
          confirmLabel="删除会话"
          danger
          busy={deleteBusy}
          error={deleteError}
          onConfirm={() => { void confirmDeleteConversation(); }}
          onCancel={() => { setDeleteTarget(null); setDeleteError(null); }}
        >
          <p>「{deleteTarget.title}」的聊天记录将被删除，已形成的长期记忆仍会保留。</p>
          <p className="ws2-dialog-hint">要遗忘记忆，请在 Memory 中管理。</p>
        </ConfirmDialog>
      )}

      {forgetTarget && (
        <ForgetConversationModal
          title={forgetTarget.title}
          busy={conversationBusy}
          error={forgetError}
          onClose={() => { if (!conversationBusy) { setForgetTarget(null); setForgetError(null); } }}
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
