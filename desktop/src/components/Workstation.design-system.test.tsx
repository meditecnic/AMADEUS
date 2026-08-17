import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

import { Workstation } from './Workstation';

const wsSpies = vi.hoisted(() => ({
  reconnect: vi.fn(),
  switchConversation: vi.fn(),
  switchWorldline: vi.fn(),
  useAmadeusWS: vi.fn(),
  sendChat: vi.fn(),
}));

const audioSpies = vi.hoisted(() => ({
  isPlaying: false,
  init: vi.fn(),
  playBase64: vi.fn(),
  stopAll: vi.fn(),
}));

const fetchMock = vi.hoisted(() => vi.fn());

vi.mock('../hooks/useAmadeusWS', () => ({
  getOrCreateAmadeusSessionId: () => 'session-ds',
  useAmadeusWS: (params: unknown) => {
    const override = wsSpies.useAmadeusWS(params);
    return (
      override || {
        status: 'READY',
        messages: [
          { id: 'u1', role: 'user', content: 'こんにちは', timestamp: Date.now() },
          {
            id: 'a1',
            role: 'assistant',
            content: '了解した',
            translation: '明白了',
            timestamp: Date.now(),
          },
        ],
        isThinking: false,
        isSynthesizing: false,
        emotion: 'neutral',
        sendChat: wsSpies.sendChat,
        stopVoice: vi.fn(),
        cancelTurn: vi.fn(),
        sendDebugTts: vi.fn(),
        updateConfig: vi.fn(),
        reconnect: wsSpies.reconnect,
        switchConversation: wsSpies.switchConversation,
        switchWorldline: wsSpies.switchWorldline,
        wsRef: { current: null },
      }
    );
  },
}));

vi.mock('../hooks/useAudioPlayer', () => ({
  useAudioPlayer: () => ({ currentVolume: 0, isPlaying: audioSpies.isPlaying }),
  audioPlayer: {
    init: (...args: unknown[]) => audioSpies.init(...args),
    playBase64: (...args: unknown[]) => audioSpies.playBase64(...args),
    stopAll: (...args: unknown[]) => audioSpies.stopAll(...args),
  },
}));

vi.mock('../services/AudioService', () => ({
  audioService: {
    setEnableBGM: vi.fn(),
    setBGMVolume: vi.fn(),
    setEnableSFX: vi.fn(),
    setSFXVolume: vi.fn(),
    playBGMForWorldline: vi.fn(),
    stopBGM: vi.fn(),
    playSFX: vi.fn(),
  },
}));

vi.mock('./StatusBar', () => ({ StatusBar: () => null }));
vi.mock('./SettingsModal', () => ({
  SettingsModal: () => null,
  CHAT_TEMPERATURE_MIN: 0,
  CHAT_TEMPERATURE_MAX: 1.2,
  CHAT_TEMPERATURE_STEP: 0.1,
  CHAT_TEMPERATURE_DEFAULT: 0.0,
  DEFAULT_IDENTITY_MODE: 'okabe',
  IDENTITY_MODE_STORAGE_KEY: 'amadeus_pc_identity_mode',
  SELF_NAME_STORAGE_KEY: 'amadeus_pc_self_name',
  clampChatTemperature: (value: number) => {
    if (!Number.isFinite(value)) return 0.0;
    return Math.max(0, Math.min(1.2, value));
  },
  normalizeIdentityMode: (value: unknown) => (value === 'self' ? 'self' : 'okabe'),
  loadStoredIdentityMode: () => 'okabe' as const,
  persistIdentityMode: () => {},
  loadStoredSelfName: () => {
    try {
      return localStorage.getItem('amadeus_pc_self_name') || '';
    } catch {
      return '';
    }
  },
  persistSelfName: (name: string) => {
    try {
      localStorage.setItem('amadeus_pc_self_name', name);
    } catch {
      /* ignore */
    }
  },
}));
// SoundMixerRail is real (header audio zone composition is under S4-DS test).
vi.mock('./HistorySidebar', () => ({
  HistorySidebar: () => <div data-testid="history-sidebar" />,
}));
vi.mock('./TtsDebugPanel', () => ({ TtsDebugPanel: () => null }));
vi.mock('./AvatarViewer', () => ({ default: () => null }));
vi.mock('./SpriteAnimator', () => ({ default: () => null }));
vi.mock('./HudBadge', () => ({ HudBadge: () => null }));
// Real DivergenceMeter is mounted (structure + aria-label ownership; not a mock stub).
vi.mock('./CRTOverlay', () => ({ CRTOverlay: () => null }));
vi.mock('./BilingualMessage', () => ({
  BilingualMessage: ({ japanese, chinese }: { japanese?: string; chinese?: string }) => (
    <div>
      <p>{japanese}</p>
      {chinese ? <p>{chinese}</p> : null}
    </div>
  ),
}));
vi.mock('./ForgetConversationModal', () => ({ ForgetConversationModal: () => null }));

function defaultFetch(input: RequestInfo | URL) {
  const url = String(input);
  const conversation = {
    id: 'conversation-ds',
    title: 'DS Conversation',
    title_source: 'auto',
    is_default: true,
    is_pinned: false,
    is_selected: true,
    provider_id: 'deepseek',
    model_id: 'deepseek-v4-flash',
    identity_mode: 'okabe',
    last_active_at: '2026-08-09T00:00:00Z',
  };
  if (url === '/api/providers') {
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => [
        {
          id: 'deepseek',
          display_name: 'DeepSeek',
          default_model: 'deepseek-v4-flash',
          configured: true,
          credential_required: true,
          model_discovery: true,
        },
      ],
    } as Response);
  }
  if (url.startsWith('/api/providers/')) {
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => ({ models: ['deepseek-v4-flash'] }),
    } as Response);
  }
  if (url.startsWith('/api/conversations?')) {
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => [conversation],
    } as Response);
  }
  if (url === '/api/search/settings') {
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => ({ provider: 'tavily', configured: false, fallback: 'duckduckgo' }),
    } as Response);
  }
  if (url.includes('/api/memory/status')) {
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => ({ pending_count: 0, processing_count: 0, failed_count: 0 }),
    } as Response);
  }
  if (url.includes('/api/memory/graph/projection')) {
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => ({
        scope: { session_id: 'session-ds', worldline: 'steins_gate', identity_mode: 'okabe' },
        view: 'overview',
        projection_version: 'memory-projection-v1',
        generated_at: '2026-08-14T00:00:00Z',
        criteria: { query: null, kinds: [], topic_id: null, pinned_only: false, updated_from: null, updated_to: null },
        center: {
          kind: 'continuity_hub',
          projection_id: 'hub:continuity:session-ds:steins_gate:okabe',
          label_primary: 'AMADEUS',
          label_secondary: 'SOUL',
        },
        composition: {
          active_facts: 0,
          active_experiences: 0,
          eligible_topics: 0,
          latest_memory_change_at: null,
          person_anchors_supported: false,
        },
        budgets: { nodes: 64, edges: 128, hard_max_nodes: 160, hard_max_edges: 320 },
        eligible: { nodes: 1, edges: 0, records: 0, results: 0 },
        shown: { nodes: 1, edges: 0, records: 0, results: 0 },
        truncated: { nodes: false, edges: false, records: false, results: false },
        empty: true,
        result_ids: [],
        nodes: [{
          kind: 'continuity_hub',
          projection_id: 'hub:continuity:session-ds:steins_gate:okabe',
          label_primary: 'AMADEUS',
          label_secondary: 'SOUL',
        }],
        edges: [],
      }),
    } as Response);
  }
  if (url.includes('/api/memory')) {
    throw new Error(`memory API must not be called in S4-DS: ${url}`);
  }
  throw new Error(`unexpected fetch: ${url}`);
}

function renderWorkstation() {
  return render(
    <Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
  );
}

describe('Workstation S4-DS dark-only chrome', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.history.replaceState({}, '', '/');
  });

  beforeEach(() => {
    localStorage.clear();
    fetchMock.mockReset();
    fetchMock.mockImplementation(defaultFetch);
    vi.stubGlobal('fetch', fetchMock);
    wsSpies.useAmadeusWS.mockReset();
    wsSpies.sendChat.mockReset();
    audioSpies.init.mockClear();
  });

  it('drops AM-01 / SALIERI / NEURAL TERMINAL / USER ID / AMADEUS: KURISU mono-string', async () => {
    renderWorkstation();
    await waitFor(() => {
      expect(screen.getByText('AMADEUS')).toBeInTheDocument();
    });
    expect(screen.queryByText('AM-01')).not.toBeInTheDocument();
    expect(screen.queryByText(/SALIERI/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/NEURAL TERMINAL/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/USER ID/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/AMADEUS:\s*KURISU/i)).not.toBeInTheDocument();
  });

  it('assistant speaker is structured KURISU primary + Amadeus secondary', async () => {
    renderWorkstation();
    const primary = await screen.findByTestId('speaker-assistant-primary');
    const secondary = screen.getByTestId('speaker-assistant-secondary');
    expect(primary).toHaveTextContent('KURISU');
    expect(secondary).toHaveTextContent('Amadeus');
  });

  it('okabe user speaker shows only OKABE', async () => {
    renderWorkstation();
    const userSpeaker = await screen.findByTestId('speaker-user');
    expect(userSpeaker).toHaveTextContent(/^OKABE$/);
    expect(userSpeaker).not.toHaveTextContent(/user/i);
  });

  it('user raw Japanese path marks content with lang=ja when kana present', async () => {
    renderWorkstation();
    const raw = await screen.findByText('こんにちは');
    expect(raw.tagName).toBe('P');
    expect(raw).toHaveClass('japanese-raw');
    expect(raw).toHaveAttribute('lang', 'ja');
  });

  it('Chinese user message does not force lang=ja (inherits document zh-CN)', async () => {
    wsSpies.useAmadeusWS.mockImplementation(() => ({
      status: 'READY',
      messages: [
        { id: 'u-zh', role: 'user', content: '我是谁', timestamp: Date.now() },
        {
          id: 'a-zh',
          role: 'assistant',
          content: '了解した',
          translation: '明白了',
          timestamp: Date.now(),
        },
      ],
      isThinking: false,
      isSynthesizing: false,
      emotion: 'neutral',
      sendChat: wsSpies.sendChat,
      stopVoice: vi.fn(),
      cancelTurn: vi.fn(),
      sendDebugTts: vi.fn(),
      updateConfig: vi.fn(),
      reconnect: wsSpies.reconnect,
      switchConversation: wsSpies.switchConversation,
      switchWorldline: wsSpies.switchWorldline,
      wsRef: { current: null },
    }));
    renderWorkstation();
    const raw = await screen.findByText('我是谁');
    expect(raw).toHaveClass('japanese-raw');
    expect(raw).not.toHaveAttribute('lang', 'ja');
    expect(raw.getAttribute('lang')).toBeNull();
  });

  it('self user shows configured name only; empty self shows neutral dash', async () => {
    localStorage.setItem('amadeus_pc_self_name', '鳳凰院');
    let switched: ((details: { conversationId: string }) => void) | undefined;
    wsSpies.useAmadeusWS.mockImplementation((params: unknown) => {
      switched = (params as { onConversationSwitched?: (d: { conversationId: string }) => void })
        .onConversationSwitched;
      return {
        status: 'READY',
        messages: [
          { id: 'u1', role: 'user', content: 'hi', timestamp: Date.now() },
          { id: 'a1', role: 'assistant', content: 'yo', timestamp: Date.now() },
        ],
        isThinking: false,
        isSynthesizing: false,
        emotion: 'neutral',
        sendChat: wsSpies.sendChat,
        stopVoice: vi.fn(),
        cancelTurn: vi.fn(),
        sendDebugTts: vi.fn(),
        updateConfig: vi.fn(),
        reconnect: wsSpies.reconnect,
        switchConversation: wsSpies.switchConversation,
        switchWorldline: wsSpies.switchWorldline,
        wsRef: { current: null },
      };
    });
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/conversations?')) {
        return {
          ok: true,
          status: 200,
          json: async () => [
            {
              id: 'conversation-self',
              title: 'Self Conv',
              title_source: 'auto',
              is_default: true,
              is_pinned: false,
              is_selected: true,
              provider_id: 'deepseek',
              model_id: 'deepseek-v4-flash',
              identity_mode: 'self',
              last_active_at: '2026-08-09T00:00:00Z',
            },
          ],
        } as Response;
      }
      return defaultFetch(input);
    });
    renderWorkstation();
    await waitFor(() => expect(switched).toBeTypeOf('function'));
    act(() => switched?.({ conversationId: 'conversation-self' }));
    await waitFor(() => {
      expect(screen.getByTestId('speaker-user')).toHaveTextContent('鳳凰院');
    });
  });

  it('has no ThemeControl / AUTO DAY NIGHT switch UI', async () => {
    renderWorkstation();
    await waitFor(() => expect(screen.getByText('AMADEUS')).toBeInTheDocument());
    expect(screen.queryByRole('radiogroup', { name: /外观/i })).not.toBeInTheDocument();
    expect(document.querySelector('[data-theme-control]')).toBeNull();
    expect(document.querySelector('.theme-aperture')).toBeNull();
    expect(screen.queryByText('AUTO')).not.toBeInTheDocument();
    expect(screen.queryByText(/^DAY$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^NIGHT$/)).not.toBeInTheDocument();
  });

  it('has no theme preference localStorage and fixed dark color-scheme contract in CSS', () => {
    expect(localStorage.getItem('amadeus_pc_theme')).toBeNull();
    const appCss = fs.readFileSync(path.resolve(__dirname, '../App.css'), 'utf8');
    const tokens = fs.readFileSync(path.resolve(__dirname, '../design-system/tokens.css'), 'utf8');
    expect(appCss).not.toMatch(/\[data-theme=['"]day['"]\]/);
    expect(tokens).not.toMatch(/\[data-theme=['"]day['"]\]/);
    expect(tokens).toMatch(/color-scheme:\s*dark/);
    expect(appCss).toMatch(/color-scheme:\s*dark/);
    expect(appCss).not.toMatch(/\/assets\/ui\/day\//);
  });

  it('DivergenceMeter remains sole worldline percentage authority', async () => {
    renderWorkstation();
    // Real DivergenceMeter (not mocked) — role=img + aria-label ownership.
    const meter = await screen.findByRole('img', { name: 'World line divergence 1.048596%' });
    expect(meter).toHaveClass('divergence-meter');
  });

  it('CircuitTrace is absent from Workstation', async () => {
    const source = fs.readFileSync(path.resolve(__dirname, './Workstation.tsx'), 'utf8');
    expect(source).not.toMatch(/CircuitTrace/);
    renderWorkstation();
    await waitFor(() => expect(screen.getByText('AMADEUS')).toBeInTheDocument());
  });

  it('first-class MEMORY aperture opens Memory Space Overview', async () => {
    renderWorkstation();
    await waitFor(() => expect(screen.getByText('AMADEUS')).toBeInTheDocument());
    const aperture = await screen.findByRole('button', { name: /^MEMORY$/i });
    fireEvent.click(aperture);
    expect(await screen.findByTestId('memory-space')).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/memory/graph/projection'))).toBe(true);
    });
    expect(screen.getByTestId('constellation-stage')).toBeInTheDocument();
    expect(document.querySelector('.salieri-workstation-panel')).toHaveClass('memory-space-open');
  });

  it('hides the workstation panel frame while Memory Space is open', () => {
    const appCss = fs.readFileSync(path.resolve(__dirname, '../App.css'), 'utf8');
    expect(appCss).toMatch(
      /\.salieri-workstation-panel\.memory-space-open\s+\.workstation-panel-frame-overlay/,
    );
    expect(appCss).toMatch(
      /\.salieri-workstation-panel\.memory-space-open::before/,
    );
  });

  it('production DCE: MEMORY aperture is not a DEV-only specimen', () => {
    const source = fs.readFileSync(path.resolve(__dirname, './Workstation.tsx'), 'utf8');
    expect(source).toMatch(/memory-aperture-btn/);
    expect(source).not.toMatch(/showMemorySpecimen/);
    expect(source).not.toMatch(/placement specimen/);
  });

  it('composer/model-bottom-bar and portal menus use semantic tokens (dark-only)', () => {
    const appCss = fs.readFileSync(path.resolve(__dirname, '../App.css'), 'utf8');
    expect(appCss).toMatch(/\.model-bottom-bar\s*\{[^}]*var\(--color-bg-panel/s);
    expect(appCss).toMatch(/\.salieri-select-menu(?:--portal)?[\s\S]{0,200}var\(--color-/);
    expect(appCss).not.toMatch(/\.model-bottom-bar\s+\.salieri-select-menu\s*\{/);
  });

  it('tokens.css does not own component chrome; App.css owns composition without tail !important wall', () => {
    const tokens = fs.readFileSync(path.resolve(__dirname, '../design-system/tokens.css'), 'utf8');
    const appCss = fs.readFileSync(path.resolve(__dirname, '../App.css'), 'utf8');
    // F2: tokens.css is token/type only.
    expect(tokens).not.toMatch(/\.workstation-header\s*\{/);
    expect(tokens).not.toMatch(/\.workstation-bg-overlay\s*\{/);
    expect(tokens).not.toMatch(/\.workstation-bg-layer\s*\{/);
    expect(tokens).not.toMatch(/\.left-brand\s*\{/);
    expect(tokens).not.toMatch(/\.speaker-primary\s*\{/);
    expect(tokens).not.toMatch(/\.memory-control-specimen\s*\{/);
    expect(tokens).not.toMatch(/\.right-controls\s*\{/);
    // App.css owns chrome via composition (source contract, not browser cascade PASS).
    expect(appCss).toMatch(/\.workstation-bg-layer\s*\{[^}]*filter:\s*var\(--bg-filter/s);
    expect(appCss).toMatch(/\.workstation-bg-overlay\s*\{[^}]*var\(--bg-overlay-top/s);
    expect(appCss).toMatch(/\.workstation-header\s*\{[^}]*var\(--chrome-header-bg/s);
    expect(appCss).not.toMatch(/S4-DS final chrome owners/);
    expect(appCss).not.toMatch(/Connections surface - recessed instrument bays/);
  });

  it('self-hosted S4-DS fonts exist under /assets/fonts/s4-ds/', () => {
    const fontsDir = path.resolve(__dirname, '../../public/assets/fonts/s4-ds');
    expect(fs.existsSync(fontsDir)).toBe(true);
    const woff2 = fs.readdirSync(fontsDir).filter((n) => n.endsWith('.woff2'));
    expect(woff2.length).toBeGreaterThan(0);
    const total = woff2.reduce((s, n) => s + fs.statSync(path.join(fontsDir, n)).size, 0);
    expect(total).toBeLessThanOrEqual(12 * 1024 * 1024);
    const tokens = fs.readFileSync(path.resolve(__dirname, '../design-system/tokens.css'), 'utf8');
    expect(tokens).toMatch(/Oxanium|Noto Sans SC|Noto Sans JP|IBM Plex Mono/);
    expect(tokens).toMatch(/\/assets\/fonts\/s4-ds\//);
  });

  it('header audio zone hosts mixer rail; no SoundPanel portal/backdrop/dropdown', async () => {
    renderWorkstation();
    await waitFor(() => expect(screen.getByText('AMADEUS')).toBeInTheDocument());
    const zone = document.querySelector('[data-header-audio-zone="true"]');
    expect(zone).toBeTruthy();
    expect(zone?.closest('.workstation-header')).toBeTruthy();

    const sound = screen.getByRole('button', { name: '音量控制' });
    expect(sound).toHaveAttribute('aria-expanded', 'false');
    // Closed: no dangling aria-controls when rail is unmounted.
    expect(sound).not.toHaveAttribute('aria-controls');
    expect(document.querySelector('.sound-panel-backdrop')).toBeNull();
    expect(document.querySelector('.sound-panel-dropdown')).toBeNull();
    expect(document.querySelector('[data-sound-mixer-rail]')).toBeNull();

    fireEvent.click(sound);
    expect(sound).toHaveAttribute('aria-expanded', 'true');
    expect(sound).toHaveAttribute('aria-controls', 'sound-mixer-rail');
    const rail = document.querySelector('[data-sound-mixer-rail="true"]');
    expect(rail).toBeTruthy();
    expect(rail?.closest('[data-header-audio-zone]')).toBeTruthy();
    expect(rail?.closest('.workstation-header')).toBeTruthy();
    expect(rail?.parentElement).not.toBe(document.body);
    expect(document.querySelector('.sound-panel-backdrop')).toBeNull();
    expect(document.querySelector('.sound-panel-dropdown')).toBeNull();
    // Telemetry priority: real DivergenceMeter remains while rail open (not mocked away).
    expect(
      screen.getByRole('img', { name: 'World line divergence 1.048596%' }),
    ).toBeInTheDocument();

    fireEvent.click(sound);
    expect(sound).toHaveAttribute('aria-expanded', 'false');
    expect(sound).not.toHaveAttribute('aria-controls');
    expect(document.querySelector('[data-sound-mixer-rail]')).toBeNull();
  });

  it('SoundPanel production import and legacy popup selectors are retired', () => {
    const ws = fs.readFileSync(path.resolve(__dirname, './Workstation.tsx'), 'utf8');
    const appCss = fs.readFileSync(path.resolve(__dirname, '../App.css'), 'utf8');
    expect(ws).not.toMatch(/from ['"]\.\/SoundPanel['"]/);
    expect(ws).toMatch(/from ['"]\.\/SoundMixerRail['"]/);
    expect(fs.existsSync(path.resolve(__dirname, './SoundPanel.tsx'))).toBe(false);
    expect(fs.existsSync(path.resolve(__dirname, './SoundMixerRail.tsx'))).toBe(true);
    // Legacy floating popup archetype must not remain as production CSS owners.
    expect(appCss).not.toMatch(/\.sound-panel-backdrop\s*\{/);
    expect(appCss).not.toMatch(/\.sound-panel-dropdown\s*\{/);
    expect(appCss).toMatch(/\.sound-mixer-rail\s*\{/);
    expect(appCss).toMatch(/\.header-audio-zone/);
  });

  it('mixer rail CSS is header layout, not fixed/portal; viewport contracts exist', () => {
    const appCss = fs.readFileSync(path.resolve(__dirname, '../App.css'), 'utf8');
    const railBlock = appCss.match(/\.sound-mixer-rail\s*\{[^}]*\}/s)?.[0] || '';
    expect(railBlock).toBeTruthy();
    expect(railBlock).not.toMatch(/position:\s*fixed/);
    expect(railBlock).not.toMatch(/position:\s*absolute/);
    // Breakpoint contracts for wide / compact / min-window density.
    expect(appCss).toMatch(/@media\s*\(min-width:\s*1280px\)[\s\S]{0,400}\.sound-mixer-rail/);
    expect(appCss).toMatch(/@media\s*\(max-width:\s*1279px\)[\s\S]{0,800}\.sound-mixer-rail|@media\s*\(min-width:\s*1080px\)\s*and\s*\(max-width:\s*1279px\)/);
    // Compact open: sole HUD owner hides low-priority badge (no !important override war);
    // never hide divergence meter. CSS source contract — not full browser cascade proof.
    expect(appCss).toMatch(
      /\.salieri-workstation-panel\s+\.workstation-header:has\(\.header-audio-zone\.is-open\)[\s\S]{0,220}\.hud-badge-container[\s\S]{0,120}display:\s*none/,
    );
    expect(appCss).not.toMatch(
      /\.workstation-header:has\(\.header-audio-zone\.is-open\)[\s\S]{0,200}\.divergence-meter\s*\{\s*display:\s*none/,
    );
    // reduced-motion path for rail
    expect(appCss).toMatch(/prefers-reduced-motion:\s*reduce[\s\S]{0,600}\.sound-mixer-rail/);
  });

  it('utility/history/mixer focus-visible uses semantic solid ring (no outline:auto / bare none)', () => {
    const appCss = fs.readFileSync(path.resolve(__dirname, '../App.css'), 'utf8');
    // Family must include workstation utilities + history + mixer rail.
    expect(appCss).toMatch(/\.workstation-utilities\s+\.ctrl-btn:focus-visible/);
    expect(appCss).toMatch(/\.history-sidebar-toggle:focus-visible/);
    expect(appCss).toMatch(/\.history-sidebar-icon:focus-visible/);
    expect(appCss).toMatch(/\.conversation-select:focus-visible/);
    expect(appCss).toMatch(/\.conversation-actions\s+button:focus-visible/);
    expect(appCss).toMatch(/\.history-new-btn:focus-visible/);
    expect(appCss).toMatch(/\.sound-mixer-rail[\s\S]{0,120}:focus-visible/);
    // Semantic solid ring (2px amber/copper via tokens) in the shared focus block.
    expect(appCss).toMatch(
      /S4-DS semantic focus-visible[\s\S]{0,1200}outline:\s*2px\s+solid\s+var\(--color-signal/,
    );
    expect(appCss).toMatch(
      /\.workstation-utilities\s+\.ctrl-btn:focus-visible[\s\S]{0,800}\.sound-mixer-rail[\s\S]{0,240}outline:\s*2px\s+solid\s+var\(--color-signal/,
    );
    // No CSS declaration of platform default outline:auto (comments excluded).
    expect(appCss).not.toMatch(/(?<![\w-])outline:\s*auto\b(?![^;{]*\*\/)/);
    // Reduced motion must not strip focus ring rules for this family.
    const reduced = appCss.match(/@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*?\n\}/g) || [];
    for (const block of reduced) {
      expect(block).not.toMatch(/\.workstation-utilities\s+\.ctrl-btn:focus-visible[\s\S]{0,80}outline:\s*none/);
    }
  });

  it('exactly one App.css import owner (main.tsx only)', () => {
    const main = fs.readFileSync(path.resolve(__dirname, '../main.tsx'), 'utf8');
    const app = fs.readFileSync(path.resolve(__dirname, '../App.tsx'), 'utf8');
    expect(main).toMatch(/import\s+['"]\.\/App\.css['"]/);
    expect(app).not.toMatch(/import\s+['"]\.\/App\.css['"]/);
  });

  it('unified scrollbar owns real scrollports; no scrollbar_thumb.png raster', () => {
    // CSS source/regex contract — not a full browser cascade measurement.
    const appCss = fs.readFileSync(path.resolve(__dirname, '../App.css'), 'utf8');
    expect(appCss).toMatch(/S4-DS unified scrollbar system/);
    expect(appCss).toMatch(/\.terminal-chat-viewport[\s\S]{0,200}\.history-session-list/);
    expect(appCss).toMatch(/\.history-session-list::-webkit-scrollbar/);
    expect(appCss).toMatch(
      /\.salieri-select-menu--portal::-webkit-scrollbar|\[data-salieri-portal=["']true["']\]::-webkit-scrollbar/,
    );
    expect(appCss).toMatch(/\.tab-panels::-webkit-scrollbar/);
    expect(appCss).not.toMatch(/url\(['"]\/assets\/ui\/scrollbar_thumb\.png['"]\)/);
    expect(appCss).toMatch(/scrollbar-width:\s*thin/);
    expect(appCss).toMatch(/scrollbar-color:/);
  });

  it('SG and β worldline token anchors are statically distinct', () => {
    const appCss = fs.readFileSync(path.resolve(__dirname, '../App.css'), 'utf8');
    const tokens = fs.readFileSync(path.resolve(__dirname, '../design-system/tokens.css'), 'utf8');
    // Anchors (CSS source). Static distinction also uses --wl-surface-tint / selected-edge.
    expect(appCss).toMatch(/\.worldline-sg[\s\S]{0,400}--wl-accent:\s*#d97a2d/);
    expect(appCss).toMatch(/\.worldline-beta[\s\S]{0,400}--wl-accent:\s*#c64b3a/);
    expect(appCss).toMatch(/--wl-surface-tint/);
    expect(appCss).toMatch(/--wl-selected-edge/);
    // β is not a simple dim of SG orange family alone — iron-red family present.
    expect(appCss).toMatch(/#c64b3a|#f18468/);
    expect(tokens).toMatch(/--color-signal:\s*#d96f24/);
    expect(tokens).toMatch(/--color-gold:\s*#c6a45f/);
  });

  it('Connections surface uses semantic tokens (no legacy orange/brown hardcodes in final rules)', () => {
    const appCss = fs.readFileSync(path.resolve(__dirname, '../App.css'), 'utf8');
    // Single owner (original CONNECTIONS block) — source contract.
    expect(appCss).toMatch(
      /\.connections-section\s*\{[\s\S]{0,200}var\(--color-border/,
    );
    expect(appCss).toMatch(
      /\.connections-section-title\s*\{[\s\S]{0,120}var\(--color-text/,
    );
    expect(appCss).toMatch(
      /\.custom-profile-row\s*\{[\s\S]{0,200}var\(--color-border/,
    );
    expect(appCss).toMatch(
      /\.profile-action-delete\s*\{[\s\S]{0,120}var\(--color-danger/,
    );
    expect(appCss).toMatch(
      /\.connections-secret-input[\s\S]{0,200}var\(--color-border/,
    );
    // No second Connections override wall.
    expect(appCss).not.toMatch(/Connections surface - recessed instrument bays/);
    // Active Connections rules must not still use listed legacy hardcodes as owners.
    const connSlice = appCss.slice(
      appCss.indexOf('/* CONNECTIONS comfort'),
      appCss.indexOf('.salieri-btn') > 0 ? appCss.indexOf('.salieri-btn') : appCss.length,
    );
    for (const hex of ['#6b3010', '#ffaa00', '#ffd18e', '#5a2500', '#4e250d', '#7a3a12', '#ffcc66', '#a8642e', '#925124', '#d98a42', '#61300d']) {
      expect(connSlice).not.toContain(hex);
    }
  });

  it('App.css does not redeclare canonical semantic tokens; tokens.css owns exact values', () => {
    // Source contract: cascade authority (not browser computed-style PASS).
    const appCss = fs.readFileSync(path.resolve(__dirname, '../App.css'), 'utf8');
    const tokens = fs.readFileSync(path.resolve(__dirname, '../design-system/tokens.css'), 'utf8');
    const rootMatch = appCss.match(/:root\s*\{[\s\S]*?\n\}/);
    const root = rootMatch ? rootMatch[0] : '';
    // Canonical names must not be reassigned in App.css :root.
    expect(root).not.toMatch(/--color-bg\s*:/);
    expect(root).not.toMatch(/--color-signal\s*:/);
    expect(root).not.toMatch(/--color-gold\s*:/);
    expect(root).not.toMatch(/--font-mono\s*:/);
    expect(root).not.toMatch(/--glow-primary\s*:/);
    expect(root).not.toMatch(/--transition-fast\s*:/);
    // tokens.css exact authority values.
    expect(tokens).toMatch(/--color-bg:\s*#0b0907/);
    expect(tokens).toMatch(/--color-bg-deep:\s*#070605/);
    expect(tokens).toMatch(/--color-signal:\s*#d96f24/);
    expect(tokens).toMatch(/--color-gold:\s*#c6a45f/);
    expect(tokens).toMatch(/--color-signal-hot:\s*#ffad54/);
  });

  it('DivergenceMeter has no TSX hardcoded accent hex; meter uses --wl-meter-accent; ghost uses --wl-accent', () => {
    const src = fs.readFileSync(path.resolve(__dirname, './DivergenceMeter.tsx'), 'utf8');
    const appCss = fs.readFileSync(path.resolve(__dirname, '../App.css'), 'utf8');
    // RED if TSX reintroduces palette hex.
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}/);
    expect(src).toMatch(/currentColor|divergence-meter-glyph/);
    // Primary meter readout = original instrument palette via --wl-meter-accent.
    expect(appCss).toMatch(/\.divergence-meter\s*\{[^}]*color:\s*var\(--wl-meter-accent\)/s);
    expect(appCss).toMatch(
      /\.salieri-workstation-panel\.worldline-sg\s*\{[\s\S]{0,800}--wl-meter-accent:\s*#ffb85c/,
    );
    expect(appCss).toMatch(
      /\.salieri-workstation-panel\.worldline-beta\s*\{[\s\S]{0,800}--wl-meter-accent:\s*#e39146/,
    );
    // Structural beta red remains #c64b3a; ghost uses --wl-accent not meter palette.
    expect(appCss).toMatch(
      /\.salieri-workstation-panel\.worldline-beta\s*\{[\s\S]{0,120}--wl-accent:\s*#c64b3a/,
    );
    expect(appCss).toMatch(
      /\.divergence-meter-glyph-ghost[\s\S]{0,160}(?:color|stroke):\s*var\(--wl-accent\)/,
    );
  });

  it('ordinary HUD / footer do not wash beta iron-red onto day/date/ONLINE/footer value', () => {
    // Source contract only — not browser cascade PASS.
    const appCss = fs.readFileSync(path.resolve(__dirname, '../App.css'), 'utf8');
    const tokens = fs.readFileSync(path.resolve(__dirname, '../design-system/tokens.css'), 'utf8');
    expect(tokens).not.toMatch(/\.app-main-container\s*\{/);
    expect(appCss).toMatch(/\.app-main-container\s*\{/);
    // Sole non-media .workstation-header base is HEADER STYLE owner (grid + chrome).
    const baseHeaders = (appCss.match(/^\.workstation-header\s*\{/gm) || []).length;
    expect(baseHeaders).toBe(1);
    // Targeted HUD owner (no !important cascade war).
    const hudStart = appCss.indexOf('Sole targeted HUD owner');
    const hudEnd = appCss.indexOf('Jump to latest');
    expect(hudStart).toBeGreaterThan(-1);
    expect(hudEnd).toBeGreaterThan(hudStart);
    const hudOwner = appCss.slice(hudStart, hudEnd);
    expect(hudOwner).toMatch(/hud-day-text[\s\S]{0,160}var\(--color-text-dim\)/);
    expect(hudOwner).toMatch(/hud-date-text[\s\S]{0,160}var\(--color-gold\)/);
    expect(hudOwner).toMatch(/hud-status-pill\s*\{[\s\S]{0,240}var\(--color-text-dim\)/);
    expect(hudOwner).toMatch(/hud-status-dot[\s\S]{0,160}var\(--color-success\)/);
    expect(hudOwner).not.toMatch(/!important/);
    // Footer beta restrained copper, not structural iron-red.
    expect(appCss).toMatch(
      /\.salieri-workstation-panel\.worldline-beta\s*\{[\s\S]{0,800}--wl-footer-accent:\s*#b77b55/,
    );
    expect(appCss).toMatch(/worldline-val[\s\S]{0,120}var\(--wl-footer-accent\)/);
    // Day/date/status sole owner must not color with --wl-accent (beta red wash).
    expect(hudOwner).not.toMatch(/var\(--wl-accent\)/);
  });
});
