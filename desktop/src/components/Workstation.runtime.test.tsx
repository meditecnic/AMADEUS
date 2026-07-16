import { fireEvent, render, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { Workstation } from './Workstation';


const wsSpies = vi.hoisted(() => ({
  reconnect: vi.fn(),
  switchConversation: vi.fn(),
}));

vi.mock('../hooks/useAmadeusWS', () => ({
  getOrCreateAmadeusSessionId: () => 'session-one',
  useAmadeusWS: () => ({
    status: 'ERROR',
    messages: [],
    isThinking: false,
    isSynthesizing: false,
    emotion: 'neutral',
    sendChat: vi.fn(),
    stopVoice: vi.fn(),
    cancelTurn: vi.fn(),
    sendDebugTts: vi.fn(),
    updateConfig: vi.fn(),
    reconnect: wsSpies.reconnect,
    switchConversation: wsSpies.switchConversation,
    wsRef: { current: null },
  }),
}));

vi.mock('../hooks/useAudioPlayer', () => ({
  useAudioPlayer: () => ({ currentVolume: 0, isPlaying: false }),
  audioPlayer: { init: vi.fn(), playBase64: vi.fn(), stopAll: vi.fn() },
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
vi.mock('./SettingsModal', () => ({ SettingsModal: () => null }));
vi.mock('./SoundPanel', () => ({ SoundPanel: () => null }));
vi.mock('./HistorySidebar', () => ({ HistorySidebar: () => null }));
vi.mock('./TtsDebugPanel', () => ({ TtsDebugPanel: () => null }));
vi.mock('./AvatarViewer', () => ({ default: () => null }));
vi.mock('./SpriteAnimator', () => ({ default: () => null }));
vi.mock('./HudBadge', () => ({ HudBadge: () => null }));
vi.mock('./DivergenceMeter', () => ({ DivergenceMeter: () => null }));
vi.mock('./CRTOverlay', () => ({ CRTOverlay: () => null }));
vi.mock('./CircuitTrace', () => ({ CircuitTrace: () => null }));
vi.mock('./BilingualMessage', () => ({ BilingualMessage: () => null }));
vi.mock('./ForgetConversationModal', () => ({ ForgetConversationModal: () => null }));


describe('Workstation provider runtime changes', () => {
  beforeEach(() => {
    localStorage.clear();
    wsSpies.reconnect.mockClear();
    wsSpies.switchConversation.mockClear();

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const conversation = {
        id: 'conversation-one',
        title: 'Default Conversation',
        title_source: 'auto',
        is_default: true,
        is_pinned: false,
        is_selected: true,
        provider_id: 'deepseek',
        model_id: 'deepseek-v4-flash',
        last_active_at: '2026-07-16T00:00:00Z',
      };
      const providers = [
        {
          id: 'deepseek', display_name: 'DeepSeek', default_model: 'deepseek-v4-flash',
          configured: false, credential_required: true, model_discovery: true,
        },
        {
          id: 'custom', display_name: 'OpenAI-compatible', default_model: 'local-model',
          configured: true, credential_required: false, model_discovery: true,
        },
      ];

      if (url === '/api/providers') {
        return { ok: true, status: 200, json: async () => providers } as Response;
      }
      if (url.startsWith('/api/providers/')) {
        return { ok: true, status: 200, json: async () => ({ models: ['local-model'] }) } as Response;
      }
      if (url.startsWith('/api/conversations?')) {
        return { ok: true, status: 200, json: async () => [conversation] } as Response;
      }
      if (url.startsWith('/api/conversations/conversation-one') && init?.method === 'PATCH') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ ...conversation, provider_id: 'custom', model_id: 'local-model' }),
        } as Response;
      }
      throw new Error(`unexpected fetch: ${url}`);
    }));
  });

  it('reconnects after a provider patch so auth reloads the stored provider', async () => {
    const { container } = render(
      <Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );

    const providerSelect = await waitFor(() => {
      const select = container.querySelector<HTMLSelectElement>('.provider-select');
      expect(select).not.toBeNull();
      expect(select?.disabled).toBe(false);
      return select as HTMLSelectElement;
    });
    fireEvent.change(providerSelect, { target: { value: 'custom' } });

    await waitFor(() => expect(wsSpies.reconnect).toHaveBeenCalledTimes(1));
    expect(wsSpies.switchConversation).not.toHaveBeenCalled();
  });
});
