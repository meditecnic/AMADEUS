import React, { useState, useEffect } from 'react';

// 情绪所对应的立绘前缀映射
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
  }
};

interface AvatarViewerProps {
  emotion: 'neutral' | 'tsundere' | 'embarrassed' | 'intellectual';
  isSpeaking: boolean;
  volume: number;
  glitchState: 'none' | 'light' | 'heavy';
}

export const AvatarViewer: React.FC<AvatarViewerProps> = ({
  emotion,
  isSpeaking,
  volume,
  glitchState
}) => {
  const [isBlinking, setIsBlinking] = useState(false);
  const config = EMOTION_MAP[emotion] || EMOTION_MAP.neutral;

  // 定时器控制随机眨眼效果（每 4~7 秒眨眼一次，闭眼持续 150 毫秒）
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

  // 根据实时音量振幅 (Analyser RMS 范围) 映射口型等级
  let mouthLevel = 1;
  if (isSpeaking && volume > 0.05) {
    if (volume > 0.25) {
      mouthLevel = 3; // 张大嘴
    } else {
      mouthLevel = 2; // 半开嘴
    }
  }

  // 拼装 CSS class
  let glitchClass = '';
  if (glitchState === 'heavy') glitchClass = 'glitch-heavy';
  else if (glitchState === 'light') glitchClass = 'glitch-light';

  // 决定当前立绘的可见性（防闪烁设计：绝对定位重叠，控制 opacity）
  const showBlink = isBlinking && !isSpeaking;
  const opacity1 = (!showBlink && mouthLevel === 1) ? 1 : 0;
  const opacity2 = (!showBlink && mouthLevel === 2) ? 1 : 0;
  const opacity3 = (!showBlink && mouthLevel === 3) ? 1 : 0;
  const opacityBlink = showBlink ? 1 : 0;

  // 四张差分图的文件路径
  const img1 = `${config.base}1.png`;
  const img2 = `${config.base}2.png`;
  const img3 = `${config.base}3.png`;
  const imgBlink = config.blink;

  return (
    <div 
      className={`relative w-full overflow-hidden select-none`}
      style={{
        height: '62vh',
        maxHeight: '520px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center'
      }}
    >
      {/* 背景科技风发光圈 */}
      <div 
        className="absolute rounded-full animate-pulse" 
        style={{
          width: '320px',
          height: '320px',
          background: 'radial-gradient(circle, rgba(255,108,0,0.14) 0%, rgba(0,0,0,0) 70%)',
          zIndex: 1
        }}
      />
      
      {/* 人物立绘容器，应用呼吸特效与 Glitch 特效类 */}
      <div 
        className={`relative h-full w-full flex items-center justify-center breath-animation ${glitchClass}`}
        style={{ zIndex: 2 }}
      >
        {/* 闭嘴/静止态立绘 */}
        <img
          src={img1}
          alt="Kurisu Calm"
          style={{
            height: '100%',
            width: 'auto',
            objectFit: 'contain',
            position: 'absolute',
            opacity: opacity1,
            filter: 'drop-shadow(0 0 12px rgba(255, 108, 0, 0.4))'
          }}
          onError={(e) => {
            e.currentTarget.src = '/assets/images/kurisu_normal1.png';
          }}
        />

        {/* 半开嘴态立绘 */}
        <img
          src={img2}
          alt="Kurisu Talking Half"
          style={{
            height: '100%',
            width: 'auto',
            objectFit: 'contain',
            position: 'absolute',
            opacity: opacity2,
            filter: 'drop-shadow(0 0 12px rgba(255, 108, 0, 0.4))'
          }}
          onError={(e) => {
            e.currentTarget.src = '/assets/images/kurisu_normal2.png';
          }}
        />

        {/* 张大嘴态立绘 */}
        <img
          src={img3}
          alt="Kurisu Talking Open"
          style={{
            height: '100%',
            width: 'auto',
            objectFit: 'contain',
            position: 'absolute',
            opacity: opacity3,
            filter: 'drop-shadow(0 0 12px rgba(255, 108, 0, 0.4))'
          }}
          onError={(e) => {
            e.currentTarget.src = '/assets/images/kurisu_normal3.png';
          }}
        />

        {/* 闭眼/眨眼态立绘 */}
        <img
          src={imgBlink}
          alt="Kurisu Blink"
          style={{
            height: '100%',
            width: 'auto',
            objectFit: 'contain',
            position: 'absolute',
            opacity: opacityBlink,
            filter: 'drop-shadow(0 0 12px rgba(255, 108, 0, 0.4))'
          }}
          onError={(e) => {
            e.currentTarget.src = '/assets/images/kurisu_eyes_closed1.png';
          }}
        />
      </div>
    </div>
  );
};
