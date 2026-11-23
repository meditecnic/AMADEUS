export type AmadeusEmotion =
  | 'neutral'
  | 'tsundere'
  | 'embarrassed'
  | 'intellectual'
  | 'happy'
  | 'surprised'
  | 'annoyed'
  | 'disappointed'
  | 'sad';

export const ALLOWED_EMOTIONS: ReadonlySet<string> = new Set<AmadeusEmotion>([
  'neutral',
  'tsundere',
  'embarrassed',
  'intellectual',
  'happy',
  'surprised',
  'annoyed',
  'disappointed',
  'sad',
]);

const LEADING_EMO_TAG = /^\[(?:E?MO)[:=]([^\]\r\n]{1,24})\]/i;

export function parseLeadingEmotion(content: string): AmadeusEmotion | undefined {
  const match = content.trimStart().match(LEADING_EMO_TAG);
  if (!match) return undefined;
  const tag = match[1].toLowerCase();
  return ALLOWED_EMOTIONS.has(tag) ? (tag as AmadeusEmotion) : undefined;
}

export function stripHistoryEmotionTags(content: string): string {
  return content.replace(/[ \t]*(?:\[EMO:[^\]\r\n]{1,24}\][ \t]*)+/gi,
    (tags) => tags.replace(/\[EMO:[^\]\r\n]{1,24}\]/gi, '') ? ' ' : '').trimStart();
}

export function resolveConversationEmotion(
  wireEmotion: unknown,
  history?: ReadonlyArray<{ role: 'user' | 'assistant'; emotion?: string; systemError?: boolean }>,
): AmadeusEmotion {
  if (typeof wireEmotion === 'string' && ALLOWED_EMOTIONS.has(wireEmotion)) {
    return wireEmotion as AmadeusEmotion;
  }
  if (history) {
    for (let i = history.length - 1; i >= 0; i -= 1) {
      const message = history[i];
      if (message.role !== 'assistant' || message.systemError) continue;
      if (message.emotion && ALLOWED_EMOTIONS.has(message.emotion)) {
        return message.emotion as AmadeusEmotion;
      }
      return 'neutral';
    }
  }
  return 'neutral';
}
