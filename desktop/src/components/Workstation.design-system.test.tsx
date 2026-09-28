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
    expect(raw).toHaveClass('ws2-user-text');
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
    expect(raw).toHaveClass('ws2-user-text');
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

  it('nixie meter remains sole worldline percentage authority', async () => {
    renderWorkstation();
    const meter = await screen.findByRole('img', { name: 'World line divergence 1.048596%' });
    expect(meter).toHaveClass('nixie');
    expect(screen.getAllByRole('img', { name: /World line divergence/ })).toHaveLength(1);
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
    expect(document.querySelector('.ws2')).toHaveClass('memory-space-open');
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

  it('her voice is adjusted and muted on the stage scope without losing the level', async () => {
    renderWorkstation();
    await waitFor(() => expect(screen.getByText('AMADEUS')).toBeInTheDocument());
    const scope = screen.getByRole('slider', { name: '角色语音音量' });
    const start = Number(scope.getAttribute('aria-valuenow'));
    fireEvent.keyDown(scope, { key: 'ArrowDown' });
    expect(Number(scope.getAttribute('aria-valuenow'))).toBe(Math.max(0, start - 5));
    fireEvent.keyDown(scope, { key: 'm' });
    expect(scope.getAttribute('aria-valuetext')).toMatch(/静音/);
    // Muting keeps the level for restore.
    expect(Number(scope.getAttribute('aria-valuenow'))).toBe(Math.max(0, start - 5));
    fireEvent.keyDown(scope, { key: 'm' });
    expect(scope.getAttribute('aria-valuetext')).not.toMatch(/静音/);
  });

  it('keeps a keyboard focus ring and reduces volume-rail motion', () => {
    const appCss = fs.readFileSync(path.resolve(__dirname, '../App.css'), 'utf8');
    expect(appCss).toMatch(
      /\.workstation-utilities\s+\.ctrl-btn:focus-visible[\s\S]{0,800}\.sound-mixer-rail[\s\S]{0,240}outline:\s*2px\s+solid\s+var\(--color-signal/,
    );
    const reduced = appCss.match(/@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*?\n\}/g) || [];
    expect(reduced.length).toBeGreaterThan(0);
    for (const block of reduced) {
      expect(block).not.toMatch(/:focus-visible[\s\S]{0,80}outline:\s*none/);
    }
    expect(appCss).toMatch(/prefers-reduced-motion:\s*reduce[\s\S]{0,600}\.sound-mixer-rail/);
  });

  it('exactly one App.css import owner (main.tsx only)', () => {
    const main = fs.readFileSync(path.resolve(__dirname, '../main.tsx'), 'utf8');
    const app = fs.readFileSync(path.resolve(__dirname, '../App.tsx'), 'utf8');
    expect(main).toMatch(/import\s+['"]\.\/App\.css['"]/);
    expect(app).not.toMatch(/import\s+['"]\.\/App\.css['"]/);
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
});
