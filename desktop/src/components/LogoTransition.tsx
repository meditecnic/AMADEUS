import React, { useState, useEffect, useRef } from 'react';
import { packAsset } from '../assetPack';

// Mounted only when the pack is present (App skips this step otherwise).
const logoFrame = (n: number) => packAsset(`assets/images/logo${n}.png`) ?? '';

interface LogoTransitionProps {
  onComplete: () => void;
}

const LogoTransition: React.FC<LogoTransitionProps> = ({ onComplete }) => {
  const [frame, setFrame] = useState(1);
  const [fading, setFading] = useState(false);
  const doneRef = useRef(false);

  // 预加载所有帧
  useEffect(() => {
    for (let i = 1; i <= 39; i++) {
      const img = new Image();
      img.src = logoFrame(i);
    }
  }, []);

  // 39帧在 2 秒内播完，每帧约 50ms
  useEffect(() => {
    const frameInterval = setInterval(() => {
      setFrame((prev) => {
        if (prev >= 39) {
          return 39;
        }
        return prev + 1;
      });
    }, 50);

    const doneTimer = setTimeout(() => {
      if (doneRef.current) return;
      doneRef.current = true;
      setFading(true);
      setTimeout(() => {
        onComplete();
      }, 300);
    }, 2000);

    return () => {
      clearInterval(frameInterval);
      clearTimeout(doneTimer);
    };
  }, [onComplete]);

  return (
    <div
      className="logo-transition-container"
      style={{
        opacity: fading ? 0 : 1,
        transition: fading ? 'opacity 300ms ease-out' : 'none',
      }}
    >
      <img
        src={logoFrame(frame)}
        alt="Amadeus Logo Animation"
        className="logo-transition-img"
      />
    </div>
  );
};

export default LogoTransition;
