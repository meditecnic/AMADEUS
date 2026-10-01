import React from 'react';

interface CRTOverlayProps {
  glitchState: 'none' | 'light' | 'heavy';
}

export const CRTOverlay: React.FC<CRTOverlayProps> = ({ glitchState }) => {
  // 根据不同的毛刺状态调整偏移值
  const heavyDx = glitchState === 'heavy' ? 5 : 0;
  const lightDx = glitchState === 'light' ? 2 : 0;

  return (
    <>
      {/* 隐藏的 SVG 滤镜定义，负责底层的通道分离渲染 */}
      <svg className="absolute w-0 h-0 pointer-events-none" style={{ position: 'absolute', width: 0, height: 0 }}>
        <defs>
          {/* 剧烈通道分离 (Tsundere 情绪或报错时触发) */}
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

          {/* 轻微通道分离 (Embarrassed 情绪或连接握手时触发) */}
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

      {/* CRT 物理暗角和阴影，加强复古破败感 */}
      <div 
        className="pointer-events-none absolute inset-0 z-[9999]" 
        style={{
          boxShadow: 'inset 0 0 100px rgba(0, 0, 0, 0.95)',
          background: 'radial-gradient(circle, transparent 55%, rgba(0, 0, 0, 0.4) 100%)'
        }}
      />
    </>
  );
};
