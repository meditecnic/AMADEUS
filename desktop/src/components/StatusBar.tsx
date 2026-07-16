import React from 'react';

interface StatusBarProps {
  worldline: 'steins_gate' | 'beta';
  connectionStatus: 'CONNECTING' | 'READY' | 'ERROR' | 'CLOSED' | 'SETUP';
}

export const StatusBar: React.FC<StatusBarProps> = ({ worldline, connectionStatus }) => {
  const worldlineLabel = worldline === 'steins_gate' ? 'STEINS;GATE' : 'β';

  return (
    <div className="salieri-status-bar">
      <div className="status-section left-section">
        <span className="label">WORLD LINE:</span>
        <span className={`val worldline-val ${worldline}`}>{worldlineLabel}</span>
      </div>
      
      <div className="status-section right-section">
        <span className="label">SYSTEM:</span>
        <span
          className={`val status-val ${connectionStatus.toLowerCase()}`}
          aria-live="polite"
          aria-atomic="true"
        >
          {connectionStatus === 'READY' && '准备就绪'}
          {connectionStatus === 'CONNECTING' && '正在连接'}
          {connectionStatus === 'ERROR' && '连接异常'}
          {connectionStatus === 'CLOSED' && '已断开'}
          {connectionStatus === 'SETUP' && '待配置'}
        </span>
        <span className={`status-dot ${connectionStatus.toLowerCase()}`} />
      </div>
    </div>
  );
};
export default StatusBar;
