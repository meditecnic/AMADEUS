/*
 * Small pieces of the observation log (styles in log.css).
 */

const ERROR_PREFIX = 'SYSTEM ERROR: ';

/**
 * A failed turn's line. The "SYSTEM ERROR" prefix the socket hook writes is set as a red plate; the colon is kept for
 * screen readers and copy, so the line's text is unchanged.
 */
export function ErrorText({ content }: { content: string }) {
  if (!content.startsWith(ERROR_PREFIX)) return <>{content}</>;
  return (
    <>
      <span className="ws2-err-tag">SYSTEM ERROR</span>
      <span className="ws2-vh">: </span>
      {content.slice(ERROR_PREFIX.length)}
    </>
  );
}

/**
 * The connection check under the composer, short enough to stay on the line. Each is a part of the full label the
 * button is named by (所选模型尚未测试 / 正在测试连接… / 连接测试已通过 / 连接测试未通过：…).
 */
export function checkShortLabel(status: string | undefined): string {
  if (!status) return '尚未测试';
  if (status === 'checking') return '正在测试';
  if (status === 'ok') return '测试已通过';
  return '测试未通过';
}
