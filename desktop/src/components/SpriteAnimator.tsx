import React, { useState, useEffect, useRef } from 'react';

interface SpriteAnimatorProps {
  // Mode A — Sprite Sheet
  spriteSrc?: string;
  cols?: number;
  rows?: number;
  // Mode B — Sequence Frames
  framePrefix?: string;
  frameSuffix?: string;
  frameCount?: number;
  framePadding?: number;
  // Mode — Pixel-based sprite sheet (mutually exclusive with percentage mode)
  frameWidth?: number;
  frameHeight?: number;
  // Common
  fps?: number;
  loop?: boolean;
  autoplay?: boolean;
  onComplete?: () => void;
  className?: string;
  style?: React.CSSProperties;
  alt?: string;
}

const SpriteAnimator: React.FC<SpriteAnimatorProps> = ({
  spriteSrc,
  frameWidth,
  frameHeight,
  cols = 1,
  rows = 1,
  framePrefix,
  frameSuffix = '.png',
  frameCount = 0,
  framePadding = 0,
  fps = 12,
  loop = false,
  autoplay = true,
  onComplete,
  className,
  style,
  alt = '',
}) => {
  const isSpriteSheet = Boolean(spriteSrc);
  const totalFrames = isSpriteSheet ? cols * rows : frameCount;

  const [currentFrame, setCurrentFrame] = useState(0);
  const [loadedFrames, setLoadedFrames] = useState<HTMLImageElement[]>([]);
  const completedRef = useRef(false);
  const onCompleteRef = useRef(onComplete);
  onCompleteRef.current = onComplete;

  // Preload frames for Mode B
  useEffect(() => {
    if (isSpriteSheet || frameCount <= 0) return;
    const images: HTMLImageElement[] = [];
    const total = frameCount;

    for (let i = 0; i < total; i++) {
      const img = new Image();
      const frameNum = framePadding > 0
        ? String(i + 1).padStart(framePadding, '0')
        : String(i + 1);
      const src = `${framePrefix}${frameNum}${frameSuffix}`;
      img.src = src;
      images.push(img);
    }
    setLoadedFrames(images);

    return () => {
      images.forEach((img) => {
        img.onload = null;
        img.onerror = null;
      });
    };
  }, [isSpriteSheet, framePrefix, frameSuffix, frameCount, framePadding]);

  // Animation timer
  useEffect(() => {
    if (!autoplay) return;
    if (totalFrames <= 1) return;

    const intervalMs = 1000 / fps;
    const timer = setInterval(() => {
      setCurrentFrame((prev) => {
        const next = prev + 1;
        if (next >= totalFrames) {
          if (!loop && !completedRef.current) {
            completedRef.current = true;
            onCompleteRef.current?.();
            return prev;
          }
          return 0;
        }
        return next;
      });
    }, intervalMs);

    return () => clearInterval(timer);
  }, [autoplay, fps, totalFrames, loop]);

  // Mode A: Sprite sheet via background-position
  if (isSpriteSheet) {
    const usePixelMode = frameWidth != null && frameHeight != null;

    if (totalFrames <= 1) {
      return (
        <div
          className={className}
          style={{
            backgroundImage: `url(${spriteSrc})`,
            backgroundRepeat: 'no-repeat',
            backgroundSize: usePixelMode ? 'auto' : 'contain',
            backgroundPosition: 'center',
            width: usePixelMode ? frameWidth : undefined,
            height: usePixelMode ? frameHeight : undefined,
            ...style,
          }}
          role="img"
          aria-label={alt}
        />
      );
    }

    if (usePixelMode) {
      const col = currentFrame % cols;
      const row = Math.floor(currentFrame / cols);
      return (
        <div
          className={className}
          style={{
            backgroundImage: `url(${spriteSrc})`,
            backgroundRepeat: 'no-repeat',
            backgroundSize: 'auto',
            backgroundPosition: `${-col * frameWidth}px ${-row * frameHeight}px`,
            width: frameWidth,
            height: frameHeight,
            ...style,
          }}
          role="img"
          aria-label={alt}
        />
      );
    }

    const frameWPercent = 100 / (cols - 1);
    const frameHPercent = 100 / (rows - 1);
    const col = currentFrame % cols;
    const row = Math.floor(currentFrame / cols);

    return (
      <div
        className={className}
        style={{
          backgroundImage: `url(${spriteSrc})`,
          backgroundRepeat: 'no-repeat',
          backgroundSize: `${cols * 100}% ${rows * 100}%`,
          backgroundPosition: `${col * frameWPercent}% ${row * frameHPercent}%`,
          ...style,
        }}
        role="img"
        aria-label={alt}
      />
    );
  }

  // Mode B: Sequence frames via img src
  const currentSrc = loadedFrames.length > 0 && loadedFrames[currentFrame]
    ? loadedFrames[currentFrame].src
    : framePrefix
      ? `${framePrefix}${String(currentFrame + 1)}${frameSuffix}`
      : undefined;

  return (
    <img
      className={className}
      src={currentSrc}
      alt={alt}
      style={style}
    />
  );
};

export default SpriteAnimator;
