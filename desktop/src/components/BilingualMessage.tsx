import { useState } from 'react';

export interface BilingualSegment {
  id: number;
  ja: string;
  zh: string;
  translationError?: string;
}

interface BilingualMessageProps {
  segments: BilingualSegment[];
  streaming: boolean;
  fallbackJa?: string;
  fallbackZh?: string;
  translationScope?: 'turn';
  /** paragraph: Chinese reads as one flowing paragraph with the Japanese original below; sentences stay linked. */
  layout?: 'pairs' | 'paragraph';
  /** Sentence currently heard (paragraph layout): lit in both languages while her voice plays it. */
  speakingId?: number | null;
}

export function BilingualMessage({
  segments,
  streaming,
  fallbackJa = '',
  fallbackZh = '',
  translationScope,
  layout = 'pairs',
  speakingId = null,
}: BilingualMessageProps) {
  const [hot, setHot] = useState<number | null>(null);
  const ordered = [...segments].sort((a, b) => a.id - b.id);
  const wholeTurn = translationScope === 'turn';
  // History and whole-turn repairs have no reliable sentence alignment.
  const rows = ordered.length && !wholeTurn
    ? ordered
    : [{
      id: 0,
      ja: ordered.length ? ordered.map(segment => segment.ja).join('') : fallbackJa,
      zh: fallbackZh,
    } as BilingualSegment];

  if (layout === 'paragraph') {
    const anyZh = rows.some(segment => segment.zh);
    const degraded = rows.some(segment => segment.translationError);
    const linked = rows.length > 1;
    const bind = (id: number) => linked ? {
      tabIndex: 0,
      className: `s${hot === id ? ' hot' : ''}${speakingId === id ? ' speaking' : ''}`,
      onMouseEnter: () => setHot(id),
      onMouseLeave: () => setHot(null),
      onFocus: () => setHot(id),
      onBlur: () => setHot(null),
    } : { className: 's' };
    return (
      <div className={`bilingual-message paragraph ${streaming ? 'streaming' : 'static'}`}>
        {wholeTurn && <p className="translation-scope">整段译文</p>}
        {anyZh && (
          <p className="chinese-main">
            {rows.map(segment => (
              <span key={segment.id} {...bind(segment.id)}>
                {segment.zh || (streaming && !segment.translationError
                  ? <em className="translation-pending">翻译中</em>
                  : null)}
              </span>
            ))}
          </p>
        )}
        <p className={anyZh ? 'japanese-sub' : 'japanese-raw'} lang="ja">
          {rows.map(segment => <span key={segment.id} {...bind(segment.id)}>{segment.ja}</span>)}
        </p>
        {degraded && (
          <p className="translation-degraded" role="status">
            中文翻译暂不可用，已保留日文原文
          </p>
        )}
      </div>
    );
  }

  return (
    <div className={`bilingual-message ${streaming ? 'streaming' : 'static'}`}>
      {wholeTurn && <p className="translation-scope">整段译文</p>}
      {rows.map(segment => (
        <div className="bilingual-segment" key={segment.id}>
          {segment.zh && <p className="chinese-main">{segment.zh}</p>}
          <p className={segment.zh ? 'japanese-sub' : 'japanese-raw'} lang="ja">
            {segment.ja}
          </p>
          {segment.translationError && (
            <p className="translation-degraded" role="status">
              中文翻译暂不可用，已保留日文原文
            </p>
          )}
        </div>
      ))}
    </div>
  );
}
