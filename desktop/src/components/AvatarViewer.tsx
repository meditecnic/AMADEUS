import React, { useState, useEffect, useRef, useLayoutEffect } from 'react';
import { hasAssetPack, packAsset } from '../assetPack';

// The portraits are original game art, shipped only in the optional asset pack.
const art = (path: string) => packAsset(path) ?? '';

const EMOTION_MAP: Record<string, { base: string; blink: string }> = {
  neutral: {
    base: '/assets/images/kurisu_normal',
    blink: '/assets/images/kurisu_eyes_closed1.png'
  },
  tsundere: {
    base: '/assets/images/kurisu_sided_angry',
    blink: '/assets/images/kurisu_sided_eyes_closed1.png'
  },
  embarrassed: {
    base: '/assets/images/kurisu_sided_blush',
    blink: '/assets/images/kurisu_sided_eyes_closed1.png'
  },
  intellectual: {
    base: '/assets/images/kurisu_sided_thinking',
    blink: '/assets/images/kurisu_sided_eyes_closed1.png'
  },
  happy: {
    base: '/assets/images/kurisu_happy',
    blink: '/assets/images/kurisu_eyes_closed1.png'
  },
  surprised: {
    base: '/assets/images/kurisu_sided_surprised',
    blink: '/assets/images/kurisu_sided_eyes_closed1.png'
  },
  annoyed: {
    base: '/assets/images/kurisu_annoyed',
    blink: '/assets/images/kurisu_eyes_closed1.png'
  },
  disappointed: {
    base: '/assets/images/kurisu_disappointed',
    blink: '/assets/images/kurisu_eyes_closed1.png'
  },
  sad: {
    base: '/assets/images/kurisu_sad',
    blink: '/assets/images/kurisu_eyes_closed1.png'
  },
};

type Emotion = 'neutral' | 'tsundere' | 'embarrassed' | 'intellectual' | 'happy' | 'surprised' | 'annoyed' | 'disappointed' | 'sad';

interface AvatarViewerProps {
  emotion: Emotion;
  isSpeaking: boolean;
  volume: number;
  glitchState: 'none' | 'light' | 'heavy';
  /** stage: layout and lighting come from the workstation stylesheet (no inline sizing, no glow ring). */
  variant?: 'legacy' | 'stage';
}

const AvatarViewer: React.FC<AvatarViewerProps> = ({
  emotion,
  isSpeaking,
  volume,
  glitchState,
  variant = 'legacy',
}) => {
  const [isBlinking, setIsBlinking] = useState(false);
  const img1Ref = useRef<HTMLImageElement>(null);
  const img2Ref = useRef<HTMLImageElement>(null);
  const img3Ref = useRef<HTMLImageElement>(null);
  const imgBlinkRef = useRef<HTMLImageElement>(null);
  const config = EMOTION_MAP[emotion] || EMOTION_MAP.neutral;

  const isSpeakingRef = useRef(isSpeaking);
  const isBlinkingRef = useRef(isBlinking);
  const volumeRef = useRef(volume);
  useEffect(() => {
    isSpeakingRef.current = isSpeaking;
    isBlinkingRef.current = isBlinking;
    volumeRef.current = volume;
  }, [isSpeaking, isBlinking, volume]);

  useEffect(() => {
    let blinkTimer: number | null = null;
    let openTimer: number | null = null;
    let isCurrent = true;

    const blinkCycle = () => {
      const nextBlinkTime = 4000 + Math.random() * 3000;
      blinkTimer = window.setTimeout(() => {
        if (!isCurrent) return;
        if (!isSpeaking) {
          setIsBlinking(true);
          openTimer = window.setTimeout(() => {
            if (!isCurrent) return;
            setIsBlinking(false);
            blinkCycle();
          }, 150);
        } else {
          blinkCycle();
        }
      }, nextBlinkTime);
    };

    blinkCycle();

    return () => {
      isCurrent = false;
      if (blinkTimer !== null) window.clearTimeout(blinkTimer);
      if (openTimer !== null) window.clearTimeout(openTimer);
    };
  }, [isSpeaking]);

  let glitchClass = '';
  if (glitchState === 'heavy') glitchClass = 'glitch-heavy';
  else if (glitchState === 'light') glitchClass = 'glitch-light';

  const img1 = art(`${config.base}1.png`);
  const img2 = art(`${config.base}2.png`);
  const img3 = art(`${config.base}3.png`);
  const imgBlink = art(config.blink);

  useLayoutEffect(() => {
    let rafId: number | null = null;
    const apply = () => {
      const speaking = isSpeakingRef.current;
      const vol = volumeRef.current;
      const blinking = isBlinkingRef.current;
      let mouthLevel = 1;
      if (speaking && vol > 0.05) {
        mouthLevel = vol > 0.25 ? 3 : 2;
      }
      const showBlink = blinking && !speaking;
      const o1 = (!showBlink && mouthLevel === 1) ? 1 : 0;
      const o2 = (!showBlink && mouthLevel === 2) ? 1 : 0;
      const o3 = (!showBlink && mouthLevel === 3) ? 1 : 0;
      const oBlink = showBlink ? 1 : 0;
      if (img1Ref.current) img1Ref.current.style.opacity = String(o1);
      if (img2Ref.current) img2Ref.current.style.opacity = String(o2);
      if (img3Ref.current) img3Ref.current.style.opacity = String(o3);
      if (imgBlinkRef.current) imgBlinkRef.current.style.opacity = String(oBlink);
      rafId = requestAnimationFrame(apply);
    };
    rafId = requestAnimationFrame(apply);
    return () => { if (rafId) cancelAnimationFrame(rafId); };
  }, []);

  if (!hasAssetPack()) {
    return (
      <div className={`avatar-viewer-container avatar-absent${variant === 'stage' ? ' avatar-stage' : ''}`} data-testid="avatar-absent">
        <p>立绘未载入<small>安装原作素材包后显示</small></p>
      </div>
    );
  }

  return (
    <div
      className={`avatar-viewer-container${variant === 'stage' ? ' avatar-stage' : ''}`}
      style={variant === 'stage' ? undefined : {
        height: '100%',
        width: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        overflow: 'hidden',
        maxHeight: '720px'
      }}
    >
      {variant !== 'stage' && (
        <div
          className={`avatar-glow-ring${isSpeaking ? ' paused' : ''}`}
          style={{
            width: '280px',
            height: '280px',
            background: 'radial-gradient(circle, rgba(255,108,0,0.14) 0%, rgba(0,0,0,0) 70%)',
            zIndex: 1
          }}
        />
      )}

      <div
        className={`avatar-sprite-wrapper breath-animation ${glitchClass}`}
        style={variant === 'stage' ? undefined : { zIndex: 2 }}
      >
        <img
          src={img1}
          alt="Kurisu Calm"
          className="avatar-sprite-layer"
          ref={img1Ref}
          onError={(e) => {
            e.currentTarget.src = art('/assets/images/kurisu_normal1.png');
          }}
        />

        <img
          src={img2}
          alt="Kurisu Talking Half"
          className="avatar-sprite-layer"
          ref={img2Ref}
          onError={(e) => {
            e.currentTarget.src = art('/assets/images/kurisu_normal2.png');
          }}
        />

        <img
          src={img3}
          alt="Kurisu Talking Open"
          className="avatar-sprite-layer"
          ref={img3Ref}
          onError={(e) => {
            e.currentTarget.src = art('/assets/images/kurisu_normal3.png');
          }}
        />

        <img
          src={imgBlink}
          alt="Kurisu Blink"
          className="avatar-sprite-layer"
          ref={imgBlinkRef}
          onError={(e) => {
            e.currentTarget.src = art('/assets/images/kurisu_eyes_closed1.png');
          }}
        />
      </div>
    </div>
  );
};

export default React.memo(AvatarViewer);
