import React from 'react';

interface CircuitTraceProps {
  className?: string;
  opacity?: number;
  zIndex?: number;
}

/**
 * SVG circuit trace decorative overlay.
 * Renders flat, glowing circuit-board traces (right-angle paths + nodes)
 * as a tiling pattern. NOT metallic/rusted — flat orange lines with glow.
 * Designed to sit as a background atmosphere layer at low opacity.
 */
export const CircuitTrace: React.FC<CircuitTraceProps> = ({
  className = '',
  opacity = 0.05,
  zIndex = 1,
}) => {
  return (
    <svg
      className={`pointer-events-none absolute inset-0 ${className}`}
      style={{ width: '100%', height: '100%', opacity, zIndex }}
      aria-hidden="true"
    >
      <defs>
        {/* Tileable circuit pattern: 320x320 grid */}
        <pattern id="circuit-trace-pattern" x="0" y="0" width="320" height="320" patternUnits="userSpaceOnUse">
          {/* Horizontal traces with right-angle bends */}
          <g stroke="#ff6c00" strokeWidth="1.0" fill="none" opacity="0.8">
            {/* Trace 1: top-left horizontal with bend down */}
            <path d="M 0 40 L 80 40 L 80 80 L 140 80 L 140 40 L 200 40 L 200 100 L 280 100 L 280 40 L 320 40" />
            {/* Trace 2: mid horizontal with bend up */}
            <path d="M 0 120 L 60 120 L 60 160 L 120 160 L 120 120 L 180 120 L 180 200 L 260 200 L 260 120 L 320 120" />
            {/* Trace 3: lower horizontal with bends */}
            <path d="M 0 240 L 40 240 L 40 280 L 100 280 L 100 240 L 160 240 L 160 300 L 220 300 L 220 240 L 320 240" />
            {/* Trace 4: top area */}
            <path d="M 40 0 L 40 20 L 100 20 L 100 0" />
            <path d="M 180 0 L 180 15 L 240 15 L 240 0" />

            {/* Vertical traces */}
            <path d="M 20 0 L 20 60 L 50 60" />
            <path d="M 100 60 L 100 0" />
            <path d="M 220 0 L 220 50 L 250 50 L 250 0" />
            <path d="M 300 0 L 300 70" />

            <path d="M 60 160 L 60 320" />
            <path d="M 140 100 L 140 320" />
            <path d="M 200 200 L 200 320" />
            <path d="M 280 100 L 280 320" />
          </g>

          {/* Trace highlights: brighter center segments */}
          <g stroke="#ffaa00" strokeWidth="0.7" fill="none" opacity="0.6">
            <path d="M 80 40 L 140 40" />
            <path d="M 120 120 L 180 120" />
            <path d="M 100 240 L 160 240" />
          </g>

          {/* Junction nodes: small circles at trace intersections */}
          <g fill="#ff6c00" opacity="0.7">
            <circle cx="80" cy="40" r="2" />
            <circle cx="140" cy="40" r="2" />
            <circle cx="200" cy="40" r="2" />
            <circle cx="280" cy="40" r="2" />
            <circle cx="60" cy="120" r="2" />
            <circle cx="120" cy="120" r="2" />
            <circle cx="180" cy="120" r="2" />
            <circle cx="260" cy="120" r="2" />
            <circle cx="40" cy="240" r="2" />
            <circle cx="100" cy="240" r="2" />
            <circle cx="160" cy="240" r="2" />
            <circle cx="220" cy="240" r="2" />
            <circle cx="280" cy="240" r="2" />
          </g>

          {/* Larger pad nodes at trace endpoints/turns */}
          <g fill="none" stroke="#ff6c00" strokeWidth="0.8" opacity="0.55">
            <circle cx="80" cy="80" r="3" />
            <circle cx="140" cy="80" r="3" />
            <circle cx="200" cy="100" r="3" />
            <circle cx="60" cy="160" r="3" />
            <circle cx="180" cy="200" r="3" />
            <circle cx="100" cy="280" r="3" />
            <circle cx="220" cy="300" r="3" />
          </g>

          {/* Small chip-like rectangles at a few spots */}
          <g fill="#ff6c00" fillOpacity="0.1" stroke="#ff6c00" strokeWidth="0.5" opacity="0.5">
            <rect x="75" y="75" width="10" height="10" rx="1" />
            <rect x="195" y="95" width="10" height="10" rx="1" />
            <rect x="175" y="195" width="10" height="10" rx="1" />
          </g>
        </pattern>
      </defs>

      {/* Full coverage rect with the circuit pattern */}
      <rect width="100%" height="100%" fill="url(#circuit-trace-pattern)" />
    </svg>
  );
};
