import { useState, useRef, useEffect } from 'react';
import { AvatarViewer } from './components/AvatarViewer';
import { CRTOverlay } from './components/CRTOverlay';
import { LoadingLogo } from './components/LoadingLogo';
import { TypewriterText } from './components/TypewriterText';
import { useAudioPlayer } from './hooks/useAudioPlayer';
import { useAudio } from './hooks/useAudio';
import { Volume2, VolumeX, Cpu } from 'lucide-react';

import { SettingsModal, Settings } from './components/SettingsModal';
import { StatusBar } from './components/StatusBar';
import { TerminalHistory, LogEntry } from './components/TerminalHistory';
import { DEFAULT_SYSTEM_PROMPT } from './constants/defaultPrompt';

let memorySessionId: string | null = null;
let legacyDeepseekKey: string | null = null;

const sanitizeStoredSettings = (raw: string): Settings => {
  const parsed = JSON.parse(raw) as Settings;
  if (typeof parsed.deepseekKey === 'string' && parsed.deepseekKey.trim()) {
    legacyDeepseekKey = legacyDeepseekKey || parsed.deepseekKey.trim();
  }
  return { ...parsed, deepseekKey: '' };
};

// 静态离线 Mock 沙盒 WebSocket 模拟类
class MockWebSocket {
  public readyState: number = 1; // WebSocket.OPEN
  public onopen: (() => void) | null = null;
  public onmessage: ((event: { data: string }) => void) | null = null;
  public onclose: (() => void) | null = null;
  public onerror: ((err: any) => void) | null = null;

  private mockResponses = [
    {
      chunks: ["あんた、", "バカ？ ", "何言ってるのよ！"],
      emotions: ["tsundere", "tsundere", "embarrassed"],
      translation: "你是笨蛋吗？在胡说些什么啊！"
    },
    {
      chunks: ["私は牧瀬紅莉栖。", "別にあなたのために", "戻ってきたわけじゃないわ。"],
      emotions: ["neutral", "neutral", "embarrassed"],
      translation: "我是牧濑红莉栖。我可不是为了你才回来的。"
    },
    {
      chunks: ["タイムマシンの理论について", "ディスカッションしたいの？", "仕方ないわね。"],
      emotions: ["intellectual", "intellectual", "neutral"],
      translation: "想讨论关于时间机器的理论吗？真拿你没办法呢。"
    }
  ];
  private responseIndex = 0;

  constructor() {
    setTimeout(() => {
      if (this.onopen) this.onopen();
    }, 150);
  }

  send(data: string) {
    try {
      const payload = JSON.parse(data);
      if (payload.type === 'auth') {
        return;
      }
      if (payload.type === 'ping') {
        setTimeout(() => {
          if (this.onmessage) this.onmessage({ data: JSON.stringify({ type: 'pong' }) });
        }, 50);
        return;
      }

      if (payload.type === 'chat' && payload.content) {
        this.triggerResponseSequence();
      }
    } catch (e) {
      console.error("MockWS error parsing send data", e);
    }
  }

  close() {
    this.readyState = 3; // WebSocket.CLOSED
    setTimeout(() => {
      if (this.onclose) this.onclose();
    }, 50);
  }

  private triggerResponseSequence() {
    const res = this.mockResponses[this.responseIndex];
    this.responseIndex = (this.responseIndex + 1) % this.mockResponses.length;

    // 1. Thinking 状态 (300ms)
    setTimeout(() => {
      this.sendToClient({ type: "status", state: "thinking" });
    }, 300);

    // 2. Speaking 状态 (1300ms)
    setTimeout(() => {
      this.sendToClient({ type: "status", state: "speaking" });
    }, 1300);

    // 3. 陆续发送 text_chunk
    let chunkDelay = 1500;
    res.chunks.forEach((chunk, index) => {
      setTimeout(() => {
        this.sendToClient({
          type: "text_chunk",
          content: chunk,
          emotion: res.emotions[index]
        });
      }, chunkDelay);
      chunkDelay += 600;
    });

    // 4. 发送翻译结果
    setTimeout(() => {
      this.sendToClient({
        type: "translation",
        content: res.translation
      });
    }, chunkDelay);
    chunkDelay += 500;

    // 5. Done 状态
    setTimeout(() => {
      this.sendToClient({ type: "status", state: "done" });
    }, chunkDelay);
  }

  private sendToClient(msg: any) {
    if (this.readyState === 1 && this.onmessage) {
      this.onmessage({ data: JSON.stringify(msg) });
    }
  }
}

// 缓存与配置获取逻辑
const loadSettings = (): Settings => {
  let sessionSettings: Settings | null = null;
  let localSettings: Settings | null = null;
  try {
    const sessionVal = sessionStorage.getItem('amadeus_settings');
    if (sessionVal) {
      sessionSettings = sanitizeStoredSettings(sessionVal);
      sessionStorage.setItem('amadeus_settings', JSON.stringify(sessionSettings));
    }
  } catch (e) {
    console.warn('Failed to load settings from sessionStorage:', e);
  }
  
  try {
    const localVal = localStorage.getItem('amadeus_settings');
    if (localVal) {
      localSettings = sanitizeStoredSettings(localVal);
      localStorage.setItem('amadeus_settings', JSON.stringify(localSettings));
    }
  } catch (e) {
    console.warn('Failed to load settings from localStorage:', e);
  }

  if (sessionSettings || localSettings) {
    return sessionSettings || localSettings!;
  }
  
  return {
    deepseekKey: '',
    sovitsUrl: '',
    systemPrompt: DEFAULT_SYSTEM_PROMPT,
    rememberMe: true,
    enableTTS: true,
    enableBGM: true,
    bgmVolume: 0.15,
    sfxVolume: 1.0,
    temperature: 1.0
  };
};

export default function App() {
  const [status, setStatus] = useState<'DISCONNECTED' | 'CONNECTING' | 'CONNECTED'>('DISCONNECTED');
  const [emotion, setEmotion] = useState<'neutral' | 'tsundere' | 'embarrassed' | 'intellectual'>('neutral');
  const [glitchState, setGlitchState] = useState<'none' | 'light' | 'heavy'>('none');
  const [chatText, setChatText] = useState('SYSTEM OFF');
  const [translationText, setTranslationText] = useState('');
  const [mute, setMute] = useState(false);
  
  // 新增实时聊天相关状态
  const [inputText, setInputText] = useState('');
  const [isThinking, setIsThinking] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isInteractive, setIsInteractive] = useState(false); // 区别系统问候和交互文本显示模式
  
  // 扩展组件配置与状态
  const [settings, setSettings] = useState<Settings>(loadSettings);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [notice, setNotice] = useState<string>('');
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isMockMode, setIsMockMode] = useState(false);
  
  // Mock 模式的说话嘴动动画状态
  const [mockIsSpeaking, setMockIsSpeaking] = useState(false);
  const [mockVolume, setMockVolume] = useState(0);
  const mockVoiceIntervalRef = useRef<number | null>(null);

  const { currentVolume, isPlaying, playAudio, stopAudio, initContext, unlockAudioContext, setMuted } = useAudioPlayer();
  const { playBGM, stopBGM, playSFX, setBGMVolume, setSFXVolume, setMute: setAudioMute } = useAudio();
  
  // WebSocket 相关 Refs
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectCountRef = useRef(0);
  const reconnectTimerRef = useRef<number | null>(null);
  const heartbeatTimerRef = useRef<number | null>(null);
  const watchdogTimerRef = useRef<number | null>(null);
  
  // 缓存流式数据
  const accumulatedTextRef = useRef('');
  const pendingTranslationRef = useRef('');
  const hasPendingTranslationRef = useRef(false);

  // Refs mirroring to avoid stale closures in event handlers
  const muteRef = useRef(mute);
  useEffect(() => {
    muteRef.current = mute;
    setMuted(mute);
    setAudioMute(mute);
  }, [mute]);

  const settingsRef = useRef(settings);
  useEffect(() => {
    settingsRef.current = settings;
  }, [settings]);

  const emotionRef = useRef(emotion);
  useEffect(() => {
    emotionRef.current = emotion;
  }, [emotion]);

  const isMockModeRef = useRef(isMockMode);
  useEffect(() => {
    isMockModeRef.current = isMockMode;
  }, [isMockMode]);

  const statusRef = useRef(status);
  useEffect(() => {
    statusRef.current = status;
  }, [status]);

  const isPlayingRef = useRef(isPlaying);
  useEffect(() => {
    isPlayingRef.current = isPlaying;
  }, [isPlaying]);

  const connectionTimeoutRef = useRef<number | null>(null);

  // One-time migration for builds that previously persisted the provider key.
  useEffect(() => {
    const key = legacyDeepseekKey;
    legacyDeepseekKey = null;
    if (!key) return;
    fetch('/api/providers/deepseek/credentials', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: key })
    }).catch(err => console.error('Failed to migrate provider credential:', err));
  }, []);

  // Load settings from REST API on mount
  useEffect(() => {
    fetch('/api/settings')
      .then(res => {
        if (!res.ok) throw new Error('Settings GET failed');
        return res.json();
      })
      .then(data => {
        setSettings(prev => ({
          ...prev,
          sovitsUrl: data.sovits_url !== undefined ? data.sovits_url : prev.sovitsUrl,
          systemPrompt: data.system_prompt !== undefined ? data.system_prompt : prev.systemPrompt,
          rememberMe: data.remember_me !== undefined ? data.remember_me : prev.rememberMe,
          enableTTS: data.enable_tts !== undefined ? data.enable_tts : prev.enableTTS,
          enableBGM: data.enable_bgm !== undefined ? data.enable_bgm : prev.enableBGM,
          bgmVolume: data.bgm_volume !== undefined ? data.bgm_volume : prev.bgmVolume,
          sfxVolume: data.sfx_volume !== undefined ? data.sfx_volume : prev.sfxVolume,
          temperature: data.temperature !== undefined ? data.temperature : prev.temperature,
        }));
      })
      .catch(err => console.error('Failed to fetch settings from REST API:', err));
  }, []);

  // Clear developer console logs immediately when notice becomes active
  useEffect(() => {
    if (notice) {
      setLogs([]);
    }
  }, [notice]);

  // Sync BGM playback
  useEffect(() => {
    if (settings.enableBGM && !mute) {
      if (status === 'CONNECTED') {
        playBGM('/audio/bgm/Amadeus.ogg', settings.bgmVolume);
      } else {
        playBGM('/audio/bgm/Messenger main theme.ogg', settings.bgmVolume);
      }
    } else {
      stopBGM();
    }
  }, [status, settings.enableBGM, mute]);

  useEffect(() => {
    setBGMVolume(settings.bgmVolume);
  }, [settings.bgmVolume]);

  useEffect(() => {
    setSFXVolume(settings.sfxVolume);
  }, [settings.sfxVolume]);

  // 添加 WS 日志项方法
  const addLog = (direction: 'TX' | 'RX', data: string) => {
    setLogs(prev => {
      const next = [
        ...prev,
        {
          id: Math.random().toString(36).substring(2, 9),
          timestamp: Date.now(),
          direction,
          data
        }
      ];
      return next.slice(-200); // 限制最多200条以防内存溢出
    });
  };

  // 触发 Glitch 特效方法
  const triggerGlitch = (state: 'none' | 'light' | 'heavy') => {
    setGlitchState(state);
    if (state !== 'none') {
      setTimeout(() => setGlitchState('none'), state === 'heavy' ? 250 : 120);
    }
  };

  // 监听播放状态以适时展示翻译
  useEffect(() => {
    if (!isPlaying && hasPendingTranslationRef.current) {
      setTranslationText(pendingTranslationRef.current);
      hasPendingTranslationRef.current = false;
    }
  }, [isPlaying]);

  // 组件卸载清理
  useEffect(() => {
    return () => {
      disconnectWS();
      if (reconnectTimerRef.current) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (mockVoiceIntervalRef.current) {
        clearInterval(mockVoiceIntervalRef.current);
        mockVoiceIntervalRef.current = null;
      }
      if (watchdogTimerRef.current) {
        window.clearTimeout(watchdogTimerRef.current);
        watchdogTimerRef.current = null;
      }
      if (connectionTimeoutRef.current) {
        window.clearTimeout(connectionTimeoutRef.current);
        connectionTimeoutRef.current = null;
      }
    };
  }, []);

  // 断开 WebSocket 连接
  const disconnectWS = () => {
    stopHeartbeat();
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }
    if (mockVoiceIntervalRef.current) {
      clearInterval(mockVoiceIntervalRef.current);
      mockVoiceIntervalRef.current = null;
    }
    if (connectionTimeoutRef.current) {
      window.clearTimeout(connectionTimeoutRef.current);
      connectionTimeoutRef.current = null;
    }
    if (reconnectTimerRef.current) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (watchdogTimerRef.current) {
      window.clearTimeout(watchdogTimerRef.current);
      watchdogTimerRef.current = null;
    }
  };

  // 开启心跳
  const startHeartbeat = () => {
    stopHeartbeat();
    heartbeatTimerRef.current = window.setInterval(() => {
      if (wsRef.current && wsRef.current.readyState === 1) {
        wsRef.current.send(JSON.stringify({ type: 'ping' }));
      }
    }, 30000);
  };

  // 关闭心跳
  const stopHeartbeat = () => {
    if (heartbeatTimerRef.current) {
      clearInterval(heartbeatTimerRef.current);
      heartbeatTimerRef.current = null;
    }
  };

  // 切换至 MockWebSocket 离线沙盒
  const switchToMock = () => {
    disconnectWS();
    setIsMockMode(true);
    setStatus('CONNECTING');
    setChatText('ESTABLISHING MOCK CONNECTION...');

    const ws = new MockWebSocket() as unknown as WebSocket;
    wsRef.current = ws;

    // 代理 Mock WS 发送数据包
    const originalSend = ws.send.bind(ws);
    ws.send = (data: string | ArrayBufferLike | Blob | ArrayBufferView) => {
      addLog('TX', typeof data === 'string' ? data : '[Binary Data]');
      originalSend(data);
    };

    const connectStartTime = Date.now();
    ws.onopen = () => {
      if (wsRef.current !== ws) return;
      // Send auth frame immediately as first frame
      const authFrame = {
        type: "auth",
        sovits_url: settingsRef.current.sovitsUrl,
        system_prompt: settingsRef.current.systemPrompt,
        enable_tts: settingsRef.current.enableTTS,
        temperature: settingsRef.current.temperature
      };
      ws.send(JSON.stringify(authFrame));

      const elapsed = Date.now() - connectStartTime;
      const remainingDelay = Math.max(0, 1550 - elapsed);
      
      setTimeout(() => {
        if (wsRef.current !== ws) return;
        setStatus('CONNECTED');
        setIsThinking(false);
        setIsSpeaking(false);
        setIsInteractive(false);
        setChatText("Amadeus System 已激活 (MOCK)。你好，我是牧濑红莉栖。");
        triggerGlitch('light');
        reconnectCountRef.current = 0;
        accumulatedTextRef.current = '';
        pendingTranslationRef.current = '';
        hasPendingTranslationRef.current = false;
        startHeartbeat();
      }, remainingDelay);
    };

    ws.onmessage = (event) => {
      if (wsRef.current !== ws) return;
      addLog('RX', typeof event.data === 'string' ? event.data : '[Binary Data]');
      try {
        const msg = JSON.parse(event.data);
        handleServerMessageRef.current(msg);
      } catch (err) {
        console.error('Failed to parse MOCK WS payload:', err);
      }
    };

    ws.onclose = () => {
      if (wsRef.current !== ws) return;
      stopHeartbeat();
      wsRef.current = null;
      setStatus('DISCONNECTED');
      setChatText('SYSTEM DISCONNECTED.');
    };

    ws.onerror = (err) => {
      if (wsRef.current !== ws) return;
      console.error('Mock WS Error:', err);
    };
  };

  // 建立 WebSocket 连接
  const connectWS = () => {
    disconnectWS();
    initContext();
    stopAudio();
    setStatus('CONNECTING');
    setChatText('ESTABLISHING CONNECTION...');

    // 优先检查是否带有 `?mock=true` 或者强制 Mock
    const isForceMock = window.location.search.includes('mock=true');
    if (isForceMock) {
      switchToMock();
      return;
    }

    setIsMockMode(false);

    let sessionId: string | null = null;
    try {
      sessionId = localStorage.getItem('amadeus_session_id');
    } catch (e) {
      console.warn('Failed to get session ID from localStorage:', e);
      sessionId = memorySessionId;
    }

    if (!sessionId) {
      sessionId = (typeof crypto !== 'undefined' && crypto.randomUUID) 
        ? crypto.randomUUID() 
        : Math.random().toString(36).substring(2, 15) + Math.random().toString(36).substring(2, 15);
      try {
        localStorage.setItem('amadeus_session_id', sessionId);
      } catch (e) {
        console.warn('Failed to set session ID in localStorage:', e);
        memorySessionId = sessionId;
      }
    }

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    let wsUrl = `${wsProtocol}//${window.location.host}/ws/chat?session_id=${sessionId}`;
    
    // 如果 settingsRef.current 中有值，作为 Query Params 附加在 wsUrl 后面
    if (settingsRef.current.sovitsUrl) {
      wsUrl += `&sovits_url=${encodeURIComponent(settingsRef.current.sovitsUrl)}`;
    }

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    const connectStartTime = Date.now();

    // 代理原生 WS 发送数据包
    const originalSend = ws.send.bind(ws);
    ws.send = (data: string | ArrayBufferLike | Blob | ArrayBufferView) => {
      addLog('TX', typeof data === 'string' ? data : '[Binary Data]');
      originalSend(data);
    };

    // 3秒连接超时定时器，超时则回退到 Mock 离线沙盒
    connectionTimeoutRef.current = window.setTimeout(() => {
      if (ws.readyState !== WebSocket.OPEN) {
        console.warn('WebSocket connection timeout. Falling back to Mock Sandbox...');
        setNotice('WS Connect Timeout. Switched to Mock Sandbox.');
        ws.close();
        switchToMock();
      }
    }, 3000);

    ws.onopen = () => {
      if (wsRef.current !== ws) return;
      if (connectionTimeoutRef.current) {
        window.clearTimeout(connectionTimeoutRef.current);
        connectionTimeoutRef.current = null;
      }

      // Send auth frame immediately as first frame
      const authFrame = {
        type: "auth",
        sovits_url: settingsRef.current.sovitsUrl,
        system_prompt: settingsRef.current.systemPrompt,
        enable_tts: settingsRef.current.enableTTS,
        temperature: settingsRef.current.temperature
      };
      ws.send(JSON.stringify(authFrame));

      const elapsed = Date.now() - connectStartTime;
      const remainingDelay = Math.max(0, 1550 - elapsed);
      
      setTimeout(() => {
        if (wsRef.current !== ws) return; // 确保在等待期间没有被用户手动断开
        setStatus('CONNECTED');
        setIsThinking(false);
        setIsSpeaking(false);
        setIsInteractive(false);
        setChatText("Amadeus System 已激活。你好，我是牧濑红莉栖。");
        triggerGlitch('light');
        reconnectCountRef.current = 0;
        accumulatedTextRef.current = '';
        pendingTranslationRef.current = '';
        hasPendingTranslationRef.current = false;
        startHeartbeat();
      }, remainingDelay);
    };

    ws.onmessage = (event) => {
      if (wsRef.current !== ws) return;
      addLog('RX', typeof event.data === 'string' ? event.data : '[Binary Data]');
      try {
        const msg = JSON.parse(event.data);
        handleServerMessageRef.current(msg);
      } catch (err) {
        console.error('Failed to parse WS payload:', err);
      }
    };

    ws.onclose = () => {
      if (wsRef.current !== ws) return;
      if (connectionTimeoutRef.current) {
        window.clearTimeout(connectionTimeoutRef.current);
        connectionTimeoutRef.current = null;
      }
      stopHeartbeat();
      wsRef.current = null;
      
      if (statusRef.current !== 'DISCONNECTED' && reconnectCountRef.current < 5) {
        const delay = Math.pow(2, reconnectCountRef.current) * 1000;
        reconnectCountRef.current += 1;
        setChatText(`CONNECTION LOST. RECONNECTING IN ${delay / 1000}s... (ATTEMPT ${reconnectCountRef.current}/5)`);
        setStatus('CONNECTING');
        reconnectTimerRef.current = window.setTimeout(connectWS, delay);
      } else {
        setNotice('Real WS Connection Failed. Falling back to Mock.');
        switchToMock();
      }
    };

    ws.onerror = (err) => {
      if (wsRef.current !== ws) return;
      console.error('WS Error:', err);
      if (connectionTimeoutRef.current) {
        window.clearTimeout(connectionTimeoutRef.current);
        connectionTimeoutRef.current = null;
      }
    };
  };

  // 解析服务端响应消息
  const handleServerMessage = (msg: any) => {
    // Clear watchdog timer if we receive translation, status done, or error
    if (msg.type === 'translation' || 
        (msg.type === 'status' && msg.state === 'done') || 
        msg.type === 'error') {
      if (watchdogTimerRef.current) {
        clearTimeout(watchdogTimerRef.current);
        watchdogTimerRef.current = null;
      }
    }

    switch (msg.type) {
      case 'status':
        if (msg.state === 'thinking') {
          setIsThinking(true);
          setIsSpeaking(false);
        } else if (msg.state === 'speaking') {
          setIsThinking(false);
          setIsSpeaking(true);
          if (isMockModeRef.current) {
            setMockIsSpeaking(true);
            if (mockVoiceIntervalRef.current) clearInterval(mockVoiceIntervalRef.current);
            mockVoiceIntervalRef.current = window.setInterval(() => {
              setMockVolume(0.06 + Math.random() * 0.4);
            }, 80);
          }
        } else if (msg.state === 'done') {
          setIsThinking(false);
          setIsSpeaking(false);
          if (isMockModeRef.current) {
            setMockIsSpeaking(false);
            setMockVolume(0);
            if (mockVoiceIntervalRef.current) {
              clearInterval(mockVoiceIntervalRef.current);
              mockVoiceIntervalRef.current = null;
            }
          }
        }
        break;

      case 'text_chunk':
        if (msg.emotion) {
          if (msg.emotion !== emotionRef.current) {
            triggerGlitch(msg.emotion === 'tsundere' ? 'heavy' : 'light');
          }
          setEmotion(msg.emotion);
        }
        accumulatedTextRef.current += msg.content || '';
        setChatText(accumulatedTextRef.current);
        break;

      case 'audio_chunk':
        if (!muteRef.current && msg.data) {
          playAudio(msg.data);
        }
        break;

      case 'translation':
        pendingTranslationRef.current = msg.content || '';
        hasPendingTranslationRef.current = true;
        // 如果静音，或者当前根本没在播放，说明没有音频触发 onended，或者音频已经放完/没开始，我们可以直接展示翻译
        if (muteRef.current || !isPlaying) {
          setTranslationText(msg.content || '');
          hasPendingTranslationRef.current = false;
        }
        break;

      case 'error':
        setIsThinking(false);
        setIsSpeaking(false);
        stopAudio();
        setChatText(msg.message || 'An error occurred.');
        setNotice(msg.message || 'An error occurred.');
        if (isMockModeRef.current) {
          setMockIsSpeaking(false);
          setMockVolume(0);
          if (mockVoiceIntervalRef.current) {
            clearInterval(mockVoiceIntervalRef.current);
            mockVoiceIntervalRef.current = null;
          }
        }
        break;

      default:
        break;
    }
  };

  const handleServerMessageRef = useRef(handleServerMessage);
  useEffect(() => {
    handleServerMessageRef.current = handleServerMessage;
  }, [handleServerMessage]);

  // Listen to window mock events for Playwright tests
  useEffect(() => {
    const handleMockWS = (e: Event) => {
      const customEvent = e as CustomEvent;
      const msg = customEvent.detail;
      addLog('RX', JSON.stringify(msg));
      handleServerMessageRef.current(msg);
    };
    window.addEventListener('amadeus-mock-ws', handleMockWS);
    return () => window.removeEventListener('amadeus-mock-ws', handleMockWS);
  }, []);

  // 处理发送逻辑
  const handleSend = (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputText.trim() || !wsRef.current || wsRef.current.readyState !== 1) return;

    playSFX('/audio/sfx/连接成功，进入页面1.ogg');
    stopAudio();
    setIsThinking(true);
    setIsSpeaking(false);
    setIsInteractive(true);
    setTranslationText('');
    setChatText('');
    accumulatedTextRef.current = '';
    pendingTranslationRef.current = '';
    hasPendingTranslationRef.current = false;

    const payload = {
      type: "chat",
      content: inputText.trim()
    };

    wsRef.current.send(JSON.stringify(payload));
    setInputText('');

    // Setup watchdog timer
    if (watchdogTimerRef.current) clearTimeout(watchdogTimerRef.current);
    watchdogTimerRef.current = window.setTimeout(() => {
      console.warn('Watchdog timeout triggered (30s)');
      setNotice('Response timeout.');
      playSFX('/assets/audio/gah.ogg');
      setIsThinking(false);
      setIsSpeaking(false);
    }, 30000);
  };

  const handleConnect = () => {
    playSFX('/audio/sfx/连接成功，进入页面2.ogg');
    unlockAudioContext();
    connectWS();
  };

  // 保存设置的逻辑
  const handleSaveSettings = (newSettings: Settings, isKeyChanged: boolean) => {
    const safeSettings = { ...newSettings, deepseekKey: '' };
    setSettings(safeSettings);
    
    // 存储分级
    if (newSettings.rememberMe) {
      localStorage.setItem('amadeus_settings', JSON.stringify(safeSettings));
      sessionStorage.removeItem('amadeus_settings');
    } else {
      sessionStorage.setItem('amadeus_settings', JSON.stringify(safeSettings));
      localStorage.removeItem('amadeus_settings');
    }

    const payload: any = {
      sovits_url: newSettings.sovitsUrl,
      system_prompt: newSettings.systemPrompt,
      remember_me: newSettings.rememberMe,
      enable_tts: newSettings.enableTTS,
      enable_bgm: newSettings.enableBGM,
      bgm_volume: newSettings.bgmVolume,
      sfx_volume: newSettings.sfxVolume,
      temperature: newSettings.temperature
    };

    const settingsRequest = fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const credentialRequest = isKeyChanged && newSettings.deepseekKey.trim()
      ? fetch('/api/providers/deepseek/credentials', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ api_key: newSettings.deepseekKey.trim() })
        })
      : Promise.resolve(new Response(null, { status: 204 }));

    Promise.all([settingsRequest, credentialRequest])
      .then(([settingsResponse, credentialResponse]) => {
        if (!settingsResponse.ok || !credentialResponse.ok) {
          throw new Error('Settings save failed');
        }
      })
      .then(() => {
        setNotice('Settings saved.');
        if (isKeyChanged && status === 'CONNECTED') {
          setNotice('API Key updated, reconnecting...');
          connectWS();
        } else if (!isKeyChanged && wsRef.current && wsRef.current.readyState === 1) {
          // Send flat config_update to WS
          const configUpdateFrame = {
            type: 'config_update',
            sovits_url: newSettings.sovitsUrl,
            system_prompt: newSettings.systemPrompt,
            enable_tts: newSettings.enableTTS,
            temperature: newSettings.temperature
          };
          wsRef.current.send(JSON.stringify(configUpdateFrame));
        }
      })
      .catch(err => {
        console.error('Failed to save settings:', err);
        setNotice('Failed to save settings.');
      });
  };

  const wsState = isThinking ? 'thinking' : (isSpeaking || isPlaying || (isMockMode ? mockIsSpeaking : false)) ? 'speaking' : 'idle';

  return (
    <div 
      className="relative w-screen h-screen flex justify-center bg-black overflow-hidden crt-screen crt-flicker"
      style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        backgroundColor: '#050302'
      }}
    >
      {/* 全屏扫描线与色差滤镜 */}
      <CRTOverlay glitchState={glitchState} />

      {/* 手机视口模拟容器 (CSS断点) */}
      <div 
        className="w-full h-full flex flex-col justify-between relative grid-background"
        style={{
          maxWidth: '430px',
          backgroundColor: '#0c0604',
          borderLeft: '1px solid #3c1e10',
          borderRight: '1px solid #3c1e10',
          boxShadow: '0 0 50px rgba(255, 108, 0, 0.12)',
          zIndex: 10
        }}
      >
        {/* 顶部 Header */}
        <header 
          style={{
            padding: '12px 16px',
            borderBottom: '1px solid #3c1e10',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            fontSize: '11px',
            letterSpacing: '0.15em',
            color: 'var(--color-amadeus-orange)',
            backgroundColor: '#140804',
            fontWeight: 'bold'
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <Cpu style={{ width: '14px', height: '14px', strokeWidth: 2.5 }} />
            <span>AMADEUS v1.0.4</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <button
              data-testid="settings-trigger"
              onClick={() => {
                playSFX('/audio/sfx/提示音1.ogg');
                setIsSettingsOpen(true);
              }}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--color-amadeus-orange)',
                cursor: 'pointer',
                fontSize: '10px',
                fontWeight: 'bold',
                padding: '2px 6px',
                borderRight: '1px solid rgba(255,108,0,0.2)'
              }}
              className="hover:text-white transition-colors"
            >
              [设置]
            </button>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span 
                style={{
                  width: '7px',
                  height: '7px',
                  borderRadius: '50%',
                  backgroundColor: status === 'CONNECTED' ? '#ff6c00' : (status === 'CONNECTING' ? '#e2a300' : '#4a2515'),
                  boxShadow: status === 'CONNECTED' ? '0 0 6px #ff6c00' : 'none'
                }}
              />
              <span>{status}</span>
            </div>
          </div>
        </header>

        {/* 实时性能监控与通告 StatusBar */}
        <StatusBar
          notice={notice}
          onClearNotice={() => setNotice('')}
          wsState={wsState}
        />

        {/* 核心视口 */}
        <main 
          style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'center',
            alignItems: 'center',
            padding: '16px',
            position: 'relative',
            overflowY: 'auto'
          }}
        >
          {status === 'DISCONNECTED' && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '30px' }}>
              <div 
                style={{
                  width: '128px',
                  height: '128px',
                  border: '1px solid rgba(255, 108, 0, 0.25)',
                  borderRadius: '50%',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  padding: '8px',
                  backgroundColor: 'rgba(255, 108, 0, 0.04)',
                  boxShadow: '0 0 15px rgba(255, 108, 0, 0.05)'
                }}
              >
                <img 
                  src="/assets/images/ic_launcher.png" 
                  alt="App Launcher" 
                  style={{ width: '96px', height: '96px', objectFit: 'contain' }} 
                />
              </div>
              <button
                onClick={handleConnect}
                className="neon-border neon-text"
                style={{
                  padding: '10px 24px',
                  backgroundColor: 'rgba(255, 108, 0, 0.08)',
                  color: 'var(--color-amadeus-orange)',
                  fontSize: '13px',
                  letterSpacing: '0.2em',
                  cursor: 'pointer',
                  outline: 'none',
                  textTransform: 'uppercase',
                  fontFamily: 'inherit',
                  fontWeight: 'bold',
                  transition: 'all 0.2s'
                }}
              >
                Connect Amadeus
              </button>
            </div>
          )}

          {status === 'CONNECTING' && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '20px' }}>
              <LoadingLogo className="w-48 h-48" style={{ width: '192px', height: '192px' }} />
              <p 
                style={{
                  fontSize: '11px',
                  color: 'rgba(255, 108, 0, 0.7)',
                  letterSpacing: '0.18em',
                  animation: 'crt-flicker-anim 0.4s infinite',
                  fontWeight: 'bold'
                }}
              >
                ESTABLISHING SECURE CONNECTION...
              </p>
            </div>
          )}

          {status === 'CONNECTED' && (
            <div 
              style={{
                width: '100%',
                height: '100%',
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'space-between'
              }}
            >
              {/* Live2D 人物渲染区 */}
              <AvatarViewer
                emotion={emotion}
                isSpeaking={isMockMode ? mockIsSpeaking : isPlaying}
                volume={isMockMode ? mockVolume : currentVolume}
                glitchState={glitchState}
              />

              {/* 文本字幕对话气泡 */}
              <div 
                className="neon-border"
                style={{
                  width: '100%',
                  minHeight: '100px',
                  backgroundColor: 'rgba(21, 10, 5, 0.85)',
                  padding: '14px',
                  borderRadius: '4px',
                  color: 'var(--color-amadeus-orange)',
                  fontSize: '13px',
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'space-between',
                  letterSpacing: '0.04em'
                }}
              >
                {translationText && (
                  <div style={{ lineHeight: 1.6, fontWeight: 500, fontSize: '15px', color: 'var(--color-amadeus-orange)' }}>
                     <TypewriterText text={translationText} speed={25} />
                  </div>
                )}

                <div 
                  style={{
                    lineHeight: 1.4,
                    fontSize: translationText ? '11px' : '13px',
                    color: translationText ? 'rgba(255, 108, 0, 0.55)' : 'var(--color-amadeus-orange)',
                    borderTop: translationText ? '1px solid rgba(255, 108, 0, 0.12)' : 'none',
                    paddingTop: translationText ? '8px' : '0',
                    marginTop: translationText ? '8px' : '0',
                    transition: 'all 0.3s ease'
                  }}
                >
                  {isInteractive ? (
                    <span>{chatText}</span>
                  ) : (
                    <TypewriterText text={chatText} speed={30} />
                  )}
                </div>
              </div>
            </div>
          )}
        </main>

        {/* 底部实时 WebSocket 交互控制面板 (仅在连接后显示) */}
        {status === 'CONNECTED' && (
          <footer 
            style={{
              borderTop: '1px solid #3c1e10',
              backgroundColor: '#120704',
              padding: '12px',
              display: 'flex',
              flexDirection: 'column',
              gap: '8px'
            }}
          >
            <div 
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                fontSize: '10px',
                color: 'rgba(255, 108, 0, 0.5)',
                letterSpacing: '0.08em',
                borderBottom: '1px solid rgba(60, 30, 16, 0.4)',
                paddingBottom: '6px'
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                <Cpu 
                  className={isThinking ? 'animate-spin' : ''} 
                  style={{ width: '12px', height: '12px' }} 
                />
                <span>
                  {isThinking 
                    ? '思考中...' 
                    : (isMockMode ? mockIsSpeaking : isPlaying)
                      ? '输出中...' 
                      : '待机'}
                </span>
              </div>
              <button
                onClick={() => setMute(!mute)}
                style={{
                  background: 'none',
                  border: 'none',
                  color: 'inherit',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                  fontSize: '9px',
                  fontWeight: 'bold'
                }}
              >
                {mute ? <VolumeX style={{ width: '12px', height: '12px' }} /> : <Volume2 style={{ width: '12px', height: '12px' }} />}
                <span>{mute ? "静音" : "声音开"}</span>
              </button>
            </div>

            {/* 实时聊天输入框与发送按钮 */}
            <form 
              onSubmit={handleSend}
              style={{
                display: 'flex',
                gap: '8px',
                width: '100%'
              }}
            >
              <input
                type="text"
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                disabled={isThinking || (isMockMode ? mockIsSpeaking : isPlaying)}
                placeholder={isThinking ? "思考中..." : (isMockMode ? mockIsSpeaking : isPlaying) ? "输出中..." : "在这里输入对话内容..."}
                style={{
                  flex: 1,
                  backgroundColor: '#1b0d07',
                  border: '1px solid rgba(255, 108, 0, 0.25)',
                  borderRadius: '4px',
                  padding: '8px 12px',
                  fontSize: '13px',
                  color: 'var(--color-amadeus-orange)',
                  outline: 'none',
                  fontFamily: 'inherit',
                  transition: 'all 0.2s',
                  cursor: (isThinking || (isMockMode ? mockIsSpeaking : isPlaying)) ? 'not-allowed' : 'text',
                  opacity: (isThinking || (isMockMode ? mockIsSpeaking : isPlaying)) ? 0.6 : 1,
                }}
                className="input-glow"
              />
              <button
                type="submit"
                disabled={isThinking || (isMockMode ? mockIsSpeaking : isPlaying) || !inputText.trim()}
                className="neon-border"
                style={{
                  padding: '8px 16px',
                  backgroundColor: 'rgba(255, 108, 0, 0.08)',
                  color: 'var(--color-amadeus-orange)',
                  fontSize: '12px',
                  borderRadius: '4px',
                  cursor: (isThinking || (isMockMode ? mockIsSpeaking : isPlaying) || !inputText.trim()) ? 'not-allowed' : 'pointer',
                  opacity: (isThinking || (isMockMode ? mockIsSpeaking : isPlaying) || !inputText.trim()) ? 0.4 : 1,
                  transition: 'all 0.15s',
                  fontWeight: 'bold',
                  fontFamily: 'inherit'
                }}
              >
                发送
              </button>
            </form>
          </footer>
        )}

        {/* 开发者终端调试浮层 */}
        <TerminalHistory
          logs={logs}
          onClear={() => setLogs([])}
        />

        {/* 配置参数 SettingsModal 弹窗 */}
        <SettingsModal
          isOpen={isSettingsOpen}
          onClose={() => setIsSettingsOpen(false)}
          savedSettings={settings}
          onSave={handleSaveSettings}
        />
      </div>
    </div>
  );
}
