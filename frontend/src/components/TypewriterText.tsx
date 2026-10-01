import React, { useState, useEffect } from 'react';
import { audioPlayer } from '../hooks/useAudioPlayer';

interface TypewriterTextProps {
  text: string;
  speed?: number; // 毫秒/字
  onComplete?: () => void;
}

// 在打字时发出清脆、短促的电子哔哔反馈音，使用 Web Audio 振荡器内存合成
const playTypewriterBeep = () => {
  try {
    audioPlayer.init();
    const audioCtx = audioPlayer.ctx;
    if (!audioCtx || audioCtx.state === 'suspended') return;
    
    const oscillator = audioCtx.createOscillator();
    const gainNode = audioCtx.createGain();
    
    oscillator.type = 'sine';
    oscillator.frequency.setValueAtTime(850, audioCtx.currentTime); // 850Hz 科技感音高
    
    gainNode.gain.setValueAtTime(0.012, audioCtx.currentTime); // 音量必须极微弱以防刺耳
    gainNode.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.035); // 快速淡出
    
    oscillator.connect(gainNode);
    gainNode.connect(audioCtx.destination);
    
    oscillator.start();
    oscillator.stop(audioCtx.currentTime + 0.04);
  } catch (e) {}
};

export const TypewriterText: React.FC<TypewriterTextProps> = ({
  text,
  speed = 35,
  onComplete
}) => {
  const [displayedText, setDisplayedText] = useState('');

  useEffect(() => {
    setDisplayedText('');
    if (!text) return;

    let index = 0;
    const interval = setInterval(() => {
      // 逐步增加字符
      setDisplayedText((prev) => prev + text.charAt(index));
      playTypewriterBeep();
      index++;
      
      if (index >= text.length) {
        clearInterval(interval);
        if (onComplete) onComplete();
      }
    }, speed);

    return () => clearInterval(interval);
  }, [text, speed]);

  return <span className="cursor-blink">{displayedText}</span>;
};
