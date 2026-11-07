import { DraftSheet } from './DraftSheet';

/* The SG0 Amadeus screen's backdrop: ragged columns of slashed binary. Seeded so it never reshuffles between renders. */
const BINARY = (() => {
  let s = 0x2f1d;
  const bit = () => ((s = (s * 1103515245 + 12345) & 0x7fffffff) >> 16) & 1;
  return Array.from({ length: 28 }, (_, i) => '/' + Array.from({ length: 18 + (i * 7) % 6 }, bit).join('')).join('\n');
})();

/**
 * Same room, different light, on the same drafting sheet as the login: SG gets cool slate paper and a clock movement,
 * β teal-black paper, the Lorenz attractor, phosphor grain and the Amadeus screen's binary.
 */
export function Atmosphere({ worldline }: { worldline: 'steins_gate' | 'beta' }) {
  return (
    <div className="ws2-atmo" aria-hidden="true">
      <div className="ws2-atmo-light" />
      <DraftSheet worldline={worldline} />
      {worldline === 'beta' && (
        <>
          <div className="ws2-atmo-bin">{BINARY}</div>
          <div className="ws2-atmo-crt" />
        </>
      )}
      <div className="ws2-atmo-grain" />
    </div>
  );
}
