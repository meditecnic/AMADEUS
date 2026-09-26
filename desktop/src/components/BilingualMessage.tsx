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
}

export function BilingualMessage({
  segments,
  streaming,
  fallbackJa = '',
  fallbackZh = '',
  translationScope,
}: BilingualMessageProps) {
  const ordered = [...segments].sort((a, b) => a.id - b.id);
  const wholeTurn = translationScope === 'turn';
  // History and whole-turn repairs have no reliable sentence alignment.
  const rows = ordered.length && !wholeTurn
    ? ordered
    : [{
      id: 0,
      ja: ordered.length ? ordered.map(segment => segment.ja).join('') : fallbackJa,
      zh: fallbackZh,
    }];

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
