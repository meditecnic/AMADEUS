import React, { useState, useEffect } from 'react';
import './BootSplash.css';

interface BootSplashProps {
  onDone: () => void;
}

export const BootSplash: React.FC<BootSplashProps> = ({ onDone }) => {
  // 帧数从 1 到 39
  const [frame, setFrame] = useState(1);

  // 预加载所有帧，防止播放时白色闪烁
  useEffect(() => {
    for (let i = 1; i <= 39; i++) {
      const img = new Image();
      img.src = `/assets/images/logo${i}.png`;
    }
  }, []);

  // 39帧在 2 秒内播完，每帧约 50ms
  useEffect(() => {
    const frameInterval = setInterval(() => {
      setFrame((prev) => {
        if (prev >= 39) {
          return 1; // 循环播放，直到 2 秒 doneTimer 触发
        }
        return prev + 1;
      });
    }, 50);

    const doneTimer = setTimeout(() => {
      onDone();
    }, 2000);

    return () => {
      clearInterval(frameInterval);
      clearTimeout(doneTimer);
    };
  }, [onDone]);

  return (
    <div className="bootsplash-container">
      <div className="logo-animation-wrapper">
        <img 
          src={`/assets/images/logo${frame}.png`} 
          alt="Amadeus Logo Animation" 
          className="bootsplash-logo"
        />
      </div>
      <div className="bootsplash-text">
        CONNECTING TO AMADEUS SYSTEM...
      </div>
    </div>
  );
};

