import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Message, UseAmadeusWSParams } from '../hooks/useAmadeusWS';
import { Workstation } from '../components/Workstation';
import { DataPanel } from './DataPanel';
import { MessageMenu, resolveTruncateTarget } from './MessageMenu';
import { deriveFactReadingArchive } from '../memory-space/readingDetails';

const wsMock = vi.hoisted(() => ({
  messages: [] as Message[], isPlaying: false,
  callbacks: {} as Pick<UseAmadeusWSParams, 'onTurnStarted' | 'onTurnCompleted' | 'onTurnHistorySaved' | 'onAudioChunk'>,
  sendChat: vi.fn<(content: string, onAccepted?: () => void, onRejected?: () => void) => boolean>(() => true),
  canSendChat: vi.fn<(content: string) => boolean>(() => true),
  stopVoice: vi.fn(), cancelTurn: vi.fn(), sendDebugTts: vi.fn(), updateConfig: vi.fn(),
  reconnect: vi.fn(), applyTruncate: vi.fn(), hydrateMessageIds: vi.fn(),
  switchConversation: vi.fn(() => true), switchWorldline: vi.fn(() => true),
  stopAll: vi.fn(),
  playBase64: vi.fn<(data: string, onEnded?: () => void, onSettled?: () => void) => Promise<void>>(async () => undefined),
  wsRef: { current: null },
}));

vi.mock('../hooks/useAmadeusWS', () => ({
  getOrCreateAmadeusSessionId: () => 'session-288',
  useAmadeusWS: (params: UseAmadeusWSParams) => {
    wsMock.callbacks = params;
    wsMock.switchConversation.mockImplementation((conversationId: string, _history: Message[], requestEpoch?: number) => {
      params.onConversationSwitched?.({ conversationId, worldline: 'steins_gate', requestEpoch });
      return true;
    });
    return {
      status: 'READY', sessionState: 'ready', messages: wsMock.messages, isThinking: false, isTurnActive: false,
      isSynthesizing: false, activeTool: null, activeNotice: null, emotion: 'neutral',
      sendChat: wsMock.sendChat, canSendChat: wsMock.canSendChat, stopVoice: wsMock.stopVoice, cancelTurn: wsMock.cancelTurn,
      sendDebugTts: wsMock.sendDebugTts, updateConfig: wsMock.updateConfig, reconnect: wsMock.reconnect,
      applyTruncate: wsMock.applyTruncate, hydrateMessageIds: wsMock.hydrateMessageIds,
      switchConversation: wsMock.switchConversation, switchWorldline: wsMock.switchWorldline, wsRef: wsMock.wsRef,
    };
  },
}));
vi.mock('../hooks/useAudioPlayer', () => ({
  useAudioPlayer: () => ({ currentVolume: 0, isPlaying: wsMock.isPlaying }),
  audioPlayer: {
    init: vi.fn(), hasPendingPlayback: () => wsMock.isPlaying, playBase64: (...args: Parameters<typeof wsMock.playBase64>) => wsMock.playBase64(...args),
    stopAll: (...a: unknown[]) => wsMock.stopAll(...(a as [])), setVoiceVolume: vi.fn(), setMuted: vi.fn(),
  },
}));
vi.mock('../services/AudioService', () => ({
  audioService: { setEnableBGM: vi.fn(), setBGMVolume: vi.fn(), setEnableSFX: vi.fn(), setSFXVolume: vi.fn(), playBGMForWorldline: vi.fn(), stopBGM: vi.fn(), playSFX: vi.fn() },
}));
vi.mock('../design-system/useReducedMotion', () => ({ useReducedMotion: () => true }));
vi.mock('../components/TtsDebugPanel', () => ({ TtsDebugPanel: () => null }));
vi.mock('../components/AvatarViewer', () => ({ default: () => null }));
vi.mock('../components/SpriteAnimator', () => ({ default: () => null }));
vi.mock('../components/BilingualMessage', () => ({
  BilingualMessage: ({ fallbackJa, fallbackZh, speakingId }: { fallbackJa: string; fallbackZh?: string; speakingId?: number | null }) => (
    <div data-speaking-id={speakingId ?? ''}><span>{fallbackJa}</span>{fallbackZh ? <span>{fallbackZh}</span> : null}</div>
  ),
}));
vi.mock('../components/ForgetConversationModal', () => ({ ForgetConversationModal: () => null }));
vi.mock('../memory-space/MemorySpace', () => ({ MemorySpace: () => null }));

const SAMPLE: Message[] = [
  { id: 'u1', dbId: 10, role: 'user', content: '第一轮问题' },
  { id: 'a1', dbId: 11, role: 'assistant', content: '最初の答えよ。', translation: '第一个回答。' },
  { id: 'u2', dbId: 12, role: 'user', content: '第二轮问题' },
  { id: 'a2', dbId: 13, role: 'assistant', content: '二つ目の答えよ。', translation: '第二个回答。' },
];

describe('resolveTruncateTarget & MessageMenu keyboard access', () => {
  afterEach(cleanup);
  it('computes rollback point from next user turn and edit point from the same user message', () => {
    expect(resolveTruncateTarget(SAMPLE, 'a1', 'rollback')).toMatchObject({ mode: 'rollback', fromMessageId: 12, expectedLastMessageId: 13, deletedTurns: 1 });
    expect(resolveTruncateTarget(SAMPLE, 'a2', 'rollback')).toBeNull();
    expect(resolveTruncateTarget(SAMPLE, 'u1', 'edit')).toMatchObject({ mode: 'edit', fromMessageId: 10, expectedLastMessageId: 13, deletedTurns: 2 });
  });
  it('opens menu via keyboard on assistant message with required actions', () => {
    render(<div className="ws2"><MessageMenu message={SAMPLE[1]} messages={SAMPLE} replay={null} onReplay={vi.fn()} onCopy={vi.fn()} onTruncateRequest={vi.fn()} /></div>);
    const trigger = screen.getByTestId('message-menu-trigger-a1');
    trigger.focus();
    fireEvent.keyDown(trigger, { key: 'Enter' });
    for (const name of ['重播', '复制日文', '复制中文', '回到这里…']) {
      expect(screen.getByRole('menuitem', { name })).toBeInTheDocument();
    }
  });
  it('explains disabled ID-dependent menu items with a title only when dbId is missing', () => {
    const unpersisted: Message = { id: 'u-pending', role: 'user', content: '未落库' };
    const first = render(
      <div className="ws2">
        <MessageMenu message={unpersisted} messages={[...SAMPLE, unpersisted]} replay={null} onCopy={vi.fn()} onTruncateRequest={vi.fn()} />
      </div>,
    );
    const trigger = screen.getByTestId('message-menu-trigger-u-pending');
    trigger.focus();
    fireEvent.keyDown(trigger, { key: 'Enter' });
    expect(screen.getByRole('menuitem', { name: '改这句重发…' })).toHaveAttribute('title', '这条消息尚未写入记录，暂不可操作');
    expect(screen.getByRole('menuitem', { name: '回到这里…' })).toHaveAttribute('title', '这条消息尚未写入记录，暂不可操作');
    first.unmount();

    render(
      <div className="ws2">
        <MessageMenu message={SAMPLE[3]} messages={SAMPLE} replay={null} onReplay={vi.fn()} onCopy={vi.fn()} onTruncateRequest={vi.fn()} />
      </div>,
    );
    const lastTrigger = screen.getByTestId('message-menu-trigger-a2');
    lastTrigger.focus();
    fireEvent.keyDown(lastTrigger, { key: 'Enter' });
    expect(screen.getByRole('menuitem', { name: '回到这里…' })).not.toHaveAttribute('title');
  });
});

describe('Workstation message menu, truncate flow, replay & DataPanel', () => {
  let truncateStatus = 200;
  let truncateBody: Record<string, unknown> = { messages_deleted: 2, summaries_deleted: 0, core_facts_deleted: 0, episodic_deleted: 0 };
  let replayStatus = 200;
  let replayBody: Record<string, unknown> = { message_id: 11, source: 'original', cached: true, segments: ['UklGRg=='] };
  let callOrder: string[] = [];

  beforeEach(() => {
    localStorage.clear();
    wsMock.messages = SAMPLE.map((m) => ({ ...m }));
    wsMock.isPlaying = false;
    wsMock.sendChat.mockReset().mockReturnValue(true);
    wsMock.canSendChat.mockReset().mockReturnValue(true);
    wsMock.stopVoice.mockReset();
    wsMock.applyTruncate.mockReset();
    wsMock.hydrateMessageIds.mockReset();
    wsMock.switchConversation.mockClear();
    wsMock.stopAll.mockReset().mockImplementation(() => { callOrder.push('stopAll'); });
    wsMock.playBase64.mockReset().mockResolvedValue(undefined);
    truncateStatus = 200; truncateBody = { messages_deleted: 2, summaries_deleted: 0, core_facts_deleted: 0, episodic_deleted: 0 };
    replayStatus = 200; replayBody = { message_id: 11, source: 'original', cached: true, segments: ['UklGRg=='] };
    callOrder = [];

    const jsonRes = (status: number, body: unknown) => ({ ok: status >= 200 && status < 300, status, json: async () => body, clone: () => ({ json: async () => body }) }) as unknown as Response;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/providers') return jsonRes(200, []);
      if (url === '/api/search/settings') return jsonRes(200, { provider: 'tavily', configured: false });
      if (url.startsWith('/api/conversations?')) {
        return jsonRes(200, [{ id: 'conv-1', title: '测试会话', title_source: 'auto', is_default: true, is_pinned: false, provider_id: 'deepseek', model_id: 'deepseek-chat', identity_mode: 'okabe', last_active_at: '2026-11-22T00:00:00Z' }]);
      }
      if (url.includes('/api/conversations/conv-1/messages?')) return jsonRes(200, SAMPLE.map((m) => ({ id: m.dbId, role: m.role, content: m.content, translation: m.translation })));
      if (url.includes('/api/conversations/conv-1/truncate')) { callOrder.push(`truncate:${init?.body}`); return jsonRes(truncateStatus, truncateBody); }
      if (url.includes('/api/conversations/conv-1/messages/11/replay')) return jsonRes(replayStatus, replayBody);
      return jsonRes(404, {});
    }));
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  async function mountSelected() {
    render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: '测试会话' }));
    await waitFor(() => expect(wsMock.switchConversation).toHaveBeenCalled());
  }

  it('stops playback before calling truncate and confirms rollback copy', async () => {
    wsMock.isPlaying = true;
    await mountSelected();
    callOrder = [];
    const trigger = screen.getByTestId('message-menu-trigger-a1');
    trigger.focus(); fireEvent.keyDown(trigger, { key: 'Enter' });
    fireEvent.click(screen.getByRole('menuitem', { name: '回到这里…' }));
    expect(screen.getByText(/将删除之后的 1 轮对话/)).toBeInTheDocument();
    expect(screen.getByText(/她会忘掉只在这 1 轮里说过的事/)).toBeInTheDocument();
    expect(screen.getByText(/你置顶、手动确认或手动修改过的记忆会保留/)).toBeInTheDocument();
    expect(screen.getByText(/此操作无法撤销/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确认回到这里' }));
    await waitFor(() => expect(wsMock.applyTruncate).toHaveBeenCalledWith(12));
    expect(callOrder[0]).toBe('stopAll');
    expect(callOrder[1]).toContain('truncate:');
    expect(wsMock.stopVoice).toHaveBeenCalled();
  });

  it('surfaces 409 conversation_busy cleanly inside the confirm dialog', async () => {
    truncateStatus = 409; truncateBody = { detail: { code: 'conversation_busy' } };
    await mountSelected();
    const trigger = screen.getByTestId('message-menu-trigger-a1');
    trigger.focus(); fireEvent.keyDown(trigger, { key: 'Enter' });
    fireEvent.click(screen.getByRole('menuitem', { name: '回到这里…' }));
    fireEvent.click(screen.getByRole('button', { name: '确认回到这里' }));
    await waitFor(() => expect(screen.getByText('她正在回复这一轮，请先停掉当前回复后再试。')).toBeInTheDocument());
    expect(wsMock.applyTruncate).not.toHaveBeenCalled();
  });

  it('edit-and-resend runs two steps and keeps edited text in composer when sending fails', async () => {
    wsMock.sendChat.mockReturnValue(false);
    await mountSelected();
    const trigger = screen.getByTestId('message-menu-trigger-u2');
    trigger.focus(); fireEvent.keyDown(trigger, { key: 'Enter' });
    fireEvent.click(screen.getByRole('menuitem', { name: '改这句重发…' }));
    fireEvent.change(screen.getByTestId('truncate-edit-textarea'), { target: { value: '改写后的第二轮问题' } });
    fireEvent.click(screen.getByRole('button', { name: '截断并重发' }));
    await waitFor(() => expect(wsMock.applyTruncate).toHaveBeenCalledWith(12));
    expect(wsMock.sendChat.mock.calls[0]?.[0]).toBe('改写后的第二轮问题');
    expect((screen.getByLabelText('输入消息') as HTMLTextAreaElement).value).toBe('改写后的第二轮问题');
    expect(screen.getByTestId('message-action-toast')).toHaveTextContent('改好的话已留在输入框里');
  });

  it('reports TTS unavailable honestly on replay without breaking chat', async () => {
    replayStatus = 503; replayBody = { detail: { code: 'tts_unavailable', reason: 'service_unavailable' } };
    await mountSelected();
    const trigger = screen.getByTestId('message-menu-trigger-a1');
    trigger.focus(); fireEvent.keyDown(trigger, { key: 'Enter' });
    fireEvent.click(screen.getByRole('menuitem', { name: '重播' }));
    await waitFor(() => expect(screen.getByTestId('message-action-toast')).toHaveTextContent('语音服务未启动或不可用，暂时无法重播。'));
    expect(screen.getByLabelText('输入消息')).not.toBeDisabled();
  });

  function openAction(messageId: string, name: string) {
    const trigger = screen.getByTestId('message-menu-trigger-' + messageId);
    trigger.focus();
    fireEvent.keyDown(trigger, { key: 'Enter' });
    fireEvent.click(screen.getByRole('menuitem', { name }));
  }

  it('keeps foreground playback intact when a cached replay is requested', async () => {
    wsMock.isPlaying = true;
    await mountSelected();
    wsMock.stopAll.mockClear();
    openAction('a1', '重播');
    expect(await screen.findByTestId('message-action-toast')).toHaveTextContent('等这轮结束后再重播');
    expect(wsMock.stopAll).not.toHaveBeenCalled();
    expect(wsMock.playBase64).not.toHaveBeenCalled();
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('/replay?'))).toBe(false);
  });

  it.each(['stop', 'new turn'])('discards a loading replay after %s', async (source) => {
    await mountSelected();
    wsMock.stopAll.mockClear();
    const existing = vi.mocked(fetch).getMockImplementation()!;
    let finish!: (response: Response) => void;
    let signal: AbortSignal | null | undefined;
    vi.mocked(fetch).mockImplementation((url, init) => {
      if (!String(url).includes('/replay?')) return existing(url, init);
      signal = init?.signal;
      return new Promise((resolve) => { finish = resolve; });
    });
    openAction('a1', '重播');
    const bar = await screen.findByTestId('replay-status-bar');
    if (source === 'stop') {
      wsMock.isPlaying = true;
      fireEvent.click(within(bar).getByRole('button', { name: '停止' }));
      expect(wsMock.stopAll).not.toHaveBeenCalled();
    } else {
      act(() => wsMock.callbacks.onTurnStarted?.('conv-1'));
    }
    expect(signal?.aborted).toBe(true);
    await act(async () => finish(Response.json({ source: 'original', segments: ['UklGRg=='] })));
    expect(wsMock.playBase64).not.toHaveBeenCalled();
    expect(screen.queryByTestId('replay-status-bar')).not.toBeInTheDocument();
  });

  it('clears replay after the final segment fails and protects subsequent foreground audio', async () => {
    await mountSelected();
    openAction('a1', '重播');
    await waitFor(() => expect(screen.getByTestId('replay-status-bar')).toHaveTextContent('正在重播'));
    const onSettled = wsMock.playBase64.mock.calls.at(-1)?.[2];
    expect(onSettled).toBeTypeOf('function');
    act(() => onSettled?.());
    expect(screen.queryByTestId('replay-status-bar')).not.toBeInTheDocument();

    wsMock.isPlaying = true;
    wsMock.stopAll.mockClear();
    openAction('a1', '重播');
    expect(await screen.findByTestId('message-action-toast')).toHaveTextContent('等这轮结束后再重播');
    expect(wsMock.playBase64).toHaveBeenCalledOnce();
    expect(wsMock.stopAll).not.toHaveBeenCalled();
  });

  it('ignores an older history response after a newer refresh', async () => {
    await mountSelected();
    const existing = vi.mocked(fetch).getMockImplementation()!;
    const responses: Array<(response: Response) => void> = [];
    vi.mocked(fetch).mockImplementation((url, init) => {
      if (!String(url).includes('/messages?')) return existing(url, init);
      return new Promise((resolve) => responses.push(resolve));
    });
    act(() => wsMock.callbacks.onTurnHistorySaved?.('conv-1'));
    act(() => wsMock.callbacks.onTurnCompleted?.());
    expect(responses).toHaveLength(2);
    const newest = [{ id: 2, role: 'user', turn_id: 't1', content: 'newest' }];
    await act(async () => responses[1](Response.json(newest)));
    await act(async () => responses[0](Response.json([])));
    expect(wsMock.hydrateMessageIds).toHaveBeenCalledExactlyOnceWith(newest);
  });

  it('closes stale confirmation and reloads history on conversation_changed', async () => {
    truncateStatus = 409;
    truncateBody = { detail: { code: 'conversation_changed' } };
    await mountSelected();
    openAction('a1', '回到这里…');
    fireEvent.click(screen.getByRole('button', { name: '确认回到这里' }));
    expect(await screen.findByTestId('message-action-toast')).toHaveTextContent('重新选择');
    expect(screen.queryByRole('button', { name: '确认回到这里' })).not.toBeInTheDocument();
    await waitFor(() => expect(wsMock.switchConversation).toHaveBeenCalledTimes(2));
    expect(wsMock.applyTruncate).not.toHaveBeenCalled();
  });

  it('rejects an overlong edit before sending a truncate request', async () => {
    await mountSelected();
    openAction('u2', '改这句重发…');
    const draft = 'あ'.repeat(8001);
    fireEvent.change(screen.getByTestId('truncate-edit-textarea'), { target: { value: draft } });
    fireEvent.click(screen.getByRole('button', { name: '截断并重发' }));
    expect(await screen.findByText(/上限 8000 字/)).toBeInTheDocument();
    expect(callOrder.some((call) => call.startsWith('truncate:'))).toBe(false);
    expect(screen.getByTestId('truncate-edit-textarea')).toHaveValue(draft);
  });

  it('preserves the edit after truncate when writing the websocket throws', async () => {
    wsMock.sendChat.mockImplementation(() => { throw new DOMException('socket closed'); });
    await mountSelected();
    openAction('u2', '改这句重发…');
    fireEvent.change(screen.getByTestId('truncate-edit-textarea'), { target: { value: '改写后要保留' } });
    fireEvent.click(screen.getByRole('button', { name: '截断并重发' }));
    expect(await screen.findByTestId('message-action-toast')).toHaveTextContent('改好的话已留在输入框里');
    expect(wsMock.applyTruncate).toHaveBeenCalledWith(12);
    expect(screen.getByLabelText('输入消息')).toHaveValue('改写后要保留');
  });

  it('preserves an existing composer draft during successful edit-and-resend without flashing', async () => {
    await mountSelected();
    const composer = screen.getByLabelText('输入消息') as HTMLTextAreaElement;
    fireEvent.change(composer, { target: { value: '原有未发草稿' } });
    openAction('u2', '改这句重发…');
    fireEvent.change(screen.getByTestId('truncate-edit-textarea'), { target: { value: '改写后的问题' } });
    fireEvent.click(screen.getByRole('button', { name: '截断并重发' }));
    await waitFor(() => expect(wsMock.sendChat).toHaveBeenCalledWith('改写后的问题', undefined, expect.any(Function)));
    expect(composer).toHaveValue('原有未发草稿');
  });

  it('preserves both edited text and existing composer draft when edit-and-resend fails or is rejected', async () => {
    await mountSelected();
    const composer = screen.getByLabelText('输入消息') as HTMLTextAreaElement;

    // 1) Synchronous send failure keeps both edited text and pre-existing draft.
    wsMock.sendChat.mockReturnValueOnce(false);
    fireEvent.change(composer, { target: { value: '原有草稿A' } });
    openAction('u2', '改这句重发…');
    fireEvent.change(screen.getByTestId('truncate-edit-textarea'), { target: { value: '未能发出的改稿' } });
    fireEvent.click(screen.getByRole('button', { name: '截断并重发' }));
    expect(await screen.findByTestId('message-action-toast')).toHaveTextContent('改好的话已留在输入框里');
    expect(composer.value).toContain('未能发出的改稿');
    expect(composer.value).toContain('原有草稿A');

    // 2) Delayed WS rejection / disconnect before turn.started keeps both texts too.
    wsMock.sendChat.mockClear();
    wsMock.sendChat.mockReturnValueOnce(true);
    fireEvent.change(composer, { target: { value: '原有草稿B' } });
    openAction('u2', '改这句重发…');
    fireEvent.change(screen.getByTestId('truncate-edit-textarea'), { target: { value: '被拒收的改稿' } });
    fireEvent.click(screen.getByRole('button', { name: '截断并重发' }));
    await waitFor(() => expect(wsMock.sendChat).toHaveBeenCalled());
    expect(composer).toHaveValue('原有草稿B');
    act(() => wsMock.sendChat.mock.calls[0]?.[2]?.());
    expect(composer.value).toContain('被拒收的改稿');
    expect(composer.value).toContain('原有草稿B');
  });

  it('hides foreground stop buttons and speaking highlight on the last assistant message while replaying', async () => {
    const { rerender } = render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: '测试会话' }));
    await waitFor(() => expect(wsMock.switchConversation).toHaveBeenCalled());
    act(() => wsMock.callbacks.onAudioChunk?.(0, 'UklGRg=='));
    openAction('a1', '重播');
    await waitFor(() => expect(screen.getByTestId('replay-status-bar')).toHaveTextContent('正在重播'));
    wsMock.isPlaying = true;
    rerender(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    expect(screen.getByTestId('replay-status-bar')).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Current response controls' })).not.toBeInTheDocument();
    const lastArticle = document.querySelector('[data-message-id="a2"]');
    expect(lastArticle?.querySelector('[data-speaking-id]')).toHaveAttribute('data-speaking-id', '');
  });

  it('lists voice_cache separately in DataPanel disk usage', async () => {
    const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/auth/state') return ok({ passcode_set: false });
      if (url === '/api/data/status') return ok({ status: 'ok', available: true, detail: null, backups: [] });
      if (url === '/api/data/location') return ok({ path: '/data', default_path: '/data', source: 'default', movable: false, previous_path: null, previous_exists: false });
      if (url === '/api/data/disk') return ok({ categories: { conversations_memory: 2048, voice_cache: 5 * 1024 * 1024, other: 0 }, total_bytes: 2048 + 5 * 1024 * 1024 });
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    }));
    render(<DataPanel />);
    expect((await screen.findByText('语音缓存')).closest('li')).toHaveTextContent('5.00 MB');
  });

  it('renders human-readable restored provenance label in archive reading model', () => {
    const model = deriveFactReadingArchive({
      scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' },
      fact: { fact_id: 'f1', topic_id: null, is_pinned: false, active_version: 3, display_text: '你每周去跑步' },
      versions: [
        { version_no: 1, display_text: '你每周去跑步', change_kind: 'create', previous_version: null, valid_from: '2026-11-20T00:00:00Z', invalid_at: null, restored_from_version: null, is_active: false },
        { version_no: 3, display_text: '你每周去跑步', change_kind: 'create', previous_version: 2, valid_from: '2026-11-22T00:00:00Z', invalid_at: null, restored_from_version: 1, is_active: true },
      ],
      provenance: [],
    }, []);
    expect(model.revisionSummary).toBe('2 个版本 · 已在回退对话后恢复到早期说法');
    expect(model.historyVersions[1].changeKindHuman).toBe('回退对话后恢复（沿用第 1 版）');
  });
});
