import React, { useState, useEffect } from 'react';

interface LoadingLogoProps {
  className?: string;
  style?: React.CSSProperties;
}

export const LoadingLogo: React.FC<LoadingLogoProps> = ({ className = '', style }) => {
  const [frame, setFrame] = useState(1);

  // 38 帧动画，24 帧每秒 (41ms 一帧)，播到38帧停住防止多余尾帧闪烁
  useEffect(() => {
    const interval = setInterval(() => {
      setFrame((prev) => (prev >= 38 ? 38 : prev + 1));
    }, 41);

    return () => clearInterval(interval);
  }, []);

  const imageSrc = `/assets/images/logo${frame}.png`;

  return (
    <div className={`relative flex items-center justify-center ${className}`} style={style}>
      <img
        src={imageSrc}
        alt="Amadeus Logo Ring Loading"
        className="w-full h-full object-contain"
        style={{
          filter: 'drop-shadow(0 0 10px rgba(255, 108, 0, 0.75))',
          imageRendering: 'pixelated'
        }}
        onError={(e) => {
          // 兜底默认图
          e.currentTarget.src = '/assets/images/ic_launcher.png';
        }}
      />
    </div>
  );
};
