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

function getStatusLabel(status: string): string {
  switch (status) {
    case 'READY': return 'ONLINE';
    case 'CONNECTING': return 'CONNECTING';
    case 'ERROR': return 'ERROR';
    case 'CLOSED': return 'OFFLINE';
    default: return status.toUpperCase();
  }
}

export const HudBadge: React.FC<HudBadgeProps> = ({ worldline, status }) => {
  const dateStr = formatDate();
  const worldlineLabel = worldline === 'steins_gate' ? 'SG 1.048596%' : 'β 1.130205%';
  const statusLabel = getStatusLabel(status);

  return (
    <div className="hud-badge-container">
      <div className="hud-pill">{dateStr}</div>
      <div className={`hud-pill ${worldline === 'beta' ? 'worldline-beta' : 'worldline-sg'}`}>{worldlineLabel}</div>
      <div className={`hud-pill ${status === 'ERROR' ? 'status-error' : ''}`}>{statusLabel}</div>
    </div>
  );
};

export default HudBadge;
