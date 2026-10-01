/* The SG0 Amadeus screen's backdrop: ragged columns of slashed binary. Seeded so it never reshuffles between renders. */
const BINARY = (() => {
  let s = 0x2f1d;
  const bit = () => ((s = (s * 1103515245 + 12345) & 0x7fffffff) >> 16) & 1;
  return Array.from({ length: 28 }, (_, i) => '/' + Array.from({ length: 18 + (i * 7) % 6 }, bit).join('')).join('\n');
})();

/** Same room, different light: SG gets cool daylight over a blueprint draft, β a low amber glow, phosphor grain and binary. */
export function Atmosphere({ worldline }: { worldline: 'steins_gate' | 'beta' }) {
  return (
    <div className="ws2-atmo" aria-hidden="true">
      <div className="ws2-atmo-light" />
      {worldline === 'steins_gate' ? (
        <svg className="ws2-atmo-draft" viewBox="0 0 800 800" preserveAspectRatio="xMidYMid slice">
          <defs>
            <pattern id="ws2-blueprint" width="16" height="16" patternUnits="userSpaceOnUse">
              <path d="M16 0H0V16" fill="none" stroke="rgb(128, 136, 240)" strokeWidth=".35" />
            </pattern>
          </defs>
          <circle className="bp" cx="520" cy="300" r="250" fill="url(#ws2-blueprint)" />
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
        <>
          <div className="ws2-atmo-grid" />
          <div className="ws2-atmo-bin">{BINARY}</div>
          <div className="ws2-atmo-crt" />
        </>
      )}
      <div className="ws2-atmo-grain" />
    </div>
  );
}
