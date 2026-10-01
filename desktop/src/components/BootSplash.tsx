import React, { useEffect, useRef, useState } from 'react';
import './BootSplash.css';
import { packAsset } from '../assetPack';

interface BootSplashProps {
  onDone: () => void;
}

const FRAME_SIZE = 768;
const SHEET_COLUMNS = 5;

/**
 * The original entrance is split across three sprite sheets.  The first two
 * are complete 5x5 acts; the third contains ten closing-ring frames followed
 * by the title card.  Keeping the sheets separate avoids resampling the
 * source artwork and lets the title frame receive its own cinematic hold.
 */
const BOOT_SEGMENTS = [
  { src: 'assets/boot/boot_ring_1.png', frameStart: 0, frameCount: 25, fps: 24 },
  { src: 'assets/boot/boot_ring_2.png', frameStart: 0, frameCount: 25, fps: 24 },
  { src: 'assets/boot/boot_ring_3.png', frameStart: 5, frameCount: 6, fps: 16 },
] as const;

// App only mounts the splash once the pack probe succeeded.
const sheet = (src: string) => packAsset(src) ?? '';

const TITLE_HOLD_MS = 700;
const TITLE_TRANSITION_MS = 360;
const PUSH_FORWARD_MS = 1000;

export const BootSplash: React.FC<BootSplashProps> = ({ onDone }) => {
  const [assetsReady, setAssetsReady] = useState(false);
  const [position, setPosition] = useState({ segment: 0, frame: 0 });
  const [isTitleTransition, setIsTitleTransition] = useState(false);
  const [isFinalFrame, setIsFinalFrame] = useState(false);
  const doneRef = useRef(false);
  const onDoneRef = useRef(onDone);
  const animationTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const titleTransitionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const titleHoldTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  onDoneRef.current = onDone;

  // Preload all sheets before the first frame. Local packaged assets should
  // resolve immediately; the timeout keeps the splash from hanging forever
  // if a development server returns a transient image error.
  useEffect(() => {
    let cancelled = false;
    let loaded = 0;
    const fallbackTimer = window.setTimeout(() => {
      if (!cancelled) setAssetsReady(true);
    }, 1500);

    const markLoaded = () => {
      loaded += 1;
      if (loaded === BOOT_SEGMENTS.length && !cancelled) {
        window.clearTimeout(fallbackTimer);
        setAssetsReady(true);
      }
    };

    const images = BOOT_SEGMENTS.map((segment) => {
      const image = new Image();
      image.onload = markLoaded;
      image.onerror = markLoaded;
      image.src = sheet(segment.src);
      return image;
    });

    return () => {
      cancelled = true;
      window.clearTimeout(fallbackTimer);
      images.forEach((image) => {
        image.onload = null;
        image.onerror = null;
      });
    };
  }, []);

  useEffect(() => {
    if (!assetsReady || doneRef.current) return;

    let segmentIndex = 0;
    let frameIndex = 0;

    const clearTimers = () => {
      if (animationTimerRef.current) clearTimeout(animationTimerRef.current);
      if (titleTransitionTimerRef.current) clearTimeout(titleTransitionTimerRef.current);
      if (titleHoldTimerRef.current) clearTimeout(titleHoldTimerRef.current);
      if (pushTimerRef.current) clearTimeout(pushTimerRef.current);
      animationTimerRef.current = null;
      titleTransitionTimerRef.current = null;
      titleHoldTimerRef.current = null;
      pushTimerRef.current = null;
    };

    const scheduleFrame = () => {
      const segment = BOOT_SEGMENTS[segmentIndex];
      animationTimerRef.current = setTimeout(() => {
        // The last ring image should breathe for a moment before the title
        // card resolves over it. A crossfade avoids the hard geometric jump
        // between the round ring and the wide wordmark.
        const isLastRingFrame = segmentIndex === BOOT_SEGMENTS.length - 1
          && frameIndex === segment.frameCount - 2;
        if (isLastRingFrame) {
          setIsTitleTransition(true);
          titleTransitionTimerRef.current = setTimeout(() => {
            if (doneRef.current) return;
            frameIndex += 1;
            setPosition({ segment: segmentIndex, frame: frameIndex });
            setIsTitleTransition(false);
            titleHoldTimerRef.current = setTimeout(() => {
              setIsFinalFrame(true);
              pushTimerRef.current = setTimeout(() => {
                if (doneRef.current) return;
                doneRef.current = true;
                onDoneRef.current();
              }, PUSH_FORWARD_MS);
            }, TITLE_HOLD_MS);
          }, TITLE_TRANSITION_MS);
          return;
        }

        if (frameIndex < segment.frameCount - 1) {
          frameIndex += 1;
          setPosition({ segment: segmentIndex, frame: frameIndex });
          scheduleFrame();
          return;
        }

        if (segmentIndex < BOOT_SEGMENTS.length - 1) {
          segmentIndex += 1;
          frameIndex = 0;
          setPosition({ segment: segmentIndex, frame: frameIndex });
          scheduleFrame();
          return;
        }

        // Keep the title card readable before the slow forward push.
        titleHoldTimerRef.current = setTimeout(() => {
          setIsFinalFrame(true);
          pushTimerRef.current = setTimeout(() => {
            if (doneRef.current) return;
            doneRef.current = true;
            onDoneRef.current();
          }, PUSH_FORWARD_MS);
        }, TITLE_HOLD_MS);
      }, 1000 / segment.fps);
    };

    setPosition({ segment: 0, frame: 0 });
    scheduleFrame();

    return clearTimers;
  }, [assetsReady]);

  const segment = BOOT_SEGMENTS[position.segment];
  const sourceFrame = segment.frameStart + position.frame;
  const column = sourceFrame % SHEET_COLUMNS;
  const row = Math.floor(sourceFrame / SHEET_COLUMNS);
  // The title card is a two-cell wide composition: cells 11 and 12 in the
  // third sheet form one continuous Steins;Gate wordmark. Keep both cells in
  // the viewport instead of treating cell 11 as an independent frame.
  const isTitleFrame = position.segment === BOOT_SEGMENTS.length - 1
    && position.frame === BOOT_SEGMENTS[position.segment].frameCount - 1;
  const showTitleLayer = isTitleFrame || isTitleTransition;
  const titleSegment = BOOT_SEGMENTS[BOOT_SEGMENTS.length - 1];

  return (
    <div className="bootsplash-container">
      <div className={`logo-animation-wrapper${showTitleLayer ? ' is-title-frame' : ''}${isFinalFrame ? ' is-final-frame' : ''}`}>
        <div className="bootsplash-frame-window" role="img" aria-label="Amadeus System Entrance">
          <div
            className={`bootsplash-frame-canvas${isTitleTransition ? ' is-transition-ring' : ''}${isTitleFrame ? ' is-title-frame' : ''}`}
            aria-hidden="true"
            style={{
              backgroundImage: `url(${sheet(segment.src)})`,
              backgroundPosition: `${-column * FRAME_SIZE}px ${-row * FRAME_SIZE}px`,
            }}
          />
          {isTitleTransition && (
            <div
              className="bootsplash-frame-canvas is-title-frame is-transition-title"
              aria-hidden="true"
              style={{
                backgroundImage: `url(${sheet(titleSegment.src)})`,
                backgroundPosition: '0 -1536px',
              }}
            />
          )}
        </div>
      </div>
    </div>
  );
};

export default BootSplash;
