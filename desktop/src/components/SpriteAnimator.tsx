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
  /** Vertical correction applied to rows after the first one.
   * Some legacy sprite sheets were exported with inconsistent transparent
   * padding between rows; this keeps the artwork anchored while preserving
   * the original frame timing.
   */
  frameRowOffsetY?: number;
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
  frameRowOffsetY = 0,
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
  const totalFrames = isSpriteSheet ? (frameCount || cols * rows) : frameCount;

  const [currentFrame, setCurrentFrame] = useState(0);
  const [loadedFrames, setLoadedFrames] = useState<HTMLImageElement[]>([]);
  const completedRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
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
    completedRef.current = false;
    setCurrentFrame(0);
    if (!autoplay || totalFrames <= 1) return;

    const intervalMs = 1000 / fps;
    timerRef.current = setInterval(() => {
      setCurrentFrame((prev) => {
        const next = prev + 1;
        if (next >= totalFrames) {
          return 0;
        }
        return next;
      });
    }, intervalMs);

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      timerRef.current = null;
    };
  }, [autoplay, fps, totalFrames, loop]);

  // Keep completion outside the state updater. React may invoke updater
  // functions more than once in StrictMode, which used to make the splash
  // callback race the final frame and look like a second pass.
  useEffect(() => {
    if (loop || !autoplay || totalFrames <= 1 || currentFrame !== totalFrames - 1) return;
    if (completedRef.current) return;
    completedRef.current = true;
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
    onCompleteRef.current?.();
  }, [autoplay, currentFrame, loop, totalFrames]);

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
      const yOffset = row > 0 ? frameRowOffsetY : 0;
      return (
        <div
          className={className}
          style={{
            width: frameWidth,
            height: frameHeight,
            overflow: 'hidden',
            ...style,
          }}
          role="img"
          aria-label={alt}
        >
          <div
            aria-hidden="true"
            style={{
              width: frameWidth,
              height: frameHeight,
              backgroundImage: `url(${spriteSrc})`,
              backgroundRepeat: 'no-repeat',
              backgroundSize: 'auto',
              backgroundPosition: `${-col * frameWidth}px ${-row * frameHeight}px`,
              transform: yOffset ? `translateY(${yOffset}px)` : undefined,
            }}
          />
        </div>
      );
    }

    // A single-column/row sheet is valid too; avoid Infinity in CSS
    // background-position when there is no axis to interpolate.
    const frameWPercent = cols > 1 ? 100 / (cols - 1) : 0;
    const frameHPercent = rows > 1 ? 100 / (rows - 1) : 0;
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
