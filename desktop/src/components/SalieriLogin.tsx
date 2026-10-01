import React, { useState, useEffect, useRef } from 'react';
import './SalieriLogin.css';
import { audioService } from '../services/AudioService';
import { packAsset } from '../assetPack';
import { audioPlayer } from '../hooks/useAudioPlayer';

interface SalieriLoginProps {
  onLoginSuccess: () => void;
}

export const SalieriLogin: React.FC<SalieriLoginProps> = ({ onLoginSuccess }) => {
  const [userId, setUserId] = useState('');
  const [password, setPassword] = useState('');
  const [errorMsg, setErrorMsg] = useState('');
  const [isAuthenticating, setIsAuthenticating] = useState(false);
  const [scale, setScale] = useState(1);
  const photoLeft = packAsset('login/photo_left.png');
  const amadeusLogo = packAsset('login/amadeus_logo.png');

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const errTimeoutRef = useRef<number | null>(null);

  // 统一错误设置与 3s 自动清除并复原函数
  const showAuthError = (msg: string) => {
    if (errTimeoutRef.current) {
      window.clearTimeout(errTimeoutRef.current);
    }
    setErrorMsg(msg);
    errTimeoutRef.current = window.setTimeout(() => {
      setErrorMsg('');
      errTimeoutRef.current = null;
    }, 3000);
  };

  // 卸载组件时清理定时器，防内存泄漏
  useEffect(() => {
    return () => {
      if (errTimeoutRef.current) {
        window.clearTimeout(errTimeoutRef.current);
      }
    };
  }, []);

  // Play login-screen BGM on mount
  useEffect(() => {
    audioService.playBGM('/audio/bgm/Messenger main theme.ogg');
    return () => {
      audioService.stopBGM();
    };
  }, []);

  // 1. 动态计算 1920x1080 比例下的自适应 scale 值
  useEffect(() => {
    const handleResize = () => {
      const w = window.innerWidth;
      const h = window.innerHeight;
      const scaleFactor = Math.min(w / 1920, h / 1080);
      setScale(scaleFactor);
    };
    
    handleResize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // 2. USER ID 打字机自动录入效果 (基于显式局部变量，强力避免 React StrictMode 漏字与双计时器冲突)
  useEffect(() => {
    let index = 0;
    const targetText = 'Salieri';
    let current = '';
    setUserId(''); // 初始清空

    const typeTimer = setInterval(() => {
      if (index < targetText.length) {
        current += targetText.charAt(index);
        setUserId(current);
        index++;
      } else {
        clearInterval(typeTimer);
      }
    }, 50);

    return () => clearInterval(typeTimer); // 在 unmount 时彻底销毁计时器，规避竞争
  }, []);

  // 3. Canvas 动态 0/1 金黄色数字雨 (150ms 刷新)
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // 设置 Canvas 绘图分辨率
    canvas.width = 1920;
    canvas.height = 1080;

    const fontSize = 14;
    const columns = Math.floor(canvas.width / fontSize);
    
    // 初始化每一列的垂直下落 y 坐标
    const drops: number[] = [];
    for (let i = 0; i < columns; i++) {
      drops[i] = Math.random() * -20;
    }

    const drawBinaryRain = () => {
      ctx.fillStyle = 'rgba(0, 0, 0, 0.15)';
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      ctx.fillStyle = 'rgba(255, 170, 0, 0.58)';
      ctx.font = `${fontSize}px monospace`;

      for (let i = 0; i < drops.length; i++) {
        const text = Math.random() > 0.5 ? '1' : '0';
        const x = i * fontSize;
        const y = drops[i] * fontSize;

        ctx.fillText(text, x, y);

        if (y > canvas.height && Math.random() > 0.975) {
          drops[i] = 0;
        }

        drops[i]++;
      }
    };

    const rainTimer = setInterval(drawBinaryRain, 50);
    return () => clearInterval(rainTimer);
  }, []);

  const handleInitAudio = () => {
    audioPlayer.init();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    handleInitAudio();
    if (!userId) {
      showAuthError('请输入 USER ID');
      return;
    }
    if (userId !== 'Salieri') {
      showAuthError('无效的 USER ID');
      return;
    }
    if (!password) {
      showAuthError('请输入访问密码');
      return;
    }

    setIsAuthenticating(true);
    if (errTimeoutRef.current) {
      window.clearTimeout(errTimeoutRef.current);
      errTimeoutRef.current = null;
    }
    setErrorMsg('');

    try {
      const response = await fetch('/api/auth/salieri', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ password }),
      });

      if (response.ok) {
        audioService.playSFX('/audio/sfx/连接成功，进入页面2.ogg');
        onLoginSuccess();
      } else {
        const errorData = await response.json().catch(() => ({}));
        showAuthError(errorData.detail || '访问被拒绝');
      }
    } catch (error) {
      showAuthError('系统连接已断开');
    } finally {
      setIsAuthenticating(false);
    }
  };

  return (
    <div className="login-screen-container">
      {/* 顶层 CRT 扫描线与暗角效果 */}
      <div className="crt-effects-overlay" />

      <div 
        className="login-content-wrapper" 
        style={{ transform: `scale(${scale})` }}
      >
        {/* 左侧写实黑白照片 (已移入等比例缩放的 wrapper 内部，以实现 100% 精确网格线重合与滤色混合) */}
        {photoLeft && (
          <div className="left-photo-viewport">
            <img src={photoLeft} className="left-photo" alt="System Terminal Background" />
          </div>
        )}

        {/* Canvas 动态二进制数字背景雨 */}
        <canvas ref={canvasRef} className="binary-stream-canvas" />

        <div className="login-form-center">
          <form onSubmit={handleSubmit} className="salieri-login-form">

            <div className="amadeus-logo-container">
              {amadeusLogo ? (
                <img
                  src={amadeusLogo}
                  className="amadeus-logo-img"
                  alt="AMADEUS Logo"
                />
              ) : (
                <span className="amadeus-logo-text">AMADEUS</span>
              )}
            </div>

            <div className="input-field-group user-id-group">
              <label className="field-title" htmlFor="salieri-user">USER ID</label>
              <input
                id="salieri-user"
                type="text"
                className={`user-id-input ${errorMsg.includes('USER ID') ? 'input-error' : ''}`}
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                disabled={isAuthenticating}
                spellCheck={false}
                autoComplete="off"
              />
            </div>

            <div className="input-field-group password-group">
              <label className="field-title" htmlFor="salieri-pwd">PASSWORD</label>
              <div className="password-input-wrapper">
                <input
                  id="salieri-pwd"
                  type="password"
                  className={`password-input ${
                    errorMsg.includes('ACCESS') || errorMsg.includes('CONNECTION') ? 'input-error' : ''
                  }`}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={isAuthenticating}
                  spellCheck={false}
                  autoComplete="off"
                />
              </div>
            </div>

            <div className="btn-container">
              <button
                type="submit"
                className="decrypt-btn"
                disabled={isAuthenticating}
                title="CONNECT"
                aria-label="Decrypt and Connect"
              >
                <svg viewBox="0 0 80 80" width="76" height="76" className="connect-btn-svg">
                  <circle cx="40" cy="40" r="34" className="btn-circle-track" />
                  <circle cx="40" cy="40" r="34" className="btn-circle-glow" />
                  <path d="M 30 40 L 50 40 M 42 32 L 50 40 L 42 48" className="btn-arrow-icon" />
                </svg>
              </button>
            </div>

            {errorMsg && (
              <div className="auth-error-banner glitch-flicker">
                {errorMsg}
              </div>
            )}
          </form>
        </div>
      </div>
    </div>
  );
};
