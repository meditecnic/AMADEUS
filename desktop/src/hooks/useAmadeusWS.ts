import { useState, useEffect, useRef, useCallback } from 'react';

export interface UseAmadeusWSParams {
  apiKey: string;
  systemPrompt: string;
  temperature: number;
  worldline: 'steins_gate' | 'beta';
  enableTts: boolean;
  sovitsUrl: string;
  model: 'deepseek-v4-flash' | 'deepseek-v4-pro';
  reasoningEffort: 'high' | 'xhigh';
  onAudioChunk?: (sentenceId: number, base64Data: string) => void;
  onAudioDegraded?: (reason: string) => void;
}

export type WSStatus = 'CONNECTING' | 'READY' | 'ERROR' | 'CLOSED';

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  translation?: string;
  emotion?: 'neutral' | 'tsundere' | 'embarrassed' | 'intellectual' | 'happy' | 'surprised' | 'annoyed' | 'disappointed' | 'sad';
}

export interface UseAmadeusWSReturn {
  status: WSStatus;
  messages: Message[];
  isThinking: boolean;
  isSynthesizing: boolean;
  activeTool: string | null;
  activeNotice: string | null;
  emotion: 'neutral' | 'tsundere' | 'embarrassed' | 'intellectual' | 'happy' | 'surprised' | 'annoyed' | 'disappointed' | 'sad';
  clearHistory: () => void;
  sendChat: (content: string) => void;
  sendDebugTts: (emotion: string, text: string) => void;
  updateConfig: (patch: Partial<Omit<UseAmadeusWSParams, 'onAudioChunk'>>) => void;
  wsRef: React.MutableRefObject<WebSocket | null>;
}

export function useAmadeusWS(params: UseAmadeusWSParams): UseAmadeusWSReturn {
  const [status, setStatus] = useState<WSStatus>('CLOSED');
  const [messages, setMessages] = useState<Message[]>([]);
  const [isThinking, setIsThinking] = useState(false);
  const [isSynthesizing, setIsSynthesizing] = useState(false);
  const [activeTool, setActiveTool] = useState<string | null>(null);
  const [activeNotice, setActiveNotice] = useState<string | null>(null);
  const [emotion, setEmotion] = useState<UseAmadeusWSReturn['emotion']>('neutral');

  const ALLOWED_EMOTIONS: ReadonlySet<string> = new Set([
    'neutral', 'tsundere', 'embarrassed', 'intellectual',
    'happy', 'surprised', 'annoyed', 'disappointed', 'sad',
  ]);
  const lastSentenceIdRef = useRef<number>(-1);

  const wsRef = useRef<WebSocket | null>(null);
  const paramsRef = useRef<UseAmadeusWSParams>(params);
  
  // Keep params ref updated
  useEffect(() => {
    paramsRef.current = params;
  }, [params]);

  const heartbeatTimerRef = useRef<number | null>(null);
  const watchdogTimerRef = useRef<number | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectCountRef = useRef(0);

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

  // Start 30s watchdog timer
  const startWatchdog = () => {
    clearWatchdog();
    watchdogTimerRef.current = window.setTimeout(() => {
      console.warn('Watchdog timed out (30s). Closing socket due to silence.');
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
  }, []);

  const connect = useCallback(() => {
    disconnect();
    setStatus('CONNECTING');

    // Generate or load a session ID
    let sessionId = '';
    try {
      sessionId = localStorage.getItem('amadeus_pc_session_id') || '';
    } catch (e) {
      console.warn('LocalStorage access blocked:', e);
    }
    if (!sessionId) {
      sessionId = typeof crypto !== 'undefined' && crypto.randomUUID 
        ? crypto.randomUUID() 
        : Math.random().toString(36).substring(2, 15);
      try {
        localStorage.setItem('amadeus_pc_session_id', sessionId);
      } catch (e) {
        console.warn('LocalStorage write failed:', e);
      }
    }

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // Tauri/Vite Config Proxy will route /ws to backend ws endpoint
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/chat?session_id=${sessionId}`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      if (wsRef.current !== ws) return;
      reconnectCountRef.current = 0;
      setStatus('READY');

      // Send auth frame immediately as first frame
      const authFrame = {
        type: 'auth',
        api_key: paramsRef.current.apiKey,
        sovits_url: paramsRef.current.sovitsUrl,
        system_prompt: paramsRef.current.systemPrompt,
        enable_tts: paramsRef.current.enableTts,
        temperature: paramsRef.current.temperature,
        worldline: paramsRef.current.worldline,
        model: paramsRef.current.model,
        reasoning_effort: paramsRef.current.reasoningEffort,
        client: 'desktop',
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
              setIsSynthesizing(true);
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
              paramsRef.current.onAudioDegraded(msg.degraded_reason || 'unknown');
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
            // Error clears watchdog
            clearWatchdog();
            setIsThinking(false);
            setIsSynthesizing(false);
            setStatus('ERROR');
            setActiveTool(null);
            setActiveNotice(null);
            setMessages((prev) => [
              ...prev,
              { id: `err_${Date.now()}`, role: 'assistant', content: `SYSTEM ERROR: ${msg.message}` }
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
      stopHeartbeat();
      clearWatchdog();
      wsRef.current = null;
      setStatus('CLOSED');
      setActiveTool(null);
      setActiveNotice(null);

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
    
    // Add user message to state
    const userMsgId = `user_${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      { id: userMsgId, role: 'user', content: content.trim() }
    ]);
    
    setIsThinking(true);

    // Send chat frame
    wsRef.current.send(JSON.stringify({
      type: 'chat',
      content: content.trim()
    }));
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
    if (patch.apiKey !== undefined) mappedPatch.api_key = patch.apiKey;
    if (patch.model !== undefined) mappedPatch.model = patch.model;
    if (patch.reasoningEffort !== undefined) mappedPatch.reasoning_effort = patch.reasoningEffort;

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

  // Connect on mount, disconnect on unmount
  useEffect(() => {
    connect();
    return () => {
      disconnect();
    };
  }, [connect, disconnect]);

  return {
    status,
    messages,
    isThinking,
    isSynthesizing,
    activeTool,
    activeNotice,
    emotion,
    clearHistory,
    sendChat,
    sendDebugTts,
    updateConfig,
    wsRef
  };
}
