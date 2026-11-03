import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { Workstation } from './Workstation';
import type { MemoryLink } from '../memory-space/MemorySpace';

const wsState = vi.hoisted(() => ({ messages: [] as unknown[] }));

vi.mock('../hooks/useAmadeusWS', () => ({
  getOrCreateAmadeusSessionId: () => 'session-one',
  useAmadeusWS: () => ({
    status: 'READY', sessionState: 'ready', messages: wsState.messages, isThinking: false, isSynthesizing: false, emotion: 'neutral',
    sendChat: vi.fn(), stopVoice: vi.fn(), cancelTurn: vi.fn(), sendDebugTts: vi.fn(), updateConfig: vi.fn(), reconnect: vi.fn(),
    switchConversation: vi.fn(() => true), switchWorldline: vi.fn(() => true), wsRef: { current: null },
  }),
}));
vi.mock('../hooks/useAudioPlayer', () => ({
  useAudioPlayer: () => ({ currentVolume: 0, isPlaying: false }),
  audioPlayer: { init: vi.fn(), playBase64: vi.fn(), stopAll: vi.fn() },
}));
vi.mock('../services/AudioService', () => ({
  audioService: {
    setEnableBGM: vi.fn(), setBGMVolume: vi.fn(), setEnableSFX: vi.fn(), setSFXVolume: vi.fn(),
    playBGMForWorldline: vi.fn(), stopBGM: vi.fn(), playSFX: vi.fn(),
  },
}));
vi.mock('../design-system/useReducedMotion', () => ({ useReducedMotion: () => true }));
vi.mock('./TtsDebugPanel', () => ({ TtsDebugPanel: () => null }));
vi.mock('./AvatarViewer', () => ({ default: () => null }));
vi.mock('./SpriteAnimator', () => ({ default: () => null }));
vi.mock('./BilingualMessage', () => ({ BilingualMessage: ({ fallbackJa }: { fallbackJa: string }) => <span>{fallbackJa}</span> }));
vi.mock('./ForgetConversationModal', () => ({ ForgetConversationModal: () => null }));
vi.mock('../memory-space/MemorySpace', () => ({
  MemorySpace: ({ link, onClose }: { link?: MemoryLink | null; onClose: () => void }) => (
    <div data-testid="memory-stub">
      <output data-testid="memory-link">{JSON.stringify(link ?? null)}</output>
      <button type="button" onClick={onClose}>stub-close</button>
    </div>
  ),
}));

const AT = '2026-10-12T08:01:22+00:00';

describe('Workstation chat memory marks', () => {
  beforeEach(() => {
    localStorage.clear();
    wsState.messages = [
      { id: 'user_1', role: 'user', content: '记住我喜欢茶', memoryRecorded: [
        { kind: 'fact', id: 'f01', change: 'create', label: '你喜欢茶', createdAt: AT },
        { kind: 'fact', id: 'f02', change: 'create', label: '你不喝咖啡', createdAt: AT },
      ] },
      { id: 'assist_t1', role: 'assistant', content: 'はい', turnId: 't1', streaming: false, segments: [], memoryRefs: {
        at: new Date(2026, 10, 3, 21, 40).getTime(),
        items: [{ kind: 'fact', id: 'f09', via: 'supplement', label: '你养了猫', createdAt: AT }],
      } },
    ];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/providers') return { ok: true, status: 200, json: async () => [] } as Response;
      if (url.startsWith('/api/conversations?')) return { ok: true, status: 200, json: async () => [] } as Response;
      if (url === '/api/search/settings') return { ok: true, status: 200, json: async () => ({ provider: 'tavily', configured: false, fallback: 'duckduckgo' }) } as Response;
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    }));
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  const mount = () => render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);

  it('a user-line mark opens the space on that group, and closing gives focus back to the mark', async () => {
    mount();
    const mark = screen.getByTestId('memory-mark-created');
    expect(mark).toHaveTextContent('记下了 2 条 ↗');
    mark.focus();
    fireEvent.click(mark);
    expect(JSON.parse(screen.getByTestId('memory-link').textContent!)).toEqual({ select: ['f01', 'f02'], mark: ['f02'], hintAt: null });
    fireEvent.click(screen.getByText('stub-close'));
    await waitFor(() => expect(screen.queryByTestId('memory-stub')).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId('memory-mark-created')));
  });

  it('the reference list opens the space with the references marked and the turn time', async () => {
    mount();
    fireEvent.click(screen.getByTestId('memory-mark-refs'));
    fireEvent.click(screen.getByTestId('memory-refs-open'));
    expect(JSON.parse(screen.getByTestId('memory-link').textContent!)).toEqual({
      select: [], mark: ['f09'], hintAt: new Date(2026, 10, 3, 21, 40).getTime(),
    });
    fireEvent.click(screen.getByText('stub-close'));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId('memory-mark-refs')));
  });

  it('the header memory key opens without a link and still gets focus back', async () => {
    mount();
    fireEvent.click(screen.getByRole('button', { name: 'MEMORY' }));
    expect(screen.getByTestId('memory-link')).toHaveTextContent('null');
    fireEvent.click(screen.getByText('stub-close'));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: 'MEMORY' })));
  });

  it('shows no reference mark while the reply is still streaming', () => {
    wsState.messages = [
      { id: 'assist_t1', role: 'assistant', content: 'は', turnId: 't1', streaming: true, segments: [], memoryRefs: {
        at: 1, items: [{ kind: 'fact', id: 'f09', via: 'supplement', label: '你养了猫', createdAt: AT }],
      } },
    ];
    mount();
    expect(screen.queryByTestId('memory-mark-refs')).toBeNull();
  });
});
