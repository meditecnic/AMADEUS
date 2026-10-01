import React, { useState, useEffect, useRef } from 'react';
import { Trash2, Copy, X } from 'lucide-react';

export interface LogEntry {
  id: string;
  timestamp: number;
  direction: 'TX' | 'RX';
  data: string;
}

interface TerminalHistoryProps {
  logs: LogEntry[];
  onClear: () => void;
}

export const TerminalHistory: React.FC<TerminalHistoryProps> = ({
  logs,
  onClear
}) => {
  const [isVisible, setIsVisible] = useState(false);
  const [copied, setCopied] = useState(false);
  const terminalEndRef = useRef<HTMLDivElement>(null);

  // 监听 Ctrl + ` 全局快捷键
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // 兼容某些输入法或操作系统的波浪号/反引号键
      if (e.ctrlKey && (e.key === '`' || e.key === 'Backquote' || e.code === 'Backquote')) {
        e.preventDefault();
        setIsVisible(prev => !prev);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  // 自动滚动 to latest logs
  useEffect(() => {
    if (isVisible && terminalEndRef.current) {
      terminalEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, isVisible]);

  if (!isVisible) return null;

  const handleCopy = async () => {
    try {
      const jsonStr = JSON.stringify(logs, null, 2);
      await navigator.clipboard.writeText(jsonStr);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy logs:', err);
    }
  };

  const formatTime = (ts: number) => {
    const date = new Date(ts);
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    const seconds = String(date.getSeconds()).padStart(2, '0');
    const ms = String(date.getMilliseconds()).padStart(3, '0');
    return `${hours}:${minutes}:${seconds}.${ms}`;
  };

  return (
    <div 
      data-testid="terminal-history"
      className="absolute inset-0 z-40 bg-[#050302]/98 flex flex-col font-mono text-[11px] text-[#ff6c00] border-t border-[#3c1e10]"
      style={{
        boxShadow: 'inset 0 10px 30px rgba(0,0,0,0.8)'
      }}
    >
      {/* Console Header */}
      <div className="flex justify-between items-center bg-[#0c0604] border-b border-[#3c1e10] px-3 py-2 shrink-0">
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-full bg-[#ff6c00] animate-ping" />
          <span className="font-bold text-xs tracking-widest text-[#ff6c00]">
            DEVELOPER TERMINAL LOGS
          </span>
        </div>
        <div className="flex items-center gap-3">
          <button 
            onClick={handleCopy}
            title="Copy Logs JSON"
            className="text-[#ff6c00]/60 hover:text-[#ff6c00] flex items-center gap-1 transition-colors"
          >
            <Copy size={13} />
            <span>{copied ? 'COPIED!' : 'COPY'}</span>
          </button>
          <button 
            onClick={onClear}
            title="Clear Logs"
            className="text-[#ff6c00]/60 hover:text-red-500 flex items-center gap-1 transition-colors"
          >
            <Trash2 size={13} />
            <span>CLEAR</span>
          </button>
          <button 
            onClick={() => setIsVisible(false)}
            title="Close Console"
            className="text-gray-500 hover:text-[#ff6c00] transition-colors"
          >
            <X size={15} />
          </button>
        </div>
      </div>

      {/* Log list console */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2 select-text leading-relaxed">
        {logs.length === 0 ? (
          <div className="text-gray-600 italic text-center mt-8">
            -- NO WEBSOCKET TRAFFIC RECORDED --
          </div>
        ) : (
          logs.map((log) => (
            <div 
              key={log.id} 
              className="border-b border-[#1f1008]/40 pb-1.5 flex flex-col gap-0.5 break-all"
            >
              <div className="flex items-center gap-1.5 text-gray-500 text-[10px]">
                <span>[{formatTime(log.timestamp)}]</span>
                <span>({log.timestamp})</span>
                <span 
                  className={`font-bold px-1 rounded ${
                    log.direction === 'TX' 
                      ? 'bg-[#00ff66]/10 text-[#00ff66]' 
                      : 'bg-[#ff6c00]/10 text-[#ff6c00]'
                  }`}
                >
                  {log.direction}
                </span>
              </div>
              <pre 
                data-testid="terminal-log-item"
                className={`pl-4 whitespace-pre-wrap ${
                  log.direction === 'TX' ? 'text-[#a3f7bf]' : 'text-[#ffb880]'
                }`}
              >
                {log.data}
              </pre>
            </div>
          ))
        )}
        <div ref={terminalEndRef} />
      </div>
      
      {/* Keyboard info bottom bar */}
      <div className="bg-[#080402] border-t border-[#3c1e10] px-3 py-1 text-[9px] text-gray-600 shrink-0 text-right select-none">
        PRESS <kbd className="bg-[#1b0d07] px-1 border border-[#3c1e10] rounded text-gray-400">Ctrl + `</kbd> TO CLOSE TERMINAL
      </div>
    </div>
  );
};
