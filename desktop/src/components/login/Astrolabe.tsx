import type { CSSProperties } from 'react';

type Worldline = 'steins_gate' | 'beta';

/** Divergence engravings on the scale ring. It rests on ∞ and is turned to the measured line on entry. */
const SCALE: { deg: number; text: string; line?: Worldline }[] = [
  { deg: 0, text: '∞' },
  { deg: 50, text: '0.571024' },
  { deg: 108, text: 'S;G 1.048596', line: 'steins_gate' },
  { deg: 164, text: '0.409431' },
  { deg: 212, text: '0.337187' },
  { deg: 258, text: 'β 1.129848', line: 'beta' },
  { deg: 308, text: '1.130205' },
];
export const RING_TURN: Record<Worldline, number> = { steins_gate: -108, beta: 102 };

const arc = (r: number, a0: number, a1: number) => {
  const p = (a: number) => {
    const t = ((a - 90) * Math.PI) / 180;
    return `${(r * Math.cos(t)).toFixed(2)} ${(r * Math.sin(t)).toFixed(2)}`;
  };
  return `M${p(a0)}A${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${p(a1)}`;
};
const d = (ms: number) => ({ '--d': `${ms}ms` } as CSSProperties);

/** Hairline instrument around the iris: limbus scale, divergence ring, orbit, rete. Drawn on arrival. */
export function Astrolabe({ worldline }: { worldline: Worldline }) {
  return (
    <svg
      className="sl-astro"
      viewBox="-320 -320 640 640"
      style={{ '--ring-to': `${RING_TURN[worldline]}deg` } as CSSProperties}
      aria-hidden="true"
    >
      {/* Rete: broken arcs that turn very slowly against everything else. */}
      <g className="rete-x"><g className="rete">
        <path className="ln" pathLength={1} style={d(260)} d={arc(266, 8, 84)} />
        <path className="ln" pathLength={1} style={d(340)} d={arc(266, 102, 146)} />
        <path className="ln" pathLength={1} style={d(420)} d={arc(266, 164, 268)} />
        <path className="ln" pathLength={1} style={d(500)} d={arc(266, 286, 344)} />
        {[8, 84, 102, 146, 164, 268, 286, 344].map((a) => (
          <path key={a} className="cap" d="M0 -261V-271" transform={`rotate(${a})`} />
        ))}
      </g></g>
      <g className="axes">
        {[0, 90, 180, 270].map((a) => (
          <path key={a} d="M0 -276V-292" transform={`rotate(${a})`} />
        ))}
        <circle className="far" r="284" />
      </g>
      {/* Orbit: two attractors keep their own time. */}
      <circle className="orbit" r="230" />
      <g className="node n1"><circle cy="-230" r="2.3" /></g>
      <g className="node n2"><circle cy="-230" r="1.5" /></g>
      {/* Divergence scale: turned to the line at the moment of measurement. */}
      <g className="scale">
        <circle className="ln" pathLength={1} style={d(120)} r="184" />
        <g className="ticks">
          {Array.from({ length: 120 }, (_, i) => (
            <path key={i} d={`M0 -184V${i % 5 === 0 ? -190.5 : -187.5}`} transform={`rotate(${i * 3})`} className={i % 5 === 0 ? 'major' : undefined} />
          ))}
        </g>
        {SCALE.map((m) => (
          <g key={m.deg} transform={`rotate(${m.deg})`} className={`mark${m.line ? ` line-${m.line === 'beta' ? 'beta' : 'sg'}` : ''}${m.deg === 0 ? ' inf' : ''}`}>
            <text y="-197" textAnchor="middle">{m.text}</text>
          </g>
        ))}
      </g>
      <path className="index" d="M-4.5 -219H4.5L0 -211.5Z" />
      {/* Limbus: the iris edge, ruled every two degrees, with a glint that travels round now and then. */}
      <g className="limbus">
        <circle className="ln" pathLength={1} style={d(0)} r="157" />
        <g className="ticks">
          {Array.from({ length: 180 }, (_, i) => (
            <path key={i} d={`M0 -160V${i % 5 === 0 ? -167 : -163}`} transform={`rotate(${i * 2})`} className={i % 5 === 0 ? 'major' : undefined} />
          ))}
        </g>
        <circle className="glint" r="157" pathLength={360} transform="rotate(-90)" />
      </g>
    </svg>
  );
}
