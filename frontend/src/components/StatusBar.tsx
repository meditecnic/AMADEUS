import React, { useEffect } from 'react';

interface StatusBarProps {
  notice: string;
  onClearNotice: () => void;
  wsState?: 'thinking' | 'speaking' | 'idle';
}

export const StatusBar: React.FC<StatusBarProps> = ({
  notice,
  onClearNotice,
  wsState
}) => {
  // Notice 通告防抖清除定时器
  useEffect(() => {
    if (notice) {
      const timer = setTimeout(() => {
        onClearNotice();
      }, 5000);
      return () => clearTimeout(timer);
    }
  }, [notice, onClearNotice]);

  const containerClass = `w-full bg-[#080402] border-b border-[#3c1e10] px-3 py-1 flex items-center justify-between text-[10px] text-[#ff6c00] font-mono select-none ${
    wsState === 'thinking' ? 'state-thinking' : wsState === 'speaking' ? 'state-speaking' : ''
  }`;

  return (
    <div
      data-testid="status-bar"
      className={containerClass}
      style={{ minHeight: '22px' }}
    >
      {/* Notice Area */}
      <div
        data-testid="status-notice"
        className="flex-grow mr-2 overflow-hidden text-ellipsis whitespace-nowrap"
      >
        {notice ? (
          notice
        ) : (
          wsState === 'thinking' ? (
            <span className="font-bold text-[#ff6c00]">THINKING</span>
          ) : wsState === 'speaking' ? (
            <span className="font-bold text-[#ff6c00]">SPEAKING</span>
          ) : null
        )}
      </div>
    </div>
  );
};
