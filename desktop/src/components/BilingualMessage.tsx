import { useEffect, useMemo, useState } from 'react';


export interface BilingualSegment {
  id: number;
  ja: string;
  zh: string;
  translationError?: string;
}

interface RevealCount {
  ja: number;
  zh: number;
}

interface BilingualMessageProps {
  segments: BilingualSegment[];
  streaming: boolean;
  fallbackJa?: string;
  fallbackZh?: string;
}


function useReducedMotion() {
  const [reduced, setReduced] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches,
  );

  useEffect(() => {
    const media = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    if (!media) return;
    const update = () => setReduced(media.matches);
    media.addEventListener?.('change', update);
    return () => media.removeEventListener?.('change', update);
  }, []);
  return reduced;
}


export function BilingualMessage({
  segments,
  streaming,
  fallbackJa = '',
  fallbackZh = '',
}: BilingualMessageProps) {
  const reducedMotion = useReducedMotion();
  const ordered = useMemo(() => [...segments].sort((a, b) => a.id - b.id), [segments]);
  const [revealed, setRevealed] = useState<Record<number, RevealCount>>({});

  useEffect(() => {
    if (!streaming || reducedMotion) {
      setRevealed(Object.fromEntries(ordered.map((segment) => [
        segment.id,
        { ja: segment.ja.length, zh: segment.zh.length },
      ])));
      return;
    }

    const timer = window.setInterval(() => {
      setRevealed((current) => {
        const next = { ...current };
        const activeIndex = ordered.findIndex((segment) => {
          const count = current[segment.id] || { ja: 0, zh: 0 };
          return count.ja < segment.ja.length || count.zh < segment.zh.length;
        });
        if (activeIndex < 0) return current;
        const active = ordered[activeIndex];
        const count = current[active.id] || { ja: 0, zh: 0 };
        const backlog = Math.max(0, ordered.length - activeIndex - 1);
        const step = 1 + Math.min(7, backlog * 2);
        next[active.id] = {
          ja: Math.min(active.ja.length, count.ja + step),
          zh: Math.min(active.zh.length, count.zh + step),
        };
        return next;
      });
    }, 28);
    return () => window.clearInterval(timer);
  }, [ordered, streaming, reducedMotion]);

  const flushCurrent = () => {
    if (!streaming) return;
    setRevealed((current) => {
      const active = ordered.find((segment) => {
        const count = current[segment.id] || { ja: 0, zh: 0 };
        return count.ja < segment.ja.length || count.zh < segment.zh.length;
      });
      if (!active) return current;
      return {
        ...current,
        [active.id]: { ja: active.ja.length, zh: active.zh.length },
      };
    });
  };

  const japanese = ordered.length
    ? ordered.map((segment) => segment.ja.slice(0, revealed[segment.id]?.ja || 0)).join('')
    : fallbackJa;
  const chinese = ordered.length
    ? ordered.map((segment) => segment.zh.slice(0, revealed[segment.id]?.zh || 0)).join('')
    : fallbackZh;
  const hasTranslationError = ordered.some((segment) => segment.translationError);

  return (
    <div
      className={`bilingual-message ${streaming ? 'streaming' : 'static'}`}
      onClick={flushCurrent}
      role={streaming ? 'button' : undefined}
      tabIndex={streaming ? 0 : undefined}
      onKeyDown={(event) => {
        if (streaming && (event.key === 'Enter' || event.key === ' ')) flushCurrent();
      }}
      aria-label={streaming ? 'Reveal current response segment' : undefined}
    >
      {chinese && <p className="chinese-main">{chinese}</p>}
      <p className={chinese ? 'japanese-sub' : 'japanese-raw'}>{japanese}</p>
      {hasTranslationError && (
        <p className="translation-degraded" role="status">
          中文翻译暂不可用，已保留日文原文
        </p>
      )}
    </div>
  );
}
