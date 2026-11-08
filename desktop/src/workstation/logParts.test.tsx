import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ErrorText, checkShortLabel } from './logParts';

describe('ErrorText', () => {
  it('sets the SYSTEM ERROR prefix as a plate without changing the line text', () => {
    const { container } = render(<p><ErrorText content="SYSTEM ERROR: 实验通道暂时不可用，请稍后重试。" /></p>);
    expect(container.textContent).toBe('SYSTEM ERROR: 实验通道暂时不可用，请稍后重试。');
    expect(container.querySelector('.ws2-err-tag')?.textContent).toBe('SYSTEM ERROR');
  });

  it('leaves other failure lines as they are', () => {
    const { container } = render(<p><ErrorText content="这一问中断了。" /></p>);
    expect(container.textContent).toBe('这一问中断了。');
    expect(container.querySelector('.ws2-err-tag')).toBeNull();
  });
});

describe('checkShortLabel', () => {
  it('is always part of the full label the check button is named by', () => {
    const full: Record<string, string> = {
      untested: '所选模型尚未测试',
      checking: '正在测试连接…',
      ok: '连接测试已通过',
      failed: '连接测试未通过：密钥无效',
    };
    for (const [status, label] of Object.entries(full)) {
      expect(label).toContain(checkShortLabel(status === 'untested' ? undefined : status));
    }
  });
});
