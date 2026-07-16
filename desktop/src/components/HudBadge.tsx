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
  const dayLabels = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
  const statusLabel = status === 'READY' ? 'ONLINE' : 'OFFLINE';

  return (
    <div
      className={`hud-badge-container hud-badge-${worldline === 'beta' ? 'beta' : 'sg'}`}
      data-worldline={worldline}
    >
      <span className="hud-day-text">({dayLabels[currentDay]})</span>
      <span className="hud-date-text">{dateStr}</span>
      <div className={`hud-status-pill ${status === 'ERROR' ? 'status-error' : ''} ${status === 'SETUP' ? 'status-setup' : ''}`}>
        <span className="hud-status-dot" aria-hidden="true" />
        <span>{statusLabel}</span>
      </div>
    </div>
  );
};

export default HudBadge;
