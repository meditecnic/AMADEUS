import React from 'react';

interface CRTOverlayProps {
  glitchState?: 'none' | 'light' | 'heavy';
  zIndex?: number;
}

/**
 * CRT overlay: chromatic aberration (SVG filters) + vignette.
 * Ported from frontend. z-index lowered from 9999 to 50 (configurable)
 * so it sits above workstation content but below modal overlays.
 * pointer-events: none ensures no interaction blocking.
 *
 * NOTE: This component does NOT handle scanlines or noise.
 * Those are implemented separately in CSS (scanline @keyframes + SVG feTurbulence).
 */
export const CRTOverlay: React.FC<CRTOverlayProps> = ({
  glitchState = 'none',
  zIndex = 50,
}) => {
  const heavyDx = glitchState === 'heavy' ? 5 : 0;
  const lightDx = glitchState === 'light' ? 2 : 0;

  return (
    <>
      {/* Hidden SVG filter definitions for channel separation */}
      <svg className="absolute w-0 h-0 pointer-events-none" style={{ position: 'absolute', width: 0, height: 0 }}>
        <defs>
          {/* Heavy chromatic aberration (triggered by tsundere emotion or errors) */}
          <filter id="chromatic-aberration-heavy">
            <feColorMatrix type="matrix" values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" in="SourceGraphic" result="red" />
            <feOffset dx={-heavyDx} dy="1" in="red" result="red-offset" />

            <feColorMatrix type="matrix" values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0" in="SourceGraphic" result="green" />
            <feOffset dx="1" dy={-1} in="green" result="green-offset" />

            <feColorMatrix type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0" in="SourceGraphic" result="blue" />
            <feOffset dx={heavyDx} dy="0" in="blue" result="blue-offset" />

            <feBlend mode="screen" in="red-offset" in2="green-offset" result="red-green" />
            <feBlend mode="screen" in="red-green" in2="blue-offset" />
          </filter>

          {/* Light chromatic aberration (triggered by embarrassed emotion or connection handshake) */}
          <filter id="chromatic-aberration-light">
            <feColorMatrix type="matrix" values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" in="SourceGraphic" result="red" />
            <feOffset dx={-lightDx} dy="0" in="red" result="red-offset" />

            <feColorMatrix type="matrix" values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0" in="SourceGraphic" result="green" />

            <feColorMatrix type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0" in="SourceGraphic" result="blue" />
            <feOffset dx={lightDx} dy="0" in="blue" result="blue-offset" />

            <feBlend mode="screen" in="red-offset" in2="green" result="red-green" />
            <feBlend mode="screen" in="red-green" in2="blue-offset" />
          </filter>
        </defs>
      </svg>

      {/* CRT physical vignette and shadow for retro decayed feel */}
      <div
        className="pointer-events-none absolute inset-0"
        data-crt-vignette="true"
        style={{
          zIndex,
          boxShadow: 'inset 0 0 96px rgba(0, 0, 0, 0.72)',
          background: 'radial-gradient(circle, transparent 58%, rgba(0, 0, 0, 0.34) 100%)',
        }}
      />
    </>
  );
};
