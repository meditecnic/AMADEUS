import React, { useState, useRef } from 'react';
import SpriteAnimator from './SpriteAnimator';

interface LogoTransitionProps {
  onComplete: () => void;
}

const BOOT_RING = {
  sprite: '/assets/boot/boot_ring_3.png',
  cols: 2,
  rows: 5,
  w: 1920,
  h: 460.8,
  fps: 12,
} as const;

const LogoTransition: React.FC<LogoTransitionProps> = ({ onComplete }) => {
  const [fading, setFading] = useState(false);
  const doneRef = useRef(false);

  const handleAnimComplete = () => {
    if (doneRef.current) return;
    doneRef.current = true;
    setFading(true);
    setTimeout(() => {
      onComplete();
    }, 300);
  };

  return (
    <div
      className="logo-transition-container"
      style={{
        opacity: fading ? 0 : 1,
        transition: fading ? 'opacity 300ms ease-out' : 'none',
      }}
    >
      <SpriteAnimator
        spriteSrc={BOOT_RING.sprite}
        cols={BOOT_RING.cols}
        rows={BOOT_RING.rows}
        frameWidth={BOOT_RING.w}
        frameHeight={BOOT_RING.h}
        fps={BOOT_RING.fps}
        loop={false}
        autoplay={true}
        onComplete={handleAnimComplete}
        alt="Amadeus system boot"
      />
    </div>
  );
};

export default LogoTransition;
