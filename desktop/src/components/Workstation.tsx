import React, { useState } from 'react';
import { useAmadeusWS } from '../hooks/useAmadeusWS';
import { StatusBar } from './StatusBar';
import { SettingsModal } from './SettingsModal';

interface WorkstationProps {
  worldline: 'steins_gate' | 'beta';
  onWorldlineChange: (wl: 'steins_gate' | 'beta') => void;
  onLogout: () => void;
}

export const Workstation: React.FC<WorkstationProps> = ({
  worldline,
  onWorldlineChange,
  onLogout,
}) => {
  const [systemPrompt, setSystemPrompt] = useState('');
  const [enableTts, setEnableTts] = useState(true);
  const [sovitsUrl, setSovitsUrl] = useState('http://127.0.0.1:9880');
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [inputText, setInputText] = useState('');
  const [apiKey, setApiKey] = useState(() => {
    try {
      return localStorage.getItem('amadeus_pc_api_key') || '';
    } catch (e) {
      return '';
    }
  });

  // WebSocket hook connection management
  const {
    status,
    messages,
    isThinking,
    isSynthesizing,
    activeTool,
    activeNotice,
    clearHistory,
    sendChat,
    updateConfig,
  } = useAmadeusWS({
    apiKey,
    systemPrompt,
    temperature: 0.8,
    worldline,
    enableTts,
    sovitsUrl,
    onAudioChunk: (sentenceId, base64Data) => {
      console.log(`[Audio Chunk RX] sentence_id=${sentenceId}, bytes=${base64Data.length} (no-op playback)`);
    },
  });

  const handleSend = (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputText.trim()) return;
    sendChat(inputText.trim());
    setInputText('');
  };

  const handleSaveSettings = (newSettings: {
    worldline: 'steins_gate' | 'beta';
    apiKey: string;
    systemPrompt: string;
    enableTts: boolean;
    sovitsUrl: string;
  }) => {
    // 1. Sync React parent state if worldline changed
    if (newSettings.worldline !== worldline) {
      onWorldlineChange(newSettings.worldline);
    }
    
    // 2. Sync local states
    setApiKey(newSettings.apiKey);
    setSystemPrompt(newSettings.systemPrompt);
    setEnableTts(newSettings.enableTts);
    setSovitsUrl(newSettings.sovitsUrl);

    try {
      localStorage.setItem('amadeus_pc_api_key', newSettings.apiKey);
    } catch (e) {
      console.warn('LocalStorage save failed:', e);
    }

    // 3. Send config_update WS frame to backend
    updateConfig({
      apiKey: newSettings.apiKey,
      systemPrompt: newSettings.systemPrompt,
      worldline: newSettings.worldline,
      enableTts: newSettings.enableTts,
      sovitsUrl: newSettings.sovitsUrl,
      temperature: 0.8,
    });
  };

  return (
    <div className="salieri-workstation-panel">
      {/* Top Header Controls */}
      <div className="workstation-header">
        <div className="left-brand">
          <span className="brand-title">Amadeus</span>
          <span className="brand-sub">SALIERI CONSOLE</span>
        </div>
        <div className="right-controls">
          <button className="ctrl-btn settings-btn" onClick={() => setIsSettingsOpen(true)}>
            系统设置
          </button>
          <button 
            className="ctrl-btn clear-mem-btn" 
            onClick={() => {
              if (window.confirm("确定要物理清除当前会话的所有对话和记忆摘要吗？此操作不可逆！")) {
                clearHistory();
              }
            }}
            style={{ color: "#ff3333", borderColor: "#ff3333" }}
          >
            清除记忆
          </button>
          <button className="ctrl-btn disconnect-btn" onClick={onLogout}>
            断开连接
          </button>
        </div>
      </div>

      {/* Main Grid Workspace */}
      <div className="workstation-grid">
        {/* Left Side: Sidebar Placeholders */}
        <div className="grid-sidebar">
          <div className="sidebar-section">
            <h3 className="section-title">会话历史</h3>
            <div className="sidebar-placeholder">
              <span className="blink-txt">&gt; 历史记录离线</span>
              <p className="placeholder-note">Tauri SQLite 数据库连接已激活。历史已同步。</p>
            </div>
          </div>
          <div className="sidebar-section">
            <h3 className="section-title">系统预设</h3>
            <div className="sidebar-placeholder">
              <span>&gt; 基础记忆保留模式</span>
              <p className="placeholder-note">牧瀬红莉栖大脑模块拷贝激活。</p>
            </div>
          </div>
        </div>

        {/* Center: Dialogue Terminal */}
        <div className="grid-terminal">
          <div className="terminal-chat-viewport">
            {messages.length === 0 ? (
              <div className="empty-chat-tip">
                <div className="amadeus-logo-glitched" />
                <p className="terminal-welcome-msg">&gt; Amadeus 系统连接已建立。等待用户输入...</p>
              </div>
            ) : (
              <div className="messages-flow">
                {messages.map((msg) => (
                  <div key={msg.id} className={`message-card ${msg.role}`}>
                    <div className="avatar-label">
                      {msg.role === 'user' ? 'USER ID: OKABE' : 'AMADEUS: KURISU'}
                    </div>
                    <div className="message-content-box">
                      {msg.role === 'assistant' && msg.translation ? (
                        <>
                          <p className="chinese-main">{msg.translation}</p>
                          <p className="japanese-sub">（{msg.content}）</p>
                        </>
                      ) : (
                        <p className="japanese-raw">{msg.content}</p>
                      )}
                    </div>
                  </div>
                ))}
                {isThinking && (
                  <div className="message-card assistant loading-state">
                    <div className="avatar-label">AMADEUS: KURISU</div>
                    <div className="thinking-loader">
                      <span className="loader-dot" />
                      <span className="loader-dot" />
                      <span className="loader-dot" />
                      <span className="thinking-txt">
                        {activeTool === 'web_search'
                          ? '正在联网检索外部数据库...'
                          : (activeNotice || '正在调用记忆模块...')}
                      </span>
                    </div>
                  </div>
                )}
                {isSynthesizing && !isThinking && (
                  <div className="status-synthesizing-tag">
                    <span className="speaking-dot animate-ping" />
                    正在合成语音...
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Bottom Chat Input Form */}
          <form className="terminal-input-bar" onSubmit={handleSend}>
            <span className="prompt-arrow">&gt;</span>
            <input
              type="text"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              className="chat-input-field"
              placeholder="输入终端指令或对话内容..."
              disabled={status !== 'READY' || isThinking}
            />
            <button
              type="submit"
              className="chat-send-btn"
              disabled={status !== 'READY' || !inputText.trim() || isThinking}
            >
              发送
            </button>
          </form>
        </div>
      </div>

      {/* Bottom Status Bar */}
      <StatusBar worldline={worldline} connectionStatus={status} />

      {/* Settings Modal overlay */}
      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        worldline={worldline}
        apiKey={apiKey}
        systemPrompt={systemPrompt}
        enableTts={enableTts}
        sovitsUrl={sovitsUrl}
        onSaveSettings={handleSaveSettings}
      />
    </div>
  );
};
export default Workstation;
