import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ConversationTurnState, Message, UseAmadeusWSParams } from '../hooks/useAmadeusWS';
import { Workstation } from '../components/Workstation';
import { DataPanel } from './DataPanel';
import { MessageMenu, resolveTruncateFromHistory, resolveTruncateTarget } from './MessageMenu';
import { deriveFactReadingArchive } from '../memory-space/readingDetails';

const wsMock = vi.hoisted(() => ({
  messages: [] as Message[], isPlaying: false, isThinking: false, isTurnActive: false, isSynthesizing: false,
  conversationTurns: {} as Record<string, ConversationTurnState>,
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
      status: 'READY', sessionState: 'ready', messages: wsMock.messages, isThinking: wsMock.isThinking, isTurnActive: wsMock.isTurnActive,
      isSynthesizing: wsMock.isSynthesizing, activeTool: null, activeNotice: null, emotion: 'neutral',
      conversationTurns: wsMock.conversationTurns,
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
  it('previews unhydrated turns with turnId in deletedTurns while excluding rejected sends without turnId', () => {
    const withActiveTurn: Message[] = [
      ...SAMPLE,
      { id: 'u-rejected', role: 'user', content: '被拒收的消息' },
      { id: 'err-rejected', role: 'assistant', content: 'SYSTEM ERROR: too long', systemError: true },
      { id: 'u3', turnId: 't3', role: 'user', content: '正在播的第三轮' },
      { id: 'a3', turnId: 't3', role: 'assistant', streaming: true, content: '三つ目の答えよ。', translation: '第三个回答。' },
    ];
    expect(resolveTruncateTarget(withActiveTurn, 'a1', 'rollback')).toMatchObject({
      mode: 'rollback', fromMessageId: 12, expectedLastMessageId: 13, deletedTurns: 2,
    });
    expect(resolveTruncateTarget(withActiveTurn, 'u1', 'edit')).toMatchObject({
      mode: 'edit', fromMessageId: 10, expectedLastMessageId: 13, deletedTurns: 3,
    });
  });
  it('recomputes cutoff, last id, and user turn count directly from persisted history rows', () => {
    const initial = resolveTruncateTarget(SAMPLE, 'a1', 'rollback')!;
    const rowsWithZeroOutput = [
      { id: 10, role: 'user' },
      { id: 11, role: 'assistant' },
      { id: 12, role: 'user' },
      { id: 13, role: 'assistant' },
      { id: 14, role: 'user' },
      { id: 15, role: 'assistant' },
      { id: 16, role: 'user' },
      { id: 17, role: 'assistant' },
    ];
    expect(resolveTruncateFromHistory(rowsWithZeroOutput, initial)).toMatchObject({
      mode: 'rollback', fromMessageId: 12, expectedLastMessageId: 17, deletedTurns: 3,
    });
    expect(resolveTruncateFromHistory([{ id: 10, role: 'user' }], initial)).toBeNull();
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
    wsMock.conversationTurns = {};
    wsMock.isPlaying = false;
    wsMock.isThinking = false;
    wsMock.isTurnActive = false;
    wsMock.isSynthesizing = false;
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
    const view = render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: '测试会话' }));
    await waitFor(() => expect(wsMock.switchConversation).toHaveBeenCalled());
    return view;
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

  it('preserves an edited draft when a queued turn blocks edit-and-resend', async () => {
    truncateStatus = 409;
    truncateBody = { detail: { code: 'conversation_busy' } };
    const view = await mountSelected();
    const composer = screen.getByLabelText('输入消息');
    fireEvent.change(composer, { target: { value: '原有输入稿' } });
    wsMock.conversationTurns = {
      'conv-1': { running: false, queued: ['wait'], queuedTexts: { wait: '正在排队的另一句' } },
    };
    view.rerender(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    expect(screen.getByText('正在排队的另一句')).toBeInTheDocument();

    openAction('u2', '改这句重发…');
    fireEvent.change(screen.getByTestId('truncate-edit-textarea'), { target: { value: '改好的第二句' } });
    fireEvent.click(screen.getByRole('button', { name: '截断并重发' }));
    expect(await screen.findByText('这段对话有消息在排队，请先撤回排队的消息再试。')).toBeInTheDocument();
    expect(callOrder).toContain('truncate:{"from_message_id":12,"expected_last_message_id":13}');
    expect(screen.getByTestId('truncate-edit-textarea')).toHaveValue('改好的第二句');
    expect(composer).toHaveValue('原有输入稿');
    expect(wsMock.applyTruncate).not.toHaveBeenCalled();
    expect(wsMock.sendChat).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(screen.queryByTestId('truncate-edit-textarea')).not.toBeInTheDocument();
    expect(composer).toHaveValue('改好的第二句\n\n原有输入稿');
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

  it('rejects immediately with conversation_busy without stopping voice when first segment is playing before turn completes', async () => {
    truncateStatus = 409;
    truncateBody = { detail: { code: 'conversation_busy' } };
    wsMock.messages = [
      ...SAMPLE.map((m) => ({ ...m })),
      { id: 'u3', turnId: 't3', role: 'user', content: '第三轮问题' },
      { id: 'a3', turnId: 't3', role: 'assistant', streaming: true, content: '三つ目の答えよ。', translation: '第三个回答。' },
    ];
    wsMock.isPlaying = true;
    wsMock.isThinking = false;
    wsMock.isTurnActive = true;
    wsMock.isSynthesizing = true;
    await mountSelected();
    callOrder = [];
    wsMock.stopAll.mockClear();
    wsMock.stopVoice.mockClear();

    openAction('a1', '回到这里…');
    fireEvent.click(screen.getByRole('button', { name: '确认回到这里' }));
    expect(await screen.findByText('她正在回复这一轮，请先停掉当前回复后再试。')).toBeInTheDocument();
    expect(wsMock.stopAll).not.toHaveBeenCalled();
    expect(wsMock.stopVoice).not.toHaveBeenCalled();
    expect(wsMock.applyTruncate).not.toHaveBeenCalled();
    const truncateCalls = callOrder.filter((call) => call.startsWith('truncate:'));
    expect(truncateCalls).toHaveLength(1);
    expect(JSON.parse(truncateCalls[0].slice('truncate:'.length))).toEqual({
      from_message_id: 12,
      expected_last_message_id: 13,
    });
  });

  it('updates the confirm dialog count without stopping audio or posting truncate when scope changes while open', async () => {
    wsMock.isPlaying = true;
    await mountSelected();
    const existing = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation((url, init) => {
      if (String(url).includes('/api/conversations/conv-1/messages?')) {
        return Promise.resolve(Response.json([
          ...SAMPLE.map((m) => ({ id: m.dbId, role: m.role, content: m.content, translation: m.translation })),
          { id: 14, role: 'user', turn_id: 't3', content: '后台新落库的第三轮' },
          { id: 15, role: 'assistant', turn_id: 't3', content: '新しい答えよ。', translation: '新回答。' },
        ]));
      }
      return existing(url, init);
    });
    callOrder = [];
    wsMock.stopAll.mockClear();
    wsMock.stopVoice.mockClear();

    openAction('u2', '改这句重发…');
    const dialog = screen.getByRole('alertdialog');
    expect(within(dialog).getByText('1 轮')).toBeInTheDocument();
    expect(within(dialog).getByText(/将删除从这句起的 1 轮对话/)).toBeInTheDocument();
    fireEvent.change(screen.getByTestId('truncate-edit-textarea'), { target: { value: '改好的第二句' } });

    fireEvent.click(screen.getByRole('button', { name: '截断并重发' }));
    expect(await within(dialog).findByText('对话内容刚刚有更新，请刷新画面确认后再试。')).toBeInTheDocument();
    expect(within(dialog).getByText('2 轮')).toBeInTheDocument();
    expect(within(dialog).getByText(/将删除从这句起的 2 轮对话/)).toBeInTheDocument();
    expect(within(dialog).getByText(/她会忘掉只在这 2 轮里说过的事/)).toBeInTheDocument();
    expect(screen.getByTestId('truncate-edit-textarea')).toHaveValue('改好的第二句');
    expect(callOrder.filter((call) => call.startsWith('truncate:'))).toHaveLength(0);
    expect(wsMock.stopAll).not.toHaveBeenCalled();
    expect(wsMock.stopVoice).not.toHaveBeenCalled();

    // Re-confirming after seeing the updated count stops audio and sends the new last message id.
    fireEvent.click(screen.getByRole('button', { name: '截断并重发' }));
    await waitFor(() => expect(wsMock.applyTruncate).toHaveBeenCalledWith(12));
    expect(wsMock.stopAll).toHaveBeenCalled();
    expect(wsMock.stopVoice).toHaveBeenCalled();
    const truncateCalls = callOrder.filter((call) => call.startsWith('truncate:'));
    expect(truncateCalls).toHaveLength(1);
    expect(JSON.parse(truncateCalls[0].slice('truncate:'.length))).toEqual({
      from_message_id: 12,
      expected_last_message_id: 15,
    });
  });

  it('reports connection error without stopping audio or posting truncate when history verification fails', async () => {
    wsMock.isPlaying = true;
    await mountSelected();
    const composer = screen.getByLabelText('输入消息') as HTMLTextAreaElement;
    fireEvent.change(composer, { target: { value: '原有草稿历史失败' } });
    const existing = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation((url, init) => {
      if (String(url).includes('/api/conversations/conv-1/messages?')) {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      return existing(url, init);
    });
    callOrder = [];
    wsMock.stopAll.mockClear();
    wsMock.stopVoice.mockClear();

    openAction('u2', '改这句重发…');
    fireEvent.change(screen.getByTestId('truncate-edit-textarea'), { target: { value: '读历史失败保留稿' } });
    fireEvent.click(screen.getByRole('button', { name: '截断并重发' }));
    expect(await screen.findByText('回退对话时连接失败，请稍后再试。')).toBeInTheDocument();
    expect(callOrder.filter((call) => call.startsWith('truncate:'))).toHaveLength(0);
    expect(wsMock.stopAll).not.toHaveBeenCalled();
    expect(wsMock.stopVoice).not.toHaveBeenCalled();
    expect(screen.getByTestId('truncate-edit-textarea')).toHaveValue('读历史失败保留稿');

    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(composer.value).toContain('读历史失败保留稿');
    expect(composer.value).toContain('原有草稿历史失败');
  });

  it('closes dialog, preserves edit draft, and reloads without posting truncate when target message is gone in history', async () => {
    await mountSelected();
    const existing = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation((url, init) => {
      if (String(url).includes('/api/conversations/conv-1/messages?')) {
        return Promise.resolve(Response.json(SAMPLE.slice(0, 2).map((m) => ({
          id: m.dbId, role: m.role, content: m.content, translation: m.translation,
        }))));
      }
      return existing(url, init);
    });
    callOrder = [];

    openAction('u2', '改这句重发…');
    fireEvent.change(screen.getByTestId('truncate-edit-textarea'), { target: { value: '目标已删保留稿' } });
    fireEvent.click(screen.getByRole('button', { name: '截断并重发' }));
    expect(await screen.findByTestId('message-action-toast')).toHaveTextContent('改好的话已留在输入框里');
    expect(screen.queryByTestId('truncate-edit-textarea')).not.toBeInTheDocument();
    expect(callOrder.filter((call) => call.startsWith('truncate:'))).toHaveLength(0);
    expect((screen.getByLabelText('输入消息') as HTMLTextAreaElement).value).toContain('目标已删保留稿');
    await waitFor(() => expect(wsMock.switchConversation).toHaveBeenCalledTimes(2));
  });

  it('rejects immediately with conversation_busy without waiting or stopping voice when foreground reply is still writing', async () => {
    truncateStatus = 409;
    truncateBody = { detail: { code: 'conversation_busy' } };
    wsMock.messages = [
      ...SAMPLE.map((m) => ({ ...m })),
      { id: 'u3', turnId: 't3', role: 'user', content: '第三轮还在写' },
      { id: 'a3', turnId: 't3', role: 'assistant', streaming: true, content: 'まだ途中よ。' },
    ];
    wsMock.isThinking = true;
    wsMock.isTurnActive = true;
    wsMock.isSynthesizing = false;
    wsMock.isPlaying = false;
    const view = await mountSelected();
    const composer = screen.getByLabelText('输入消息') as HTMLTextAreaElement;
    fireEvent.change(composer, { target: { value: '原有草稿前台忙' } });
    callOrder = [];
    wsMock.stopAll.mockClear();
    wsMock.stopVoice.mockClear();

    // 1) Foreground still thinking: must not wait, must not stop voice, must surface busy and keep edit draft.
    openAction('u2', '改这句重发…');
    fireEvent.change(screen.getByTestId('truncate-edit-textarea'), { target: { value: '前台忙时改句' } });
    fireEvent.click(screen.getByRole('button', { name: '截断并重发' }));
    expect(await screen.findByText('她正在回复这一轮，请先停掉当前回复后再试。')).toBeInTheDocument();
    expect(callOrder.filter((call) => call.startsWith('truncate:'))).toHaveLength(1);
    expect(wsMock.stopAll).not.toHaveBeenCalled();
    expect(wsMock.stopVoice).not.toHaveBeenCalled();
    expect(wsMock.applyTruncate).not.toHaveBeenCalled();
    expect(screen.getByTestId('truncate-edit-textarea')).toHaveValue('前台忙时改句');

    // 2) Foreground streaming text without audio playing (including TTS synthesizing before playback): same immediate busy rejection.
    wsMock.isThinking = false;
    wsMock.isTurnActive = true;
    wsMock.isSynthesizing = true;
    wsMock.isPlaying = false;
    view.rerender(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '截断并重发' }));
    await waitFor(() => expect(callOrder.filter((call) => call.startsWith('truncate:'))).toHaveLength(2));
    expect(screen.getByText('她正在回复这一轮，请先停掉当前回复后再试。')).toBeInTheDocument();
    expect(wsMock.stopAll).not.toHaveBeenCalled();
    expect(wsMock.stopVoice).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(composer.value).toContain('前台忙时改句');
    expect(composer.value).toContain('原有草稿前台忙');
  });

  it('rejects immediately with conversation_busy when returning to a background-running conversation', async () => {
    truncateStatus = 409;
    truncateBody = { detail: { code: 'conversation_busy' } };
    wsMock.messages = [
      ...SAMPLE.map((m) => ({ ...m })),
      { id: 'u3', dbId: 14, role: 'user', content: '切走前发出的第三轮' },
    ];
    wsMock.isThinking = false;
    wsMock.isTurnActive = true;
    wsMock.isSynthesizing = false;
    wsMock.isPlaying = false;
    const view = await mountSelected();
    wsMock.conversationTurns = { 'conv-1': { running: true, queued: [] } };
    view.rerender(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    expect(screen.getByText('她还在回复。')).toBeInTheDocument();
    const composer = screen.getByLabelText('输入消息') as HTMLTextAreaElement;
    fireEvent.change(composer, { target: { value: '原有草稿后台忙' } });
    callOrder = [];
    wsMock.stopAll.mockClear();
    wsMock.stopVoice.mockClear();

    openAction('u2', '改这句重发…');
    fireEvent.change(screen.getByTestId('truncate-edit-textarea'), { target: { value: '后台忙时改句' } });
    fireEvent.click(screen.getByRole('button', { name: '截断并重发' }));
    expect(await screen.findByText('她正在回复这一轮，请先停掉当前回复后再试。')).toBeInTheDocument();
    expect(callOrder.filter((call) => call.startsWith('truncate:'))).toHaveLength(1);
    expect(wsMock.stopAll).not.toHaveBeenCalled();
    expect(wsMock.stopVoice).not.toHaveBeenCalled();
    expect(wsMock.applyTruncate).not.toHaveBeenCalled();
    expect(screen.getByTestId('truncate-edit-textarea')).toHaveValue('后台忙时改句');

    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(composer.value).toContain('后台忙时改句');
    expect(composer.value).toContain('原有草稿后台忙');
  });

  it('stops playback, hydrates missing message ids, and truncates once when reply text is finished and only audio is playing', async () => {
    wsMock.messages = [
      ...SAMPLE.map((m) => ({ ...m })),
      { id: 'u3', turnId: 't3', role: 'user', content: '第三轮问题' },
      { id: 'a3', turnId: 't3', role: 'assistant', streaming: false, content: '三つ目の答えよ。', translation: '第三个回答。' },
    ];
    wsMock.isPlaying = true;
    wsMock.isThinking = false;
    wsMock.isTurnActive = false;
    wsMock.isSynthesizing = false;
    await mountSelected();
    const existing = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation((url, init) => {
      if (String(url).includes('/api/conversations/conv-1/messages?')) {
        return Promise.resolve(Response.json([
          ...SAMPLE.map((m) => ({ id: m.dbId, role: m.role, content: m.content, translation: m.translation })),
          { id: 14, role: 'user', turn_id: 't3', content: '第三轮问题' },
          { id: 15, role: 'assistant', turn_id: 't3', content: '三つ目の答えよ。', translation: '第三个回答。' },
        ]));
      }
      return existing(url, init);
    });
    callOrder = [];

    openAction('a1', '回到这里…');
    fireEvent.click(screen.getByRole('button', { name: '确认回到这里' }));
    await waitFor(() => expect(wsMock.applyTruncate).toHaveBeenCalledWith(12));
    expect(wsMock.stopAll).toHaveBeenCalled();
    expect(wsMock.stopVoice).toHaveBeenCalled();
    const truncateCalls = callOrder.filter((call) => call.startsWith('truncate:'));
    expect(truncateCalls).toHaveLength(1);
    expect(JSON.parse(truncateCalls[0].slice('truncate:'.length))).toEqual({
      from_message_id: 12,
      expected_last_message_id: 15,
    });
  });

  it('re-verifies deleted turn count from delayed history when a persisted zero-output turn precedes a newly completed turn', async () => {
    wsMock.messages = [
      { id: 'u1', dbId: 101, role: 'user', content: '第一轮问题' },
      { id: 'a1', dbId: 102, role: 'assistant', content: '一。', translation: '一。' },
      { id: 'u2', dbId: 105, role: 'user', content: '第二轮问题' },
      { id: 'a2', dbId: 106, role: 'assistant', content: '二。', translation: '二。' },
      // Persisted zero-output turn not yet reconciled onto screen, followed by a newly completed turn.
      { id: 'u4', turnId: 't4', role: 'user', content: '刚完成的第四轮' },
      { id: 'a4', turnId: 't4', role: 'assistant', streaming: false, content: '四。', translation: '四。' },
    ];
    wsMock.isPlaying = true;
    wsMock.isThinking = false;
    wsMock.isTurnActive = false;
    wsMock.isSynthesizing = false;
    await mountSelected();
    const existing = vi.mocked(fetch).getMockImplementation()!;
    const fullHistory = [
      { id: 101, role: 'user', content: '第一轮问题' },
      { id: 102, role: 'assistant', content: '一。', translation: '一。' },
      { id: 105, role: 'user', content: '第二轮问题' },
      { id: 106, role: 'assistant', content: '二。', translation: '二。' },
      { id: 107, role: 'user', turn_id: 't3', content: '保存后零输出的第三轮' },
      { id: 108, role: 'assistant', turn_id: 't3', content: '中断。', translation: '已中断。' },
      { id: 109, role: 'user', turn_id: 't4', content: '刚完成的第四轮' },
      { id: 110, role: 'assistant', turn_id: 't4', content: '四。', translation: '四。' },
    ];
    let resolveDelayedHistory: ((res: Response) => void) | null = null;
    vi.mocked(fetch).mockImplementation((url, init) => {
      if (String(url).includes('/api/conversations/conv-1/messages?')) {
        if (!resolveDelayedHistory) {
          return new Promise<Response>((resolve) => { resolveDelayedHistory = resolve; });
        }
        return Promise.resolve(Response.json(fullHistory));
      }
      return existing(url, init);
    });
    callOrder = [];
    wsMock.stopAll.mockClear();
    wsMock.stopVoice.mockClear();

    // Edit on u2: initially displays 2 turns (u2 + u4, missing zero-output u3 until history arrives).
    openAction('u2', '改这句重发…');
    const dialog = screen.getByRole('alertdialog');
    expect(within(dialog).getByText('2 轮')).toBeInTheDocument();
    expect(within(dialog).getByText(/将删除从这句起的 2 轮对话/)).toBeInTheDocument();
    fireEvent.change(screen.getByTestId('truncate-edit-textarea'), { target: { value: '保留改句零输出回归' } });

    // Confirm triggers delayed history read; once history arrives with 3 persisted user turns, must NOT post truncate yet.
    fireEvent.click(screen.getByRole('button', { name: '截断并重发' }));
    await waitFor(() => expect(resolveDelayedHistory).not.toBeNull());
    await act(async () => resolveDelayedHistory!(Response.json(fullHistory)));

    expect(await within(dialog).findByText('对话内容刚刚有更新，请刷新画面确认后再试。')).toBeInTheDocument();
    expect(within(dialog).getByText('3 轮')).toBeInTheDocument();
    expect(within(dialog).getByText(/将删除从这句起的 3 轮对话/)).toBeInTheDocument();
    expect(within(dialog).getByText(/她会忘掉只在这 3 轮里说过的事/)).toBeInTheDocument();
    expect(screen.getByTestId('truncate-edit-textarea')).toHaveValue('保留改句零输出回归');
    expect(callOrder.filter((call) => call.startsWith('truncate:'))).toHaveLength(0);
    expect(wsMock.stopAll).not.toHaveBeenCalled();
    expect(wsMock.stopVoice).not.toHaveBeenCalled();

    // Re-confirming after seeing the verified count of 3 turns sends from=105, last=110 and succeeds.
    fireEvent.click(screen.getByRole('button', { name: '截断并重发' }));
    await waitFor(() => expect(wsMock.applyTruncate).toHaveBeenCalledWith(105));
    expect(wsMock.stopAll).toHaveBeenCalled();
    expect(wsMock.stopVoice).toHaveBeenCalled();
    const truncateCalls = callOrder.filter((call) => call.startsWith('truncate:'));
    expect(truncateCalls).toHaveLength(1);
    expect(JSON.parse(truncateCalls[0].slice('truncate:'.length))).toEqual({
      from_message_id: 105,
      expected_last_message_id: 110,
    });
  });

  it('includes an unhydrated playing turn in both stamp and body turn counts of the confirm dialog', async () => {
    wsMock.messages = [
      { id: 'u1', dbId: 1, role: 'user', content: '第一轮' },
      { id: 'a1', dbId: 2, role: 'assistant', content: '一。', translation: '一。' },
      { id: 'u2', dbId: 3, role: 'user', content: '第二轮' },
      { id: 'a2', dbId: 4, role: 'assistant', content: '二。', translation: '二。' },
      { id: 'u3', dbId: 5, role: 'user', content: '第三轮' },
      { id: 'a3', dbId: 6, role: 'assistant', content: '三。', translation: '三。' },
      { id: 'u4', dbId: 7, role: 'user', content: '第四轮' },
      { id: 'a4', dbId: 8, role: 'assistant', content: '四。', translation: '四。' },
      { id: 'u5', turnId: 't5', role: 'user', content: '正在播的第五轮' },
      { id: 'a5', turnId: 't5', role: 'assistant', streaming: true, content: '五。', translation: '五。' },
    ];
    wsMock.isPlaying = true;
    wsMock.isTurnActive = true;
    wsMock.isSynthesizing = true;
    await mountSelected();

    openAction('a1', '回到这里…');
    const dialog = screen.getByRole('alertdialog');
    expect(within(dialog).getByText('4 轮')).toBeInTheDocument();
    expect(within(dialog).getByText(/将删除之后的 4 轮对话/)).toBeInTheDocument();
    expect(within(dialog).getByText(/她会忘掉只在这 4 轮里说过的事/)).toBeInTheDocument();
  });

  it('preserves edited text and existing composer draft on truncate 409 and network error', async () => {
    await mountSelected();
    const composer = screen.getByLabelText('输入消息') as HTMLTextAreaElement;

    // 1) 409 conversation_changed closes the modal and merges edited text + existing draft into composer.
    truncateStatus = 409;
    truncateBody = { detail: { code: 'conversation_changed' } };
    fireEvent.change(composer, { target: { value: '原有草稿409' } });
    openAction('u2', '改这句重发…');
    fireEvent.change(screen.getByTestId('truncate-edit-textarea'), { target: { value: '改好的话409' } });
    fireEvent.click(screen.getByRole('button', { name: '截断并重发' }));
    expect(await screen.findByTestId('message-action-toast')).toHaveTextContent('改好的话已留在输入框里');
    expect(screen.queryByTestId('truncate-edit-textarea')).not.toBeInTheDocument();
    expect(composer.value).toContain('改好的话409');
    expect(composer.value).toContain('原有草稿409');

    // 2) Other non-2xx (e.g. 409 conversation_busy) keeps both texts alive and preserves both on cancel.
    truncateStatus = 409;
    truncateBody = { detail: { code: 'conversation_busy' } };
    fireEvent.change(composer, { target: { value: '原有草稿忙' } });
    openAction('u2', '改这句重发…');
    fireEvent.change(screen.getByTestId('truncate-edit-textarea'), { target: { value: '改好的话忙' } });
    fireEvent.click(screen.getByRole('button', { name: '截断并重发' }));
    expect(await screen.findByText('她正在回复这一轮，请先停掉当前回复后再试。')).toBeInTheDocument();
    expect(screen.getByTestId('truncate-edit-textarea')).toHaveValue('改好的话忙');
    expect(composer).toHaveValue('原有草稿忙');
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(composer.value).toContain('改好的话忙');
    expect(composer.value).toContain('原有草稿忙');

    // 3) Network failure keeps both texts alive while open and preserves both in composer if cancelled.
    const existing = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation((url, init) => {
      if (String(url).includes('/api/conversations/conv-1/truncate')) {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      return existing(url, init);
    });
    fireEvent.change(composer, { target: { value: '原有草稿断网' } });
    openAction('u2', '改这句重发…');
    fireEvent.change(screen.getByTestId('truncate-edit-textarea'), { target: { value: '改好的话断网' } });
    fireEvent.click(screen.getByRole('button', { name: '截断并重发' }));
    expect(await screen.findByText('回退对话时连接失败，请稍后再试。')).toBeInTheDocument();
    expect(screen.getByTestId('truncate-edit-textarea')).toHaveValue('改好的话断网');
    expect(composer).toHaveValue('原有草稿断网');
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(composer.value).toContain('改好的话断网');
    expect(composer.value).toContain('原有草稿断网');
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
