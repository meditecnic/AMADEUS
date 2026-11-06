/**
 * Shared SVG filters, mounted once by the app.
 * - hw-scorch: giant type printed badly and burnt at the edges (displacement + fine erosion).
 * - hw-dither: a high-contrast photocopy grain for the login's black-and-white mass.
 * Both are static: nothing here animates, so they cost one raster per element change.
 */
export function ScorchDefs() {
  return (
    <svg width="0" height="0" style={{ position: 'absolute' }} aria-hidden="true" focusable="false">
      <defs>
        <filter id="hw-scorch" x="-4%" y="-4%" width="108%" height="108%" colorInterpolationFilters="sRGB">
          <feTurbulence type="fractalNoise" baseFrequency="0.022 0.05" numOctaves="2" seed="7" result="warp" />
          <feDisplacementMap in="SourceGraphic" in2="warp" scale="5" xChannelSelector="R" yChannelSelector="G" result="bent" />
          <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="1" seed="3" result="pits" />
          <feColorMatrix in="pits" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 -12 8.9" result="pitMask" />
          <feComposite in="bent" in2="pitMask" operator="in" />
        </filter>
        <filter id="hw-dither" x="0" y="0" width="100%" height="100%" colorInterpolationFilters="sRGB">
          <feTurbulence type="fractalNoise" baseFrequency="0.62" numOctaves="2" seed="11" result="grain" />
          <feColorMatrix in="grain" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 -5 3.1" result="speck" />
          <feComposite in="SourceGraphic" in2="speck" operator="in" result="specked" />
          <feTurbulence type="fractalNoise" baseFrequency="0.006 0.02" numOctaves="3" seed="5" result="blot" />
          <feColorMatrix in="blot" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 -3 2.05" result="blotMask" />
          <feComposite in="specked" in2="blotMask" operator="in" />
        </filter>
      </defs>
    </svg>
  );
}
