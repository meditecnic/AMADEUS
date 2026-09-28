/** Same room, different light: SG gets cool daylight and faint drafting lines, β a low amber glow and phosphor grain. */
export function Atmosphere({ worldline }: { worldline: 'steins_gate' | 'beta' }) {
  return (
    <div className="ws2-atmo" aria-hidden="true">
      <div className="ws2-atmo-light" />
      {worldline === 'steins_gate' ? (
        <svg className="ws2-atmo-draft" viewBox="0 0 800 800" preserveAspectRatio="xMidYMid slice">
          <g fill="none" stroke="currentColor">
            <circle cx="520" cy="300" r="250" strokeWidth=".6" />
            <circle cx="520" cy="300" r="180" strokeWidth=".6" strokeDasharray="2 6" />
            <circle cx="520" cy="300" r="96" strokeWidth=".6" />
            <path d="M520 20v560M240 300h560" strokeWidth=".5" />
            <path d="M330 110l380 380M710 110L330 490" strokeWidth=".35" strokeDasharray="1 7" />
            <path d="M560 610h200M560 602v16M760 602v16" strokeWidth=".6" />
          </g>
        </svg>
      ) : (
        <div className="ws2-atmo-crt" />
      )}
      <div className="ws2-atmo-grain" />
    </div>
  );
}
