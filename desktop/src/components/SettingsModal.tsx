import React, { useState, useEffect, useRef } from 'react';

import {
  type ModelCatalogEntry,
} from './ProviderControls';
import { SalieriSelect } from './SalieriSelect';
import { packAsset } from '../assetPack';

export type CredentialStatus = 'checking' | 'configured' | 'not_configured' | 'saving' | 'error';

/** Paid web-search backends only (Tavily | Firecrawl). No free HTML fallback. */
export type WebSearchProviderId = 'tavily' | 'firecrawl';

export interface WebSearchProviderInfo {
  id: WebSearchProviderId;
  label: string;
  configured: boolean;
  docs_url?: string;
  hint?: string;
}

export interface WebSearchSettingsSnapshot {
  active_provider: WebSearchProviderId;
  providers: Record<WebSearchProviderId, WebSearchProviderInfo>;
  fallback?: string;
  fallback_note?: string;
  configured?: boolean;
}

/** Chat sampling temperature exposed as Settings「回复随机性」. Not Persona content. */
export const CHAT_TEMPERATURE_MIN = 0;
export const CHAT_TEMPERATURE_MAX = 1.2;
export const CHAT_TEMPERATURE_STEP = 0.1;
/** New-install default; daily stable band is 0–0.5 (0 is the verified safe point). */
export const CHAT_TEMPERATURE_DEFAULT = 0.0;

export function clampChatTemperature(value: number): number {
  if (!Number.isFinite(value)) return CHAT_TEMPERATURE_DEFAULT;
  return Math.max(CHAT_TEMPERATURE_MIN, Math.min(CHAT_TEMPERATURE_MAX, value));
}

/** Q20-A / Q21: conversation identity mode. Never infer from nicknames. */
export type IdentityMode = 'okabe' | 'self';
/** Product default for new conversations (Q20-A). */
export const DEFAULT_IDENTITY_MODE: IdentityMode = 'okabe';
export const IDENTITY_MODE_STORAGE_KEY = 'amadeus_pc_identity_mode';

export function normalizeIdentityMode(value: unknown): IdentityMode {
  return value === 'self' ? 'self' : DEFAULT_IDENTITY_MODE;
}

export function loadStoredIdentityMode(): IdentityMode {
  try {
    return normalizeIdentityMode(localStorage.getItem(IDENTITY_MODE_STORAGE_KEY));
  } catch {
    return DEFAULT_IDENTITY_MODE;
  }
}

export function persistIdentityMode(mode: IdentityMode): void {
  try {
    localStorage.setItem(IDENTITY_MODE_STORAGE_KEY, normalizeIdentityMode(mode));
  } catch {
    /* ignore quota / private mode */
  }
}

/* --- Slice B: self-mode call name (application-level config, not memory) --- */

export const SELF_NAME_MAX_CODEPOINTS = 40;
export const SELF_NAME_STORAGE_KEY = 'amadeus_pc_self_name';
export const RESERVED_OKABE_NAME_HINT = '如需使用冈部身份，请选择 okabe 模式。';
export const SELF_NAME_TOO_LONG_HINT = `称呼最多 ${SELF_NAME_MAX_CODEPOINTS} 个字符。`;
export const SELF_NAME_INVALID_CHARS_HINT =
  "称呼只能包含字母、数字、空格及 - ' · ・ . 分隔符。";

// Rework contract: self_name is a NAME, not free text. Allowlist only — Unicode
// letters/digits, plain space, and common name separators - ' · ・ .
// Everything else (quotes, brackets, colons, slashes, underscores, control or
// format chars, U+2028/U+2029, emoji, symbols) fails with an explicit error;
// no silent repair. Mirrors backend prompt_compiler allowlist exactly.
const NAME_ALLOWED_RE = /^[\p{L}\p{Nd} \-'.\u00b7\u30fb]+$/u;

// Separators/punctuation ignored when comparing against reserved Okabe names
// (mirrors backend prompt_compiler._NAME_SEPARATORS_RE).
const NAME_SEPARATORS_RE = /[\s\u3000\u30fb.,\u00b7\-_'\u2019"\u201c\u201d\u300c\u300d\u300e\u300f()\uff08\uff09!\uff01?\uff1f~\uff5e*\u00d7+|/\\:;\uff1a\uff1b]/g;

// Q21 guard: names that would blur self mode into the Okabe identity.
// MUST stay identical to _RESERVED_OKABE_NAMES in backend prompt_compiler.py.
const RESERVED_OKABE_NAMES = new Set([
  '岡部', '岡部倫太郎', '冈部', '冈部伦太郎',
  '鳳凰院凶真', '凤凰院凶真',
  'okabe', 'rintarookabe', 'okaberintaro', 'hououinkyouma', 'kyoumahououin',
]);

function normalizeNameForGuard(value: string): string {
  return value.normalize('NFKC').toLowerCase().replace(NAME_SEPARATORS_RE, '');
}

export function isReservedOkabeName(value: string): boolean {
  return RESERVED_OKABE_NAMES.has(normalizeNameForGuard(value));
}

export type SelfNameValidation =
  | { ok: true; value: string }
  | { ok: false; error: string };

/** Validate the call name; empty means "unset" and is valid.
 *  Order mirrors backend sanitize_self_name: edge-trim (plain space U+0020 ONLY)
 *  -> length -> reserved -> allowlist. Edge newlines/tabs/NBSP/BOM are NOT
 *  trimmed — they reject the whole name via the allowlist (no silent repair). */
export function validateSelfName(raw: unknown): SelfNameValidation {
  if (typeof raw !== 'string') return { ok: true, value: '' };
  const name = raw.replace(/^\u0020+|\u0020+$/g, '');
  if (!name) return { ok: true, value: '' };
  if (Array.from(name).length > SELF_NAME_MAX_CODEPOINTS) {
    return { ok: false, error: SELF_NAME_TOO_LONG_HINT };
  }
  if (isReservedOkabeName(name)) {
    return { ok: false, error: RESERVED_OKABE_NAME_HINT };
  }
  if (!NAME_ALLOWED_RE.test(name)) {
    return { ok: false, error: SELF_NAME_INVALID_CHARS_HINT };
  }
  return { ok: true, value: name };
}

export function loadStoredSelfName(): string {
  try {
    const stored = localStorage.getItem(SELF_NAME_STORAGE_KEY) || '';
    const checked = validateSelfName(stored);
    return checked.ok ? checked.value : '';
  } catch {
    return '';
  }
}

export function persistSelfName(name: string): void {
  try {
    if (name) {
      localStorage.setItem(SELF_NAME_STORAGE_KEY, name);
    } else {
      localStorage.removeItem(SELF_NAME_STORAGE_KEY);
    }
  } catch {
    /* ignore quota / private mode */
  }
}

/** Confirm copy when creating a conversation with an explicit mode (memory isolation). */
export function identityModeNewConversationConfirm(mode: IdentityMode): string {
  const label = mode === 'self' ? 'Self' : 'Okabe';
  return (
    `将以 ${label} 模式新建会话。\n` +
    'Self 与 Okabe 的记忆与关系不会合并；开始后不可在本会话内切换模式。'
  );
}

export type SettingsTab = 'basic' | 'connections' | 'voice';

export interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** embedded: render one tab's fields inside the workstation system menu (no overlay, no tab row). */
  variant?: 'modal' | 'embedded';
  /** Controlled tab for the embedded variant. */
  tab?: SettingsTab;
  worldline: 'steins_gate' | 'beta';
  /** Gate 7A: durable session id for the Memory Ledger (read-only wiring). */
  sessionId?: string;
  apiKey: string;
  credentialConfigured: boolean;
  credentialStatus?: CredentialStatus;
  webSearchApiKey?: string;
  webSearchConfigured?: boolean;
  webSearchStatus?: CredentialStatus;
  webSearchActiveProvider?: WebSearchProviderId;
  webSearchProviders?: Partial<Record<WebSearchProviderId, WebSearchProviderInfo>>;
  webSearchFallbackNote?: string;
  systemPrompt: string;
  enableTts: boolean;
  sovitsUrl: string;
  enableBgm: boolean;
  bgmVolume: number;
  enableSfx: boolean;
  sfxVolume: number;
  temperature: number;
  /** One-shot notice after migrating a legacy localStorage value > max (1.2) */
  temperatureMigrationNotice?: boolean;
  onDismissTemperatureMigrationNotice?: () => void;
  /** Default for newly created conversations (not the active conversation's mode). */
  identityMode?: IdentityMode;
  /** Slice B: app-level call name for self conversations (empty = unset). */
  selfName?: string;
  providerDisplayName: string;
  providerBaseUrl?: string | null;
  /** The connection's own default model; the session model may belong to another provider. */
  providerDefaultModel?: string | null;
  /** false for local connections that take no key. */
  credentialRequired?: boolean;
  baseUrlConfigurable: boolean;
  modelOptions: ModelCatalogEntry[];
  reasoningEffort: string | null;
  /** Enabled OpenAI-compatible connections for multi-profile management. */
  customProfiles?: Array<{
    id: string;
    display_name: string;
    default_model: string;
    base_url?: string | null;
    enabled?: boolean;
    configured?: boolean;
  }>;
  onCreateCustomProfile?: (input: {
    display_name: string;
    base_url: string;
    default_model: string;
  }) => void | Promise<void>;
  onDisableCustomProfile?: (providerId: string) => void | Promise<void>;
  onSaveSettings: (settings: {
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
  }) => void | Promise<void>;
  onSelectWebSearchProvider?: (provider: WebSearchProviderId) => void | Promise<void>;
  onClearWebSearchCredential?: (provider: WebSearchProviderId) => void | Promise<void>;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  variant = 'modal',
  tab,
  worldline,
  apiKey,
  credentialConfigured,
  credentialStatus,
  webSearchApiKey = '',
  webSearchConfigured = false,
  webSearchStatus,
  webSearchActiveProvider = 'tavily',
  webSearchProviders,
  webSearchFallbackNote: _webSearchFallbackNote = '请至少配置一个搜索提供商并设为当前使用。',
  systemPrompt,
  enableTts,
  sovitsUrl,
  enableBgm,
  bgmVolume,
  enableSfx,
  sfxVolume,
  temperature,
  temperatureMigrationNotice = false,
  onDismissTemperatureMigrationNotice,
  identityMode = DEFAULT_IDENTITY_MODE,
  selfName = '',
  providerDisplayName,
  providerBaseUrl,
  providerDefaultModel,
  credentialRequired = true,
  baseUrlConfigurable,
  modelOptions: _modelOptions,
  reasoningEffort,
  customProfiles = [],
  onCreateCustomProfile,
  onDisableCustomProfile,
  onSaveSettings,
  onSelectWebSearchProvider,
  onClearWebSearchCredential,
}) => {
  const embedded = variant === 'embedded';
  const [ownTab, setActiveTab] = useState<SettingsTab>('basic');
  const activeTab: SettingsTab = embedded ? (tab ?? 'basic') : ownTab;
  const [savedFlash, setSavedFlash] = useState(false);
  const [localWorldline, setLocalWorldline] = useState<'steins_gate' | 'beta'>(worldline);
  const [localApiKey, setLocalApiKey] = useState(apiKey);
  const [localWebSearchApiKey, setLocalWebSearchApiKey] = useState(webSearchApiKey);
  const [localWebSearchProvider, setLocalWebSearchProvider] = useState<WebSearchProviderId>(webSearchActiveProvider);
  const [replacingWebSearchKey, setReplacingWebSearchKey] = useState(false);
  const [localSystemPrompt, setLocalSystemPrompt] = useState(systemPrompt);
  const [localEnableTts, setLocalEnableTts] = useState(enableTts);
  const [localSovitsUrl, setLocalSovitsUrl] = useState(sovitsUrl);
  const [localTemperature, setLocalTemperature] = useState(() => clampChatTemperature(temperature));
  const [localIdentityMode, setLocalIdentityMode] = useState<IdentityMode>(() =>
    normalizeIdentityMode(identityMode),
  );
  const [localSelfName, setLocalSelfName] = useState(selfName);
  const [selfNameError, setSelfNameError] = useState<string | null>(null);
  const [newProfileName, setNewProfileName] = useState('');
  const [newProfileBaseUrl, setNewProfileBaseUrl] = useState('http://127.0.0.1:1234/v1');
  const [newProfileModel, setNewProfileModel] = useState('local-model');
  const [profileBusy, setProfileBusy] = useState(false);
  const [showCreateProfileForm, setShowCreateProfileForm] = useState(false);
  const [showDisabledProfiles, setShowDisabledProfiles] = useState(false);
  const [expandedProfileDetails, setExpandedProfileDetails] = useState<Record<string, boolean>>({});
  /** Optimistic hide after successful delete until parent snapshot catches up. */
  const [removedProfileIds, setRemovedProfileIds] = useState<Record<string, true>>({});
  /** Row-level failures stay on the row: the page-level alert sits far below a long connection list. */
  const [profileError, setProfileError] = useState<{ id: string; message: string } | null>(null);
  // Retained for Workstation prop contract; model pick lives on the bottom bar.
  void _modelOptions;
  const [localBaseUrl, setLocalBaseUrl] = useState(providerBaseUrl || '');
  const [localModel, setLocalModel] = useState(providerDefaultModel || '');
  const [localReasoningEffort, setLocalReasoningEffort] = useState(
    reasoningEffort,
  );
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [isClearingWebSearch, setIsClearingWebSearch] = useState(false);
  const [isSwitchingSearchProvider, setIsSwitchingSearchProvider] = useState(false);
  /** True only while the modal has already completed an open-edge init. */
  const wasOpenRef = useRef(false);
  // In-flight save/clear always wins over the parent snapshot status.
  const displayCredentialStatus: CredentialStatus = isSaving
    ? 'saving'
    : (credentialStatus ?? (credentialConfigured ? 'configured' : 'not_configured'));
  const activeSearchMeta = webSearchProviders?.[localWebSearchProvider];
  const activeSearchConfigured = Boolean(activeSearchMeta?.configured ?? webSearchConfigured);
  const displayWebSearchStatus: CredentialStatus =
    (isSaving || isClearingWebSearch || isSwitchingSearchProvider)
      ? 'saving'
      : (webSearchStatus ?? (activeSearchConfigured ? 'configured' : 'not_configured'));
  const temperatureWarning =
    localTemperature >= 0.9
      ? 'strong' as const
      : localTemperature >= 0.8
        ? 'mild' as const
        : null;
  const searchProviderCards: WebSearchProviderInfo[] = (
    ['tavily', 'firecrawl'] as const
  ).map((id) => webSearchProviders?.[id] ?? {
    id,
    label: id === 'tavily' ? 'Tavily' : 'Firecrawl',
    configured: id === localWebSearchProvider ? activeSearchConfigured : false,
    docs_url: id === 'tavily' ? 'https://app.tavily.com/home' : 'https://www.firecrawl.dev/app/api-keys',
    hint: id === 'tavily'
      ? '面向 AI 检索；免费额度见官方控制台。'
      : 'Search + 页面内容；免费额度见官方控制台。',
  });
  const activeSearchLabel =
    searchProviderCards.find((c) => c.id === localWebSearchProvider)?.label ?? localWebSearchProvider;
  // Clean soft-deletes leave the UI; only active rows and credential-pending rows remain.
  const enabledCustomProfiles = customProfiles.filter(
    (p) => p.enabled !== false && !removedProfileIds[p.id],
  );
  const pendingCleanupProfiles = customProfiles.filter(
    (p) => p.enabled === false && p.configured === true,
  );
  const enabledCustomCount = enabledCustomProfiles.length;
  const pendingCleanupCount = pendingCleanupProfiles.length;

  // Initialize local draft state only on the closed→open edge. Parent prop
  // updates while open must not reset tab, unsaved fields, or fold UI.
  useEffect(() => {
    if (!isOpen) {
      wasOpenRef.current = false;
      return;
    }
    if (wasOpenRef.current) {
      return;
    }
    wasOpenRef.current = true;
    setActiveTab('basic');
    setLocalWorldline(worldline);
    setLocalApiKey(apiKey);
    setLocalWebSearchApiKey(webSearchApiKey);
    setLocalWebSearchProvider(webSearchActiveProvider);
    setReplacingWebSearchKey(false);
    setSaveError(null);
    setLocalSystemPrompt(systemPrompt);
    setLocalEnableTts(enableTts);
    setLocalSovitsUrl(sovitsUrl);
    setLocalTemperature(clampChatTemperature(temperature));
    setLocalIdentityMode(normalizeIdentityMode(identityMode));
    setLocalSelfName(selfName);
    setSelfNameError(null);
    setLocalBaseUrl(providerBaseUrl || '');
    setLocalModel(providerDefaultModel || '');
    setLocalReasoningEffort(reasoningEffort);
    setShowCreateProfileForm(false);
    setShowDisabledProfiles(false);
    setExpandedProfileDetails({});
    setRemovedProfileIds({});
    setProfileError(null);
    setNewProfileName('');
    setNewProfileBaseUrl('http://127.0.0.1:1234/v1');
    setNewProfileModel('local-model');
  }, [isOpen, worldline, apiKey, webSearchApiKey, webSearchActiveProvider, systemPrompt, enableTts, sovitsUrl, temperature, identityMode, selfName, providerBaseUrl, providerDefaultModel, reasoningEffort]);

  if (!isOpen) return null;

  const handleSave = async () => {
    if (isSaving) return;
    // Slice B: reserved/overlong call names never save and never reach the backend.
    const selfNameCheck = validateSelfName(localSelfName);
    if (!selfNameCheck.ok) {
      setSelfNameError(selfNameCheck.error);
      return;
    }
    setSelfNameError(null);
    setIsSaving(true);
    setSaveError(null);
    try {
      await onSaveSettings({
        // Embedded fields never own the worldline: the menu switches it separately.
        worldline: embedded ? worldline : localWorldline,
        apiKey: localApiKey,
        webSearchApiKey: localWebSearchApiKey,
        webSearchProvider: localWebSearchProvider,
        systemPrompt: localSystemPrompt,
        enableTts: localEnableTts,
        sovitsUrl: localSovitsUrl,
        enableBgm: enableBgm,
        bgmVolume: bgmVolume,
        enableSfx: enableSfx,
        sfxVolume: sfxVolume,
        temperature: clampChatTemperature(localTemperature),
        identityMode: normalizeIdentityMode(localIdentityMode),
        selfName: selfNameCheck.value,
        baseUrl: localBaseUrl,
        // Only an edited「默认模型」field is a model change; otherwise a stale snapshot would overwrite
        // the live selection (seen as a DeepSeek model paired with a custom connection).
        model: baseUrlConfigurable && localModel.trim() && localModel.trim() !== (providerDefaultModel || '') ? localModel.trim() : '',
        reasoningEffort: localReasoningEffort,
      });
      setReplacingWebSearchKey(false);
      if (embedded) {
        setSavedFlash(true);
        window.setTimeout(() => setSavedFlash(false), 1800);
      } else {
        onClose();
      }
    } catch (error) {
      const code = error instanceof Error ? error.message : 'settings_save_failed';
      setSaveError(code);
    } finally {
      setIsSaving(false);
    }
  };

  const handleSelectSearchProvider = async (provider: WebSearchProviderId) => {
    if (provider === localWebSearchProvider || isSwitchingSearchProvider) return;
    setLocalWebSearchProvider(provider);
    setLocalWebSearchApiKey('');
    setReplacingWebSearchKey(false);
    if (!onSelectWebSearchProvider) return;
    setIsSwitchingSearchProvider(true);
    setSaveError(null);
    try {
      await onSelectWebSearchProvider(provider);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : 'web_search_provider_switch_failed');
    } finally {
      setIsSwitchingSearchProvider(false);
    }
  };

  const handleClearWebSearchCredential = async () => {
    if (!onClearWebSearchCredential || isClearingWebSearch) return;
    setIsClearingWebSearch(true);
    setSaveError(null);
    try {
      await onClearWebSearchCredential(localWebSearchProvider);
      setLocalWebSearchApiKey('');
      setReplacingWebSearchKey(false);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : 'web_search_credential_clear_failed');
    } finally {
      setIsClearingWebSearch(false);
    }
  };

  const chrome = (content: React.ReactNode) => (embedded ? (
    <div className="settings-embedded" data-testid="settings-embedded">{content}</div>
  ) : (
    <div className="salieri-modal-overlay">
      <div className="salieri-settings-modal" data-testid="settings-modal-shell">
        {/* Rotating ring is clipped to modal bounds so it cannot expand a scrollport. */}
        <div className="config-ring-clip" aria-hidden="true">
          <div className="config-ring-bg">
            {packAsset('assets/ui/ring_config.png') && <img src={packAsset('assets/ui/ring_config.png') ?? ''} alt="" />}
          </div>
        </div>
        <div className="modal-inner-content" data-testid="settings-modal-inner">
          <div className="modal-header">
            <h2>SYSTEM CONFIG</h2>
            <button className="close-btn" onClick={onClose}>×</button>
          </div>
          <nav className="tab-pills-row">
            <button aria-label="BASIC" className={`tab-pill ${activeTab === 'basic' ? 'active' : ''}`} onClick={() => setActiveTab('basic')}>
              BASIC
            </button>
            <button aria-label="CONNECTIONS" className={`tab-pill ${activeTab === 'connections' ? 'active' : ''}`} onClick={() => setActiveTab('connections')}>
              CONNECTIONS
            </button>
            <button aria-label="VOICE" className={`tab-pill ${activeTab === 'voice' ? 'active' : ''}`} onClick={() => setActiveTab('voice')}>
              VOICE
            </button>
          </nav>
          {content}
        </div>
      </div>
    </div>
  ));

  return chrome(
    <>
          <main className="tab-panels">
            {/* BASIC tab — session defaults only */}
            {activeTab === 'basic' && (
            <section className="tab-panel active" data-testid="settings-panel-basic">
              <div className="setting-field personality-field">
                <label>回复随机性</label>
                <p className="setting-field-hint" data-testid="temperature-hint">
                  越高越发散、越容易跑题；不改变角色设定本身。默认 0（最稳）；需要轻微变化可用 0.1～0.3。
                </p>
                {temperatureMigrationNotice && (
                  <p
                    className="temperature-migration-notice"
                    data-testid="temperature-migration-notice"
                    role="status"
                  >
                    已从过高采样降至安全上限 1.2。
                    {onDismissTemperatureMigrationNotice && (
                      <button
                        type="button"
                        className="temperature-migration-dismiss"
                        onClick={onDismissTemperatureMigrationNotice}
                      >
                        知道了
                      </button>
                    )}
                  </p>
                )}
                <div className="personality-control" data-testid="personality-control">
                  <div className="slider-row personality-slider-row">
                    <input
                      data-testid="personality-range"
                      type="range"
                      min={CHAT_TEMPERATURE_MIN}
                      max={CHAT_TEMPERATURE_MAX}
                      step={CHAT_TEMPERATURE_STEP}
                      value={localTemperature}
                      onChange={(e) => setLocalTemperature(clampChatTemperature(parseFloat(e.target.value)))}
                      className="volume-slider personality-range"
                      style={{ '--val': `${(localTemperature / CHAT_TEMPERATURE_MAX) * 100}%` } as React.CSSProperties}
                      aria-label="回复随机性"
                    />
                    <span className="volume-label personality-value" data-testid="personality-value">
                      {localTemperature.toFixed(1)}
                    </span>
                  </div>
                  <div className="personality-endpoints" aria-hidden="true">
                    <span className="slider-endpoint" data-testid="temperature-endpoint-min">稳定 / 贴人设</span>
                    <span className="slider-endpoint" data-testid="temperature-endpoint-max">发散 / 易跑偏</span>
                  </div>
                </div>
                {temperatureWarning === 'mild' && (
                  <p className="temperature-warning temperature-warning-mild" data-testid="temperature-warning">
                    当前采样偏高，回复可能不够稳定。
                  </p>
                )}
                {temperatureWarning === 'strong' && (
                  <p className="temperature-warning temperature-warning-strong" data-testid="temperature-warning">
                    当前采样很高，容易跑题或句式不稳。
                  </p>
                )}
              </div>
              {!embedded && <div className="setting-field">
                <label>当前世界线</label>
                <SalieriSelect
                  aria-label="当前世界线"
                  className="settings-salieri-select"
                  triggerClassName="salieri-select-trigger settings-select-trigger"
                  value={localWorldline}
                  onChange={(next) => setLocalWorldline(next as 'steins_gate' | 'beta')}
                  options={[
                    { value: 'steins_gate', label: 'Steins;Gate (1.048596%)' },
                    { value: 'beta', label: 'β 世界线 (1.129848%)' },
                  ]}
                />
              </div>}
              <div className="setting-field field-full" data-testid="identity-settings">
                <label>新会话默认身份模式</label>
                <div className="identity-mode-options" role="radiogroup" aria-label="新会话默认身份模式">
                  <label className="checkbox-label identity-mode-option">
                    <input
                      type="radio"
                      name="identity-mode"
                      value="okabe"
                      data-testid="identity-mode-okabe"
                      checked={localIdentityMode === 'okabe'}
                      onChange={() => setLocalIdentityMode('okabe')}
                      className="salieri-checkbox"
                    />
                    冈部（默认）
                  </label>
                  <label className="checkbox-label identity-mode-option">
                    <input
                      type="radio"
                      name="identity-mode"
                      value="self"
                      data-testid="identity-mode-self"
                      checked={localIdentityMode === 'self'}
                      onChange={() => setLocalIdentityMode('self')}
                      className="salieri-checkbox"
                    />
                    自己
                  </label>
                </div>
              </div>
              <div className="setting-field field-full" data-testid="self-name-settings">
                <label htmlFor="self-name-input">self 模式称呼</label>
                <input
                  id="self-name-input"
                  data-testid="self-name-input"
                  type="text"
                  className="salieri-input"
                  value={localSelfName}
                  placeholder="留空表示不设置"
                  onChange={(e) => {
                    setLocalSelfName(e.target.value);
                    setSelfNameError(null);
                  }}
                />
                {selfNameError && (
                  <p className="settings-save-error self-name-error" role="alert">
                    {selfNameError}
                  </p>
                )}
              </div>
            </section>
            )}

            {/* CONNECTIONS tab — credentials, custom profiles, web search */}
            {activeTab === 'connections' && (
            <section
              className="tab-panel tab-panel-connections active"
              data-testid="settings-panel-connections"
            >
              <div className="connections-summary" data-testid="connections-summary">
                <span className="connections-status-chip">
                  {providerDisplayName} · {!credentialRequired ? '无需密钥' : displayCredentialStatus === 'configured' ? '密钥已配置' : '密钥未配置'}
                </span>
                <span className="connections-status-chip">
                  搜索 · {activeSearchLabel}
                  {' '}({displayWebSearchStatus === 'configured' ? '已连接' : '未配置'})
                </span>
                <span className="connections-status-chip">
                  自定义 · {enabledCustomCount} 启用
                  {pendingCleanupCount > 0 ? ` / ${pendingCleanupCount} 待清理` : ''}
                </span>
              </div>

              <div className="connections-section credential-card" data-testid="connections-provider-credentials">
                <div className="connections-section-title">模型供应商凭据</div>
                <div className="credential-card-header">
                  <label>{providerDisplayName.toUpperCase()} API 密钥</label>
                  <span
                    className={`credential-badge ${credentialRequired ? displayCredentialStatus.replace('_', '-') : 'not-required'}`}
                    data-testid="credential-badge"
                  >
                    {!credentialRequired && '无需密钥'}
                    {credentialRequired && displayCredentialStatus === 'configured' && '已保存'}
                    {credentialRequired && displayCredentialStatus === 'not_configured' && '未配置'}
                    {credentialRequired && displayCredentialStatus === 'checking' && '检查中'}
                    {credentialRequired && displayCredentialStatus === 'saving' && '保存中'}
                    {credentialRequired && displayCredentialStatus === 'error' && '失败'}
                  </span>
                </div>
                {!credentialRequired ? (
                  <p className="setting-field-hint" data-testid="credential-status">
                    这个接入不需要密钥 · 是否可用以上方连接测试为准
                  </p>
                ) : (<>
                <p className="setting-field-hint">
                  密钥只写不读：空表示不改动；粘贴新密钥后点「保存连接设置」。
                </p>
                <input
                  type="password"
                  value={localApiKey}
                  onChange={(e) => setLocalApiKey(e.target.value)}
                  className="salieri-input connections-secret-input"
                  data-testid="credential-input"
                  placeholder={displayCredentialStatus === 'configured' ? '密钥已保存 · 粘贴新密钥以更换' : '粘贴 API 密钥（仅本机存储）'}
                  autoComplete="new-password"
                  spellCheck={false}
                />
                <p
                  className={`credential-status ${displayCredentialStatus.replace('_', '-')}`}
                  data-testid="credential-status"
                  aria-live="polite"
                >
                  {displayCredentialStatus === 'checking' && '正在读取凭据状态…'}
                  {displayCredentialStatus === 'configured' && '密钥已保存 · 是否可用以上方连接测试为准'}
                  {displayCredentialStatus === 'not_configured' && '尚未配置 · 保存后不会在页面回显密钥'}
                  {displayCredentialStatus === 'saving' && '保存中…'}
                  {displayCredentialStatus === 'error' && '保存失败 · 请检查连接后重试'}
                </p>
                </>)}
                {baseUrlConfigurable && (
                  <div className="connections-base-url-fields">
                    <div className="setting-field">
                      <label>Base URL</label>
                      <input type="url" value={localBaseUrl} onChange={(e) => setLocalBaseUrl(e.target.value)} className="salieri-input connections-wide-input" placeholder="http://127.0.0.1:1234/v1" />
                    </div>
                    <div className="setting-field">
                      <label>默认模型</label>
                      <input type="text" value={localModel} onChange={(e) => setLocalModel(e.target.value)} className="salieri-input connections-wide-input" placeholder="local-model" />
                    </div>
                  </div>
                )}
              </div>

              {(onCreateCustomProfile || enabledCustomProfiles.length > 0 || pendingCleanupProfiles.length > 0) && (() => {
                const renderProfileRow = (profile: (typeof customProfiles)[number]) => {
                  const disabled = profile.enabled === false;
                  const credentialPending = disabled && profile.configured === true;
                  const detailsOpen = Boolean(expandedProfileDetails[profile.id]);
                  return (
                    <li key={profile.id} className={`custom-profile-row ${disabled ? 'is-disabled' : ''}`}>
                      <div className="custom-profile-row-main">
                        <div className="custom-profile-row-head">
                          <strong>{profile.display_name}</strong>
                          {!disabled && (
                            <span className="custom-profile-pill">启用中</span>
                          )}
                          {credentialPending && (
                            <span className="custom-profile-pill is-warn">凭据待清理</span>
                          )}
                        </div>
                        <div className="custom-profile-meta">
                          <span className="custom-profile-meta-item" title={profile.base_url || undefined}>
                            {profile.base_url || '—'}
                          </span>
                          <span className="custom-profile-meta-sep">·</span>
                          <span className="custom-profile-meta-item" title={profile.default_model}>
                            模型 {profile.default_model}
                          </span>
                        </div>
                        {credentialPending && (
                          <small
                            className="custom-profile-credential-warn"
                            data-testid={`profile-credential-pending-${profile.id}`}
                          >
                            已停用 · 凭据待清理
                          </small>
                        )}
                        {profileError?.id === profile.id && (
                          <p className="settings-save-error profile-row-error" role="alert">{profileError.message}</p>
                        )}
                        <div className="custom-profile-row-foot">
                          <button
                            type="button"
                            className="profile-details-toggle"
                            onClick={() => setExpandedProfileDetails((prev) => ({
                              ...prev,
                              [profile.id]: !prev[profile.id],
                            }))}
                          >
                            {detailsOpen ? '收起详情' : '详细信息'}
                          </button>
                          {detailsOpen && (
                            <small className="profile-uuid" data-testid={`profile-id-${profile.id}`}>
                              {profile.id}
                            </small>
                          )}
                        </div>
                      </div>
                      <div className="custom-profile-row-actions">
                        {onDisableCustomProfile && !disabled && (
                          <button
                            type="button"
                            className="profile-action-delete"
                            data-testid={`profile-delete-${profile.id}`}
                            disabled={profileBusy}
                            aria-label={`删除连接 ${profile.display_name}`}
                            onClick={async () => {
                              setProfileBusy(true);
                              setProfileError(null);
                              try {
                                await onDisableCustomProfile(profile.id);
                                // Drop from the list immediately; clean soft-deletes stay hidden.
                                setRemovedProfileIds((prev) => ({ ...prev, [profile.id]: true }));
                                setExpandedProfileDetails((prev) => {
                                  if (!prev[profile.id]) return prev;
                                  const next = { ...prev };
                                  delete next[profile.id];
                                  return next;
                                });
                              } catch {
                                setProfileError({ id: profile.id, message: '删除失败，这个接入仍然保留。请稍后重试。' });
                              } finally {
                                setProfileBusy(false);
                              }
                            }}
                          >
                            删除
                          </button>
                        )}
                        {credentialPending && onDisableCustomProfile && (
                          <button
                            type="button"
                            className="profile-action-retry"
                            data-testid={`profile-credential-retry-${profile.id}`}
                            disabled={profileBusy}
                            onClick={async () => {
                              setProfileBusy(true);
                              setProfileError(null);
                              try {
                                await onDisableCustomProfile(profile.id);
                              } catch {
                                setProfileError({ id: profile.id, message: '凭据清理失败，请稍后重试。' });
                              } finally {
                                setProfileBusy(false);
                              }
                            }}
                          >
                            重试清理
                          </button>
                        )}
                      </div>
                    </li>
                  );
                };
                return (
                  <div className="connections-section" data-testid="custom-profile-manager">
                    <div className="connections-section-title">OpenAI-compatible 连接</div>
                    {enabledCustomProfiles.length > 0 && (
                      <ul className="custom-profile-list">
                        {enabledCustomProfiles.map(renderProfileRow)}
                      </ul>
                    )}
                    {enabledCustomProfiles.length === 0 && pendingCleanupProfiles.length === 0 && (
                      <p className="setting-hint connections-inline-hint">暂无自定义连接</p>
                    )}
                    {pendingCleanupProfiles.length > 0 && (
                      <div className="disabled-profiles-fold">
                        <button
                          type="button"
                          className="disabled-profiles-toggle"
                          data-testid="disabled-profiles-toggle"
                          aria-expanded={showDisabledProfiles}
                          onClick={() => setShowDisabledProfiles((v) => !v)}
                        >
                          凭据待清理（{pendingCleanupProfiles.length}）
                        </button>
                        {showDisabledProfiles && (
                          <ul className="custom-profile-list custom-profile-list-disabled">
                            {pendingCleanupProfiles.map(renderProfileRow)}
                          </ul>
                        )}
                      </div>
                    )}
                    {onCreateCustomProfile && (
                      <div className="custom-profile-create-wrap">
                        {!showCreateProfileForm ? (
                          <button
                            type="button"
                            className="salieri-btn primary custom-profile-create-open"
                            onClick={() => setShowCreateProfileForm(true)}
                          >
                            新建连接
                          </button>
                        ) : (
                          <div className="custom-profile-create">
                            <div className="setting-field">
                              <label>显示名称</label>
                              <input
                                type="text"
                                className="salieri-input connections-wide-input"
                                placeholder="显示名称"
                                value={newProfileName}
                                onChange={(e) => setNewProfileName(e.target.value)}
                              />
                            </div>
                            <div className="setting-field">
                              <label>Base URL</label>
                              <input
                                type="url"
                                className="salieri-input connections-wide-input"
                                placeholder="http://127.0.0.1:1234/v1"
                                value={newProfileBaseUrl}
                                onChange={(e) => setNewProfileBaseUrl(e.target.value)}
                              />
                            </div>
                            <div className="setting-field">
                              <label>默认模型</label>
                              <input
                                type="text"
                                className="salieri-input connections-wide-input"
                                placeholder="local-model"
                                value={newProfileModel}
                                onChange={(e) => setNewProfileModel(e.target.value)}
                              />
                            </div>
                            <div className="custom-profile-create-actions">
                              <button
                                type="button"
                                className="salieri-btn cancel-btn"
                                data-testid="cancel-create-profile"
                                disabled={profileBusy}
                                onClick={() => {
                                  setShowCreateProfileForm(false);
                                  setNewProfileName('');
                                  setNewProfileBaseUrl('http://127.0.0.1:1234/v1');
                                  setNewProfileModel('local-model');
                                }}
                              >
                                取消
                              </button>
                              <button
                                type="button"
                                className="salieri-btn primary"
                                disabled={profileBusy || !newProfileName.trim() || !newProfileBaseUrl.trim() || !newProfileModel.trim()}
                                onClick={async () => {
                                  setProfileBusy(true);
                                  setSaveError(null);
                                  try {
                                    await onCreateCustomProfile({
                                      display_name: newProfileName.trim(),
                                      base_url: newProfileBaseUrl.trim(),
                                      default_model: newProfileModel.trim(),
                                    });
                                    setNewProfileName('');
                                    setShowCreateProfileForm(false);
                                  } catch (error) {
                                    setSaveError(
                                      error instanceof Error ? error.message : 'profile_create_failed',
                                    );
                                  } finally {
                                    setProfileBusy(false);
                                  }
                                }}
                              >
                                {profileBusy ? (embedded ? '正在连接…' : '创建中…') : (embedded ? '保存并启用' : '创建')}
                              </button>
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })()}

              <div className="connections-section web-search-setting" data-testid="connections-web-search">
                <div className="connections-section-title">联网搜索</div>
                <p className="connections-web-search-status" data-testid="web-search-hint">
                  当前：{activeSearchLabel} · {
                    displayWebSearchStatus === 'configured' ? '已连接'
                      : displayWebSearchStatus === 'checking' ? '检查中'
                        : displayWebSearchStatus === 'saving' ? '处理中'
                          : displayWebSearchStatus === 'error' ? '失败'
                            : '未配置'
                  }
                  <span
                    className={`credential-badge ${displayWebSearchStatus.replace('_', '-')}`}
                    data-testid="web-search-badge"
                  >
                    {displayWebSearchStatus === 'configured' && '已连接'}
                    {displayWebSearchStatus === 'not_configured' && '未配置'}
                    {displayWebSearchStatus === 'checking' && '检查中'}
                    {displayWebSearchStatus === 'saving' && '保存中'}
                    {displayWebSearchStatus === 'error' && '失败'}
                  </span>
                  <span className="connections-web-search-ops-note">
                    切换即时生效；密钥用「保存连接设置」写入。
                  </span>
                </p>
                <div className="search-provider-grid" data-testid="web-search-provider-grid" role="radiogroup" aria-label="搜索提供商">
                  {searchProviderCards.map((card) => {
                    const selected = localWebSearchProvider === card.id;
                    return (
                      <button
                        key={card.id}
                        type="button"
                        role="radio"
                        aria-checked={selected}
                        className={`search-provider-card ${selected ? 'active' : ''} ${card.configured ? 'configured' : ''}`}
                        data-testid={`web-search-provider-${card.id}`}
                        onClick={() => { void handleSelectSearchProvider(card.id); }}
                        disabled={isSaving || isClearingWebSearch || isSwitchingSearchProvider}
                      >
                        <span className="search-provider-name">{card.label}</span>
                        <span className="search-provider-state">
                          {card.configured ? '密钥已存' : '未存密钥'}
                          {selected ? ' · 当前使用' : ''}
                        </span>
                        {card.docs_url && (
                          <a
                            className="search-provider-docs"
                            href={card.docs_url}
                            target="_blank"
                            rel="noreferrer"
                            onClick={(e) => e.stopPropagation()}
                          >
                            获取密钥
                          </a>
                        )}
                      </button>
                    );
                  })}
                </div>
                {activeSearchConfigured && !replacingWebSearchKey ? (
                  <div className="credential-actions">
                    <p className={`credential-status configured`} data-testid="web-search-credential-status" aria-live="polite">
                      {activeSearchLabel} 已连接 · 密钥不回显
                    </p>
                    <button
                      type="button"
                      className="salieri-btn cancel-btn"
                      data-testid="replace-web-search-credential"
                      onClick={() => setReplacingWebSearchKey(true)}
                      disabled={isSaving || isClearingWebSearch}
                    >
                      更换密钥
                    </button>
                    {onClearWebSearchCredential && (
                      <button
                        type="button"
                        className="salieri-btn cancel-btn"
                        data-testid="clear-web-search-credential"
                        onClick={() => { void handleClearWebSearchCredential(); }}
                        disabled={isSaving || isClearingWebSearch}
                      >
                        清除此提供商密钥
                      </button>
                    )}
                  </div>
                ) : (
                  <>
                    <input
                      type="password"
                      value={localWebSearchApiKey}
                      onChange={(e) => setLocalWebSearchApiKey(e.target.value)}
                      className="salieri-input connections-secret-input"
                      data-testid="web-search-credential-input"
                      placeholder={`粘贴 ${activeSearchLabel} API 密钥`}
                      autoComplete="new-password"
                      spellCheck={false}
                    />
                    <p className={`credential-status ${displayWebSearchStatus.replace('_', '-')}`} data-testid="web-search-credential-status" aria-live="polite">
                      {displayWebSearchStatus === 'checking' && '正在检查搜索凭据…'}
                      {displayWebSearchStatus === 'configured' && replacingWebSearchKey && '粘贴新密钥后点「保存连接设置」以覆盖'}
                      {displayWebSearchStatus === 'not_configured' && '尚未配置此提供商 · 保存后不会回显密钥'}
                      {displayWebSearchStatus === 'saving' && '正在保存…'}
                      {displayWebSearchStatus === 'error' && '保存失败 · 请检查连接后重试'}
                    </p>
                    {replacingWebSearchKey && (
                      <button
                        type="button"
                        className="salieri-btn cancel-btn"
                        data-testid="cancel-replace-web-search-credential"
                        onClick={() => {
                          setReplacingWebSearchKey(false);
                          setLocalWebSearchApiKey('');
                        }}
                        disabled={isSaving}
                      >
                        取消更换
                      </button>
                    )}
                  </>
                )}
              </div>
            </section>
            )}

            {/* VOICE tab */}
            {activeTab === 'voice' && (
            <section className="tab-panel active">
              <div className="setting-field">
                <label className="checkbox-label">
                  <input type="checkbox" checked={localEnableTts} onChange={(e) => setLocalEnableTts(e.target.checked)} className="salieri-checkbox" />
                  启用语音合成 (TTS)
                </label>
              </div>
              <div className="setting-field field-full">
                <label>GPT-SoVITS 服务端点</label>
                <input type="text" value={localSovitsUrl} onChange={(e) => setLocalSovitsUrl(e.target.value)} className="salieri-input" placeholder="http://127.0.0.1:9880" />
              </div>
            </section>
            )}
          </main>
          {saveError && (
            <p className="settings-save-error" role="alert">
              {saveError === 'credential_save_failed'
                ? 'API 密钥保存失败，请检查后端连接后重试。'
                : saveError === 'provider_config_save_failed'
                  ? '供应商配置保存失败，请检查地址和模型名。'
                  : saveError === 'profile_create_failed'
                    ? '新建接入失败，请检查地址后重试。'
                    : '设置保存失败，请稍后重试。'}
            </p>
          )}
          <div className="modal-footer">
            {embedded
              ? <span className="settings-saved" role="status">{savedFlash ? '已保存' : ''}</span>
              : <button className="salieri-btn cancel-btn" onClick={onClose}>取消</button>}
            <button className="salieri-btn save-btn" onClick={() => { void handleSave(); }} disabled={isSaving}>
              {isSaving
                ? '保存中…'
                : activeTab === 'basic'
                  ? '保存基本设置'
                  : activeTab === 'connections'
                    ? '保存连接设置'
                    : '保存语音设置'}
            </button>
          </div>
    </>
  );
};
export default SettingsModal;
