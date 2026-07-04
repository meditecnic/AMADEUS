import React, { useState, useEffect } from 'react';

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
}

const AvatarViewer: React.FC<AvatarViewerProps> = ({
  emotion,
  isSpeaking,
  volume,
  glitchState
}) => {
  const [isBlinking, setIsBlinking] = useState(false);
  const config = EMOTION_MAP[emotion] || EMOTION_MAP.neutral;

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

  let mouthLevel = 1;
  if (isSpeaking && volume > 0.05) {
    if (volume > 0.25) {
      mouthLevel = 3;
    } else {
      mouthLevel = 2;
    }
  }

  let glitchClass = '';
  if (glitchState === 'heavy') glitchClass = 'glitch-heavy';
  else if (glitchState === 'light') glitchClass = 'glitch-light';

  const showBlink = isBlinking && !isSpeaking;
  const opacity1 = (!showBlink && mouthLevel === 1) ? 1 : 0;
  const opacity2 = (!showBlink && mouthLevel === 2) ? 1 : 0;
  const opacity3 = (!showBlink && mouthLevel === 3) ? 1 : 0;
  const opacityBlink = showBlink ? 1 : 0;

  const img1 = `${config.base}1.png`;
  const img2 = `${config.base}2.png`;
  const img3 = `${config.base}3.png`;
  const imgBlink = config.blink;

  return (
    <div
      className="avatar-viewer-container"
      style={{
        height: '100%',
        width: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        overflow: 'hidden',
        maxHeight: '720px'
      }}
    >
      <div
        className="avatar-glow-ring"
        style={{
          width: '280px',
          height: '280px',
          background: 'radial-gradient(circle, rgba(255,108,0,0.14) 0%, rgba(0,0,0,0) 70%)',
          zIndex: 1
        }}
      />

      <div
        className={`avatar-sprite-wrapper breath-animation ${glitchClass}`}
        style={{ zIndex: 2 }}
      >
        <img
          src={img1}
          alt="Kurisu Calm"
          className="avatar-sprite-layer"
          style={{ opacity: opacity1, transition: 'opacity 120ms ease-in-out' }}
          onError={(e) => {
            e.currentTarget.src = '/assets/images/kurisu_normal1.png';
          }}
        />

        <img
          src={img2}
          alt="Kurisu Talking Half"
          className="avatar-sprite-layer"
          style={{ opacity: opacity2, transition: 'opacity 120ms ease-in-out' }}
          onError={(e) => {
            e.currentTarget.src = '/assets/images/kurisu_normal2.png';
          }}
        />

        <img
          src={img3}
          alt="Kurisu Talking Open"
          className="avatar-sprite-layer"
          style={{ opacity: opacity3, transition: 'opacity 120ms ease-in-out' }}
          onError={(e) => {
            e.currentTarget.src = '/assets/images/kurisu_normal3.png';
          }}
        />

        <img
          src={imgBlink}
          alt="Kurisu Blink"
          className="avatar-sprite-layer"
          style={{ opacity: opacityBlink, transition: 'opacity 120ms ease-in-out' }}
          onError={(e) => {
            e.currentTarget.src = '/assets/images/kurisu_eyes_closed1.png';
          }}
        />
      </div>
    </div>
  );
};

export default AvatarViewer;
