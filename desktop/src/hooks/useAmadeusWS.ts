import { useState, useEffect, useLayoutEffect, useRef, useCallback } from 'react';

export interface UseAmadeusWSParams {
  sessionId?: string;
  conversationId?: string | null;
  systemPrompt: string;
  temperature: number;
  worldline: 'steins_gate' | 'beta';
  enableTts: boolean;
  sovitsUrl: string;
  providerId?: string;
  model: string;
  reasoningEffort: string | null;
  /** Settings default for draft materialize / new conversation (never inferred). */
  identityMode?: 'okabe' | 'self';
  /** Slice B: app-level self-mode call name (empty = unset); resent on every auth. */
  selfName?: string;
  onAudioChunk?: (sentenceId: number, base64Data: string) => void;
  onAudioDegraded?: (reason: string) => void;
  onSessionReady?: (details: {
    conversationMode?: 'draft' | 'history';
    conversationId?: string | null;
    worldline?: 'steins_gate' | 'beta';
    generation?: number;
  }) => void;
  onConversationSwitched?: (details: {
    conversationId: string;
    worldline?: 'steins_gate' | 'beta';
    generation?: number;
  }) => void;
  onConversationSwitchError?: (details: {
    conversationId: string;
    code: string;
    message?: string;
  }) => void;
  onWorldlineSwitched?: (details: {
    worldline: 'steins_gate' | 'beta';
    generation?: number;
  }) => void;
  onWorldlineSwitchError?: (details: {
    worldline: 'steins_gate' | 'beta';
    code: string;
    message?: string;
  }) => void;
  onTurnStarted?: (conversationId?: string | null) => void;
  onTurnCompleted?: () => void;
  /** Backend-normalized config after config_update (e.g. reasoning_effort aliases). */
  onConfigApplied?: (details: {
    reasoningEffort?: string | null;
  }) => void;
}

export type WSStatus = 'CONNECTING' | 'READY' | 'ERROR' | 'CLOSED';

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  translation?: string;
  translationScope?: 'turn';
  emotion?: 'neutral' | 'tsundere' | 'embarrassed' | 'intellectual' | 'happy' | 'surprised' | 'annoyed' | 'disappointed' | 'sad';
  turnId?: string;
  segments?: Array<{ id: number; ja: string; zh: string; translationError?: string }>;
  streaming?: boolean;
  systemError?: boolean;
}

export interface UseAmadeusWSReturn {
  status: WSStatus;
  /** Optional so existing test doubles keep compiling; undefined is treated as usable. */
  sessionState?: 'unknown' | 'ready' | 'rejected';
  messages: Message[];
  isThinking: boolean;
  isSynthesizing: boolean;
  activeTool: string | null;
  activeNotice: string | null;
  emotion: 'neutral' | 'tsundere' | 'embarrassed' | 'intellectual' | 'happy' | 'surprised' | 'annoyed' | 'disappointed' | 'sad';
  clearHistory: () => void;
  sendChat: (content: string) => void;
  stopVoice: () => void;
  cancelTurn: () => void;
  sendDebugTts: (emotion: string, text: string) => void;
  updateConfig: (patch: Partial<Omit<UseAmadeusWSParams, 'onAudioChunk'>>) => void;
  reconnect: () => void;
  /** Optional so existing test doubles keep compiling. */
  resetToDraft?: () => void;
  switchConversation: (conversationId: string, history: Message[]) => boolean;
  switchWorldline: (worldline: 'steins_gate' | 'beta') => boolean;
  wsRef: React.MutableRefObject<WebSocket | null>;
}

function createRequestId(prefix: string): string {
  const suffix = typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${suffix}`;
}

export function getOrCreateAmadeusSessionId(): string {
  let sessionId = '';
  try {
    sessionId = localStorage.getItem('amadeus_pc_session_id') || '';
  } catch (error) {
    console.warn('LocalStorage access blocked:', error);
  }
  if (!sessionId) {
    sessionId = typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID()
      : Math.random().toString(36).substring(2, 15);
    try {
      localStorage.setItem('amadeus_pc_session_id', sessionId);
    } catch (error) {
      console.warn('LocalStorage write failed:', error);
    }
  }
  return sessionId;
}

export function useAmadeusWS(params: UseAmadeusWSParams): UseAmadeusWSReturn {
  const [status, setStatus] = useState<WSStatus>('CLOSED');
  /** An open socket is not a usable session: 'unknown' after open, 'ready' only on the server's session.ready
   *  (sent after auth + lease), 'rejected' when an error frame arrives before that. */
  const [sessionState, setSessionState] = useState<'unknown' | 'ready' | 'rejected'>('unknown');
  const sessionStateRef = useRef<'unknown' | 'ready' | 'rejected'>('unknown');
  const setSessionReady = (ready: boolean) => {
    sessionStateRef.current = ready ? 'ready' : 'unknown';
    setSessionState(sessionStateRef.current);
  };
  const [messages, setMessages] = useState<Message[]>([]);
  const [isThinking, setIsThinking] = useState(false);
  const [isSynthesizing, setIsSynthesizing] = useState(false);
  const [activeTool, setActiveTool] = useState<string | null>(null);
  const [activeNotice, setActiveNotice] = useState<string | null>(null);
  const [emotion, setEmotion] = useState<UseAmadeusWSReturn['emotion']>('neutral');
  const messagesRef = useRef<Message[]>([]);
  messagesRef.current = messages;

  const ALLOWED_EMOTIONS: ReadonlySet<string> = new Set([
    'neutral', 'tsundere', 'embarrassed', 'intellectual',
    'happy', 'surprised', 'annoyed', 'disappointed', 'sad',
  ]);
  const lastSentenceIdRef = useRef<number>(-1);
  const activeTurnIdRef = useRef<string>('');
  const activeGenerationRef = useRef<number>(-1);
  const activeConversationIdRef = useRef<string | null>(params.conversationId || null);
  const pendingConversationSwitchRef = useRef<{
    requestId: string;
    conversationId: string;
    history: Message[];
  } | null>(null);
  const pendingWorldlineSwitchRef = useRef<{
    requestId: string;
    worldline: 'steins_gate' | 'beta';
    /** Pre-switch transcript for one pending switch; discarded on ACK, restored on correlated error. */
    transcriptSnapshot: Message[];
  } | null>(null);
  /** Discard late config_applied after provider/conversation/model switch. */
  const configAppliedEpochRef = useRef(0);
  const pendingConfigAppliedRef = useRef<{
    epoch: number;
    providerId?: string;
    model: string;
    conversationId?: string | null;
  } | null>(null);
  const configContextKeyRef = useRef(
    `${params.providerId || ''}|${params.model}|${params.conversationId || ''}`,
  );

  const invalidatePendingConfigApplied = useCallback(() => {
    configAppliedEpochRef.current += 1;
    pendingConfigAppliedRef.current = null;
  }, []);

  const wsRef = useRef<WebSocket | null>(null);
  const paramsRef = useRef<UseAmadeusWSParams>(params);
  
  // Sync before paint so a late WS config_applied cannot observe stale
  // paramsRef/pending in the post-commit, pre-useEffect window.
  useLayoutEffect(() => {
    paramsRef.current = params;
    const nextKey = `${params.providerId || ''}|${params.model}|${params.conversationId || ''}`;
    if (nextKey !== configContextKeyRef.current) {
      configContextKeyRef.current = nextKey;
      invalidatePendingConfigApplied();
    }
  }, [params, invalidatePendingConfigApplied]);

  const heartbeatTimerRef = useRef<number | null>(null);
  const watchdogTimerRef = useRef<number | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectCountRef = useRef(0);
  const voiceStoppedRef = useRef(false);
  /** Legacy v1 per-send degradation flag (text_chunk has no turn identity). */
  const audioDegradedNotifiedRef = useRef(false);
  /** B1 v2: dedup bound to the server-accepted {turn_id, generation}, never to sendChat. */
  const audioDegradedV2Ref = useRef<{ turnId: string; generation: number; notified: boolean } | null>(null);

  // Stop heartbeat
  const stopHeartbeat = () => {
    if (heartbeatTimerRef.current) {
      window.clearInterval(heartbeatTimerRef.current);
      heartbeatTimerRef.current = null;
    }
  };

  // Start heartbeat (ping every 30s)
  const startHeartbeat = () => {
    stopHeartbeat();
    heartbeatTimerRef.current = window.setInterval(() => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'ping' }));
      }
    }, 30000);
  };

  // Clear watchdog
  const clearWatchdog = () => {
    if (watchdogTimerRef.current) {
      window.clearTimeout(watchdogTimerRef.current);
      watchdogTimerRef.current = null;
    }
  };

  const ABORT_NOTICE = '这一问中断了，没有收到回复。请再发一次。';

  const surfaceAbortedTurn = () => {
    setIsThinking(false);
    setIsSynthesizing(false);
    setMessages((prev) => {
      const lastUserIndex = prev.reduce(
        (last, message, index) => message.role === 'user' ? index : last,
        -1,
      );
      const currentTurnMessages = prev.slice(lastUserIndex + 1);
      const hasReply = currentTurnMessages.some((message) => (
        message.role === 'assistant'
        && !message.systemError
        && (
          Boolean(message.content && message.content.trim())
          || Boolean(message.segments && message.segments.length)
        )
      ));
      const settled = prev.map((message) => (
        message.streaming ? { ...message, streaming: false } : message
      ));
      if (hasReply || currentTurnMessages.some((message) => message.systemError && message.content === ABORT_NOTICE)) {
        return settled;
      }
      return [
        ...settled,
        {
          id: `err_abort_${Date.now()}`,
          role: 'assistant' as const,
          content: ABORT_NOTICE,
          systemError: true,
        },
      ];
    });
  };

  // Start 30s watchdog timer
  const startWatchdog = () => {
    clearWatchdog();
    watchdogTimerRef.current = window.setTimeout(() => {
      console.warn('Watchdog timed out (30s). Closing socket due to silence.');
      surfaceAbortedTurn();
      setStatus('ERROR');
      if (wsRef.current) {
        wsRef.current.close();
      }
    }, 30000);
  };

  const disconnect = useCallback(() => {
    stopHeartbeat();
    clearWatchdog();
    if (reconnectTimerRef.current) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.onerror = null;
      wsRef.current.onmessage = null;
      wsRef.current.close();
      wsRef.current = null;
    }
    setStatus('CLOSED');
    setIsThinking(false);
    setIsSynthesizing(false);
    voiceStoppedRef.current = false;
    audioDegradedNotifiedRef.current = false;
    audioDegradedV2Ref.current = null;
    activeTurnIdRef.current = '';
    activeGenerationRef.current = -1;
    activeConversationIdRef.current = null;
    if (pendingConversationSwitchRef.current) {
      paramsRef.current.onConversationSwitchError?.({
        conversationId: pendingConversationSwitchRef.current.conversationId,
        code: 'connection_closed',
      });
    }
    if (pendingWorldlineSwitchRef.current) {
      paramsRef.current.onWorldlineSwitchError?.({
        worldline: pendingWorldlineSwitchRef.current.worldline,
        code: 'connection_closed',
      });
    }
    pendingConversationSwitchRef.current = null;
    pendingWorldlineSwitchRef.current = null;
    setActiveTool(null);
    setActiveNotice(null);
    setMessages((prev) => prev.map((message) =>
      message.streaming ? { ...message, streaming: false } : message
    ));
  }, []);

  const connect = useCallback(() => {
    disconnect();
    setStatus('CONNECTING');

    const sessionId = paramsRef.current.sessionId || getOrCreateAmadeusSessionId();

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // Tauri/Vite Config Proxy will route /ws to backend ws endpoint
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/chat?session_id=${sessionId}`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      if (wsRef.current !== ws) return;
      reconnectCountRef.current = 0;
      setSessionReady(false);
      setStatus('READY');

      // Send auth frame immediately as first frame
      const conversationId = paramsRef.current.conversationId || null;
      const authFrame = {
        type: 'auth',
        ...(conversationId
          ? { conversation_id: conversationId, conversation_mode: 'history' as const }
          : { conversation_mode: 'draft' as const }),
        sovits_url: paramsRef.current.sovitsUrl,
        provider_id: paramsRef.current.providerId || 'deepseek',
        model: paramsRef.current.model,
        system_prompt: paramsRef.current.systemPrompt,
        enable_tts: paramsRef.current.enableTts,
        temperature: paramsRef.current.temperature,
        worldline: paramsRef.current.worldline,
        reasoning_effort: paramsRef.current.reasoningEffort,
        default_identity_mode: paramsRef.current.identityMode || 'okabe',
        self_name: paramsRef.current.selfName ?? '',
        client: 'desktop',
        protocol_version: 2,
      };
      ws.send(JSON.stringify(authFrame));
      startHeartbeat();
    };

    let activeAssistantMessageId = '';

    ws.onmessage = (event) => {
      if (wsRef.current !== ws) return;
      try {
        const msg = JSON.parse(event.data);

        
        switch (msg.type) {
          case 'session.ready':
            setSessionReady(true);
            if (msg.conversation_mode === 'draft') {
              activeConversationIdRef.current = null;
            } else if (msg.conversation_id) {
              activeConversationIdRef.current = msg.conversation_id;
            }
            paramsRef.current.onSessionReady?.({
              conversationMode: msg.conversation_mode,
              conversationId: msg.conversation_id || null,
              worldline: msg.worldline,
              generation: msg.generation,
            });
            break;

          case 'conversation.switched': {
            const pending = pendingConversationSwitchRef.current;
            if (
              !pending ||
              msg.request_id !== pending.requestId ||
              msg.conversation_id !== pending.conversationId
            ) break;
            pendingConversationSwitchRef.current = null;
            activeConversationIdRef.current = pending.conversationId;
            if (typeof msg.generation === 'number') {
              activeGenerationRef.current = msg.generation;
            }
            setMessages(pending.history);
            paramsRef.current.onConversationSwitched?.({
              conversationId: pending.conversationId,
              worldline: msg.worldline,
              generation: msg.generation,
            });
            break;
          }

          case 'conversation.switch_error': {
            const pending = pendingConversationSwitchRef.current;
            if (!pending || msg.request_id !== pending.requestId) break;
            pendingConversationSwitchRef.current = null;
            paramsRef.current.onConversationSwitchError?.({
              conversationId: pending.conversationId,
              code: msg.code || 'switch_failed',
              message: msg.message,
            });
            break;
          }

          case 'worldline.switched': {
            const pending = pendingWorldlineSwitchRef.current;
            if (!pending || msg.request_id !== pending.requestId) break;
            // Discard snapshot: successful switch keeps the target draft empty.
            pendingWorldlineSwitchRef.current = null;
            activeConversationIdRef.current = null;
            activeTurnIdRef.current = '';
            activeGenerationRef.current = -1;
            setMessages([]);
            setEmotion('neutral');
            paramsRef.current.onWorldlineSwitched?.({
              worldline: pending.worldline,
              generation: msg.generation,
            });
            break;
          }

          case 'worldline.switch_error': {
            const pending = pendingWorldlineSwitchRef.current;
            if (!pending || msg.request_id !== pending.requestId) break;
            pendingWorldlineSwitchRef.current = null;
            setMessages(pending.transcriptSnapshot);
            paramsRef.current.onWorldlineSwitchError?.({
              worldline: pending.worldline,
              code: msg.code || 'switch_failed',
              message: msg.message,
            });
            break;
          }

          case 'turn.started':
            clearWatchdog();
            startWatchdog();
            if (msg.conversation_id) {
              activeConversationIdRef.current = msg.conversation_id;
            }
            paramsRef.current.onTurnStarted?.(msg.conversation_id || null);
            activeTurnIdRef.current = msg.turn_id;
            activeGenerationRef.current = msg.generation;
            activeAssistantMessageId = `assist_${msg.turn_id}`;
            lastSentenceIdRef.current = -1;
            voiceStoppedRef.current = false;
            audioDegradedNotifiedRef.current = false;
            // Authoritative new round: bind v2 dedup identity to the accepted turn.
            audioDegradedV2Ref.current = { turnId: msg.turn_id, generation: msg.generation, notified: false };
            setIsThinking(true);
            setMessages((prev) => [
              ...prev,
              {
                id: activeAssistantMessageId,
                role: 'assistant',
                content: '',
                translation: '',
                turnId: msg.turn_id,
                segments: [],
                streaming: true,
                emotion: 'neutral',
              },
            ]);
            break;

          case 'segment.ready': {
            if (
              msg.turn_id !== activeTurnIdRef.current ||
              msg.generation !== activeGenerationRef.current
            ) break;
            startWatchdog();
            setIsThinking(false);
            setIsSynthesizing(Boolean(paramsRef.current.enableTts) && !voiceStoppedRef.current);
            setActiveTool(null);
            setActiveNotice(null);
            if (msg.emotion && ALLOWED_EMOTIONS.has(msg.emotion)) {
              setEmotion(msg.emotion as UseAmadeusWSReturn['emotion']);
            }
            setMessages((prev) => prev.map((message) => {
              if (message.id !== activeAssistantMessageId) return message;
              const segments = [...(message.segments || [])];
              const index = segments.findIndex((segment) => segment.id === msg.segment_id);
              const nextSegment = {
                id: msg.segment_id,
                ja: msg.ja || '',
                zh: msg.zh || '',
                translationError: msg.translation_error || undefined,
              };
              if (index >= 0) segments[index] = nextSegment;
              else segments.push(nextSegment);
              segments.sort((a, b) => a.id - b.id);
              return {
                ...message,
                segments,
                content: segments.map((segment) => segment.ja).join(''),
                translation: segments.map((segment) => segment.zh).join(''),
                emotion: msg.emotion && ALLOWED_EMOTIONS.has(msg.emotion)
                  ? msg.emotion as Message['emotion']
                  : message.emotion,
              };
            }));
            break;
          }

          case 'segment.audio':
            if (
              msg.turn_id === activeTurnIdRef.current &&
              msg.generation === activeGenerationRef.current &&
              !voiceStoppedRef.current &&
              paramsRef.current.onAudioChunk && msg.data
            ) {
              paramsRef.current.onAudioChunk(msg.segment_id, msg.data);
            }
            break;

          case 'segment.audio_error': {
            if (
              msg.turn_id === activeTurnIdRef.current &&
              msg.generation === activeGenerationRef.current &&
              msg.reason !== 'voice_stopped'
            ) {
              if (msg.reason === 'user_disabled') setIsSynthesizing(false);
              // B1: backend keeps per-segment errors; surface at most one toast
              // per server-accepted turn. The slot is (re)armed only by
              // turn.started, so a busy resend can never re-arm the old turn.
              const slot = audioDegradedV2Ref.current;
              if (
                slot !== null &&
                slot.turnId === msg.turn_id &&
                slot.generation === msg.generation &&
                !slot.notified
              ) {
                slot.notified = true;
                paramsRef.current.onAudioDegraded?.(msg.reason || 'unknown');
              }
            }
            break;
          }

          case 'turn.heartbeat':
            // B1 liveness only: proves the turn/connection is alive during
            // legal provider/translation/TTS waits. Never drives content,
            // progress, completion, or success. Stale turns are ignored.
            if (
              msg.turn_id !== activeTurnIdRef.current ||
              msg.generation !== activeGenerationRef.current
            ) break;
            startWatchdog();
            break;

          case 'turn.translation_repaired':
            if (
              msg.turn_id !== activeTurnIdRef.current ||
              msg.generation !== activeGenerationRef.current
            ) break;
            setMessages((prev) => prev.map((message) => {
              if (message.id !== activeAssistantMessageId) return message;
              const segments = (message.segments || []).map((segment) => ({
                ...segment,
                translationError: undefined,
              }));
              return {
                ...message,
                translation: msg.content || '',
                translationScope: 'turn',
                segments,
              };
            }));
            break;

          case 'turn.completed':
            if (
              msg.turn_id !== activeTurnIdRef.current ||
              msg.generation !== activeGenerationRef.current
            ) break;
            clearWatchdog();
            setIsThinking(false);
            setIsSynthesizing(false);
            setActiveTool(null);
            setActiveNotice(null);
            setMessages((prev) => prev.map((message) =>
              message.id === activeAssistantMessageId ? { ...message, streaming: false } : message
            ));
            paramsRef.current.onTurnCompleted?.();
            break;

          case 'turn.cancelled':
            if (msg.turn_id !== activeTurnIdRef.current) break;
            clearWatchdog();
            setIsThinking(false);
            setIsSynthesizing(false);
            setActiveTool(null);
            setActiveNotice(null);
            setMessages((prev) => prev.map((message) =>
              message.id === activeAssistantMessageId ? { ...message, streaming: false } : message
            ));
            break;

          case 'config_applied': {
            // Backend may alias/normalize thinking controls; surface effective values
            // only when this socket still owns the matching provider/model context.
            const pending = pendingConfigAppliedRef.current;
            if (
              !pending
              || pending.epoch !== configAppliedEpochRef.current
              || pending.providerId !== paramsRef.current.providerId
              || pending.model !== paramsRef.current.model
              || (pending.conversationId || null) !== (paramsRef.current.conversationId || null)
            ) {
              break;
            }
            // Consume once: clear before callback so duplicate frames cannot reapply.
            pendingConfigAppliedRef.current = null;
            if ('reasoning_effort' in msg) {
              const value = msg.reasoning_effort;
              paramsRef.current.onConfigApplied?.({
                reasoningEffort:
                  value === null || typeof value === 'string' ? value : undefined,
              });
            }
            break;
          }

          case 'status':
            if (msg.notice) {
              setActiveNotice(msg.notice);
              clearWatchdog();
              watchdogTimerRef.current = window.setTimeout(() => {
                console.warn('Watchdog timed out (60s due to notice). Closing socket due to silence.');
                setStatus('ERROR');
                if (wsRef.current) {
                  wsRef.current.close();
                }
              }, 60000);
            } else {
              setActiveNotice(null);
            }
            if (msg.tool) {
              setActiveTool(msg.tool);
            } else {
              setActiveTool(null);
            }
            if (msg.dsk === 'thinking') {
              setIsThinking(true);
            } else if (msg.dsk === 'idle') {
              setIsThinking(false);
            }
            if (msg.tts === 'synthesizing') {
              setIsSynthesizing(Boolean(paramsRef.current.enableTts) && !voiceStoppedRef.current);
            } else if (msg.tts === 'idle') {
              setIsSynthesizing(false);
            }
            break;

          case 'text_chunk':
            startWatchdog();
            setIsThinking(false);
            setActiveTool(null);
            setActiveNotice(null);
            if (
              msg.emotion &&
              ALLOWED_EMOTIONS.has(msg.emotion) &&
              typeof msg.sentence_id === 'number' &&
              msg.sentence_id !== lastSentenceIdRef.current
            ) {
              lastSentenceIdRef.current = msg.sentence_id;
              setEmotion(msg.emotion as UseAmadeusWSReturn['emotion']);
            }
            if (msg.audio_degraded && paramsRef.current.onAudioDegraded) {
              if (!audioDegradedNotifiedRef.current) {
                audioDegradedNotifiedRef.current = true;
                paramsRef.current.onAudioDegraded(msg.degraded_reason || 'unknown');
              }
            }
            setMessages((prev) => {
              const lastMsg = prev[prev.length - 1];
              if (lastMsg && lastMsg.role === 'assistant' && lastMsg.id === activeAssistantMessageId) {
                const updatedContent = lastMsg.content.includes(msg.content)
                  ? lastMsg.content
                  : lastMsg.content + msg.content;
                const updatedEmotion = msg.emotion && ALLOWED_EMOTIONS.has(msg.emotion)
                  ? (msg.emotion as Message['emotion'])
                  : lastMsg.emotion;
                return [
                  ...prev.slice(0, -1),
                  { ...lastMsg, content: updatedContent, emotion: updatedEmotion }
                ];
              } else {
                activeAssistantMessageId = `assist_${Date.now()}`;
                const initialEmotion = msg.emotion && ALLOWED_EMOTIONS.has(msg.emotion)
                  ? (msg.emotion as Message['emotion'])
                  : 'neutral';
                return [
                  ...prev,
                  { id: activeAssistantMessageId, role: 'assistant', content: msg.content, emotion: initialEmotion }
                ];
              }
            });
            break;

          case 'audio_chunk':
            if (paramsRef.current.onAudioChunk && msg.data) {
              paramsRef.current.onAudioChunk(msg.sentence_id, msg.data);
            }
            break;

          case 'translation':
            // Translation arrives at the end of the streaming lifecycle, clear watchdog
            clearWatchdog();
            setIsThinking(false);
            setIsSynthesizing(false);
            setActiveTool(null);
            setActiveNotice(null);
            setMessages((prev) => {
              const lastMsg = prev[prev.length - 1];
              if (lastMsg && lastMsg.role === 'assistant') {
                return [
                  ...prev.slice(0, -1),
                  { ...lastMsg, translation: msg.content }
                ];
              }
              return prev;
            });
            break;

          case 'error':
            if (sessionStateRef.current === 'unknown') {
              sessionStateRef.current = 'rejected';
              setSessionState('rejected');
            }
            // Error clears watchdog
            clearWatchdog();
            setIsThinking(false);
            setIsSynthesizing(false);
            setStatus(msg.recoverable === true ? 'READY' : 'ERROR');
            setActiveTool(null);
            setActiveNotice(null);
            setMessages((prev) => prev.map((message) =>
              message.id === activeAssistantMessageId
                ? { ...message, streaming: false }
                : message
            ));
            setMessages((prev) => [
              ...prev,
              {
                id: `err_${Date.now()}`,
                role: 'assistant',
                content: `SYSTEM ERROR: ${msg.message}`,
                systemError: true,
              }
            ]);
            break;

          default:
            break;
        }
      } catch (err) {
        console.error('Failed to parse WebSocket message payload:', err);
      }
    };

    ws.onclose = () => {
      if (wsRef.current !== ws) return;
      setSessionReady(false);
      const turnWasActive = Boolean(activeTurnIdRef.current);
      stopHeartbeat();
      clearWatchdog();
      wsRef.current = null;
      activeTurnIdRef.current = '';
      activeGenerationRef.current = -1;
      if (pendingConversationSwitchRef.current) {
        paramsRef.current.onConversationSwitchError?.({
          conversationId: pendingConversationSwitchRef.current.conversationId,
          code: 'connection_closed',
        });
        pendingConversationSwitchRef.current = null;
      }
      if (pendingWorldlineSwitchRef.current) {
        const pending = pendingWorldlineSwitchRef.current;
        pendingWorldlineSwitchRef.current = null;
        setMessages(pending.transcriptSnapshot.map((message) => (
          message.streaming ? { ...message, streaming: false } : message
        )));
        paramsRef.current.onWorldlineSwitchError?.({
          worldline: pending.worldline,
          code: 'connection_closed',
        });
      } else if (turnWasActive) {
        surfaceAbortedTurn();
      } else {
        setMessages((prev) => prev.map((message) =>
          message.streaming ? { ...message, streaming: false } : message
        ));
      }
      setStatus('CLOSED');
      setActiveTool(null);
      setActiveNotice(null);
      setIsThinking(false);
      setIsSynthesizing(false);
      voiceStoppedRef.current = false;

      // Attempt automatic reconnection up to 5 times
      if (reconnectCountRef.current < 5) {
        const delay = Math.pow(2, reconnectCountRef.current) * 1000;
        reconnectCountRef.current += 1;
        console.log(`Connection closed. Reconnecting in ${delay / 1000}s... (Attempt ${reconnectCountRef.current}/5)`);
        reconnectTimerRef.current = window.setTimeout(connect, delay);
      }
    };

    ws.onerror = (err) => {
      if (wsRef.current !== ws) return;
      console.error('WebSocket Error:', err);
      setStatus('ERROR');
    };
  }, [disconnect]);

  const sendChat = useCallback((content: string) => {
    if (!content.trim() || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;

    // Start 30s watchdog wait immediately on sending chat
    startWatchdog();
    // Legacy v1 per-send flag only. The v2 slot is bound to the accepted
    // turn.started, so a busy-rejected resend cannot re-arm the old turn.
    audioDegradedNotifiedRef.current = false;
    
    // Add user message to state
    const userMsgId = `user_${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      { id: userMsgId, role: 'user', content: content.trim() }
    ]);
    
    setIsThinking(true);

    // Send chat frame
    wsRef.current.send(JSON.stringify({
      type: 'chat.send',
      content: content.trim()
    }));
  }, []);

  const stopVoice = useCallback(() => {
    voiceStoppedRef.current = true;
    setIsSynthesizing(false);
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: 'voice.stop' }));
  }, []);

  const cancelTurn = useCallback(() => {
    // Clear the watchdog before dropping identity: the server turn.cancelled
    // ACK can no longer match (and therefore clear) it afterwards.
    clearWatchdog();
    const cancelledTurnId = activeTurnIdRef.current;
    activeTurnIdRef.current = '';
    activeGenerationRef.current = -1;
    setIsThinking(false);
    setIsSynthesizing(false);
    voiceStoppedRef.current = true;
    // Keep partial content, but end streaming immediately so the bubble
    // never stays live when the ACK is ignored.
    if (cancelledTurnId) {
      setMessages((prev) => prev.map((message) =>
        message.turnId === cancelledTurnId ? { ...message, streaming: false } : message
      ));
    }
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: 'turn.cancel' }));
  }, []);

  const updateConfig = useCallback((patch: Partial<Omit<UseAmadeusWSParams, 'onAudioChunk'>>) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    
    // Map CamelCase to snake_case for Python backend API Contract
    const mappedPatch: Record<string, any> = {};
    if (patch.systemPrompt !== undefined) mappedPatch.system_prompt = patch.systemPrompt;
    if (patch.temperature !== undefined) mappedPatch.temperature = patch.temperature;
    if (patch.worldline !== undefined) mappedPatch.worldline = patch.worldline;
    if (patch.enableTts !== undefined) mappedPatch.enable_tts = patch.enableTts;
    if (patch.sovitsUrl !== undefined) mappedPatch.sovits_url = patch.sovitsUrl;
    if (patch.reasoningEffort !== undefined) mappedPatch.reasoning_effort = patch.reasoningEffort;
    if (patch.identityMode !== undefined) {
      mappedPatch.default_identity_mode = patch.identityMode;
    }
    if (patch.selfName !== undefined) mappedPatch.self_name = patch.selfName;
    if (patch.model !== undefined) mappedPatch.model = patch.model;

    if (patch.reasoningEffort !== undefined || patch.model !== undefined) {
      const epoch = ++configAppliedEpochRef.current;
      pendingConfigAppliedRef.current = {
        epoch,
        providerId: paramsRef.current.providerId,
        model: patch.model ?? paramsRef.current.model,
        conversationId: paramsRef.current.conversationId ?? null,
      };
    }

    // Send config_update frame
    const configUpdateFrame = {
      type: 'config_update',
      ...mappedPatch
    };
    wsRef.current.send(JSON.stringify(configUpdateFrame));
  }, []);

  const sendDebugTts = useCallback((emotion: string, text: string) => {
    if (!text.trim() || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: 'tts_debug', emotion, content: text.trim() }));
  }, []);

  const clearHistory = useCallback(() => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: 'clear_history' }));
    setMessages([]);
  }, []);

  /** Local-only: drop the visible transcript after the selected conversation was deleted.
   *  The caller reconnects without a conversation id so the server opens an empty draft. */
  const resetToDraft = useCallback(() => {
    activeConversationIdRef.current = null;
    activeTurnIdRef.current = '';
    messagesRef.current = [];
    setMessages([]);
    setEmotion('neutral');
  }, []);

  const switchConversation = useCallback((conversationId: string, history: Message[]) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      paramsRef.current.onConversationSwitchError?.({
        conversationId,
        code: 'socket_not_ready',
      });
      return false;
    }
    const requestId = createRequestId('conversation-switch');
    clearWatchdog();
    activeTurnIdRef.current = '';
    activeGenerationRef.current = -1;
    voiceStoppedRef.current = false;
    pendingWorldlineSwitchRef.current = null;
    pendingConversationSwitchRef.current = {
      requestId,
      conversationId,
      history,
    };
    setIsThinking(false);
    setIsSynthesizing(false);
    wsRef.current.send(JSON.stringify({
      type: 'conversation.switch',
      request_id: requestId,
      conversation_id: conversationId,
    }));
    return true;
  }, []);

  const switchWorldline = useCallback((worldline: 'steins_gate' | 'beta') => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      paramsRef.current.onWorldlineSwitchError?.({
        worldline,
        code: 'socket_not_ready',
      });
      return false;
    }
    // A rejected auth leaves the socket open with no server session, so a switch frame would never be ACKed.
    // The next auth frame carries the worldline, so the switch can commit locally.
    if (sessionStateRef.current === 'rejected') {
      pendingWorldlineSwitchRef.current = null;
      activeConversationIdRef.current = null;
      setMessages([]);
      messagesRef.current = [];
      setEmotion('neutral');
      paramsRef.current.onWorldlineSwitched?.({ worldline });
      return true;
    }
    const requestId = createRequestId('worldline-switch');
    clearWatchdog();
    activeTurnIdRef.current = '';
    activeGenerationRef.current = -1;
    activeConversationIdRef.current = null;
    voiceStoppedRef.current = true;
    pendingConversationSwitchRef.current = null;
    setIsThinking(false);
    setIsSynthesizing(false);
    setEmotion('neutral');
    // Snapshot then clear immediately so the target worldline UI never shows foreign transcript.
    // Capture synchronously (messagesRef) so pending is set before the switch frame is sent.
    // If a prior pending switch left messages empty, inherit its snapshot for the newest request.
    const cloneMessages = (rows: Message[]) => rows.map((message) => ({
      ...message,
      segments: message.segments ? message.segments.map((segment) => ({ ...segment })) : message.segments,
    }));
    const current = messagesRef.current;
    const inherited = pendingWorldlineSwitchRef.current?.transcriptSnapshot;
    const transcriptSnapshot = current.length > 0
      ? cloneMessages(current)
      : (inherited ? cloneMessages(inherited) : []);
    pendingWorldlineSwitchRef.current = { requestId, worldline, transcriptSnapshot };
    setMessages([]);
    messagesRef.current = [];
    wsRef.current.send(JSON.stringify({
      type: 'worldline.switch',
      request_id: requestId,
      target_worldline: worldline,
      conversation_mode: 'draft',
    }));
    return true;
  }, []);

  // Connect on mount, disconnect on unmount
  useEffect(() => {
    connect();
    return () => {
      disconnect();
    };
  }, [connect, disconnect]);

  return {
    status,
    sessionState,
    messages,
    isThinking,
    isSynthesizing,
    activeTool,
    activeNotice,
    emotion,
    clearHistory,
    sendChat,
    stopVoice,
    cancelTurn,
    sendDebugTts,
    updateConfig,
    reconnect: connect,
    resetToDraft,
    switchConversation,
    switchWorldline,
    wsRef
  };
}
