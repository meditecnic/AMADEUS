// Backend chart times are ISO 8601 with up to microseconds, either "Z" or "+00:00"
// (both appear in the same store); a naive time is UTC, as the backend reads it.
// Date.parse keeps only milliseconds and reads naive times as local, so the chart
// parses them itself.
const ISO = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,9}))?)?(Z|[+-]\d{2}:?\d{2})?$/i;

/** Microseconds since the epoch (exact: well inside 2^53), or null when malformed. */
export function parseMicros(text: unknown): number | null {
  if (typeof text !== 'string') return null;
  const m = ISO.exec(text.trim());
  if (!m) return null;
  const [year, month, day, hour, minute, second] = [m[1], m[2], m[3], m[4], m[5], m[6] ?? '0'].map(Number);
  if (month < 1 || month > 12 || day < 1 || hour > 23 || minute > 59 || second > 59) return null;
  const ms = Date.UTC(year, month - 1, day, hour, minute, second);
  if (new Date(ms).getUTCDate() !== day) return null;
  let offsetMin = 0;
  const zone = m[8];
  if (zone && zone.toUpperCase() !== 'Z') {
    const digits = zone.replace(':', '');
    const hours = Number(digits.slice(1, 3));
    const minutes = Number(digits.slice(3, 5));
    if (hours > 23 || minutes > 59) return null;
    offsetMin = (hours * 60 + minutes) * (digits[0] === '-' ? -1 : 1);
  }
  const fraction = Number((m[7] ?? '').padEnd(6, '0').slice(0, 6));
  return (ms - offsetMin * 60_000) * 1000 + fraction;
}

/** Milliseconds since the epoch, or NaN when malformed. */
export function parseMs(text: unknown): number {
  const us = parseMicros(text);
  return us === null ? Number.NaN : Math.floor(us / 1000);
}
