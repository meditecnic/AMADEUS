import React from 'react';

interface HudBadgeProps {
  worldline: 'steins_gate' | 'beta';
  status: string;
}

function formatDate(): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, '0');
  const d = String(now.getDate()).padStart(2, '0');
  return `${y}.${m}.${d}`;
}

export const HudBadge: React.FC<HudBadgeProps> = ({ worldline, status }) => {
  const dateStr = formatDate();
  const currentDay = new Date().getDay(); // 0-6 (0=SUN, 1=MON...)

  return (
    <div className="hud-badge-container">
      <div className="hud-pill hud-date-pill">
        <span className="hud-date-text">{dateStr}</span>
        <div className="hud-days-container">
          <div 
            className="hud-day-indicator"
            style={{
              backgroundPositionY: `${(currentDay / 6) * 100}%`
            }}
          />
        </div>
      </div>
      
      <div className={`worldline-badge ${worldline === 'steins_gate' ? 'alpha' : 'beta'}`} />
      
      <div className={`hud-pill hud-status-pill ${status === 'ERROR' ? 'status-error' : ''}`}>
        {status === 'READY' ? 'ONLINE' : status.toUpperCase()}
      </div>
    </div>
  );
};

export default HudBadge;
