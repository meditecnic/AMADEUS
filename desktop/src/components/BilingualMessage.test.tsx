import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { BilingualMessage } from './BilingualMessage';


const segments = [
  { id: 0, ja: '日本語一。', zh: '中文第一段。' },
  { id: 1, ja: '日本語二。', zh: '中文第二段。' },
];


describe('BilingualMessage', () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('shows received segments immediately and keeps each translation with its source', () => {
    vi.useFakeTimers();
    vi.stubGlobal('matchMedia', () => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    const { rerender } = render(<BilingualMessage segments={[segments[0]]} streaming />);
    const first = screen.getByText('中文第一段。').parentElement!;
    expect(within(first).getByText('日本語一。')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();

    rerender(<BilingualMessage segments={[segments[1], segments[0]]} streaming />);
    const second = screen.getByText('中文第二段。').parentElement!;
    expect(within(second).getByText('日本語二。')).toBeInTheDocument();
    expect(within(first).queryByText('日本語二。')).not.toBeInTheDocument();
    expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('keeps the original line visible when translation is degraded', () => {
    render(
      <BilingualMessage
        segments={[{ id: 0, ja: '日本語の原文', zh: '', translationError: 'translation_timeout' }]}
        streaming={false}
      />,
    );

    expect(screen.getByText('日本語の原文')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('中文翻译暂不可用');
  });

  it('renders history statically and honors reduced motion', () => {
    vi.stubGlobal('matchMedia', () => ({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    const { rerender } = render(<BilingualMessage segments={segments} streaming={false} />);
    expect(screen.getByText('中文第一段。')).toBeInTheDocument();
    expect(screen.getByText('中文第二段。')).toBeInTheDocument();

    rerender(<BilingualMessage segments={segments} streaming />);
    expect(screen.getByText('日本語一。')).toBeInTheDocument();
    expect(screen.getByText('日本語二。')).toBeInTheDocument();
  });

  it('marks bilingual assistant Japanese with lang=ja (not class-only inference)', () => {
    render(<BilingualMessage segments={segments} streaming={false} />);
    const ja = screen.getByText('日本語一。');
    expect(ja.tagName).toBe('P');
    expect(ja).toHaveClass('japanese-sub');
    expect(ja).toHaveAttribute('lang', 'ja');
  });

  it('shows a repaired whole-turn translation without attributing it to one sentence', () => {
    render(<BilingualMessage segments={segments} streaming={false} fallbackZh="整段修复译文。" translationScope="turn" />);
    expect(screen.getByText('整段修复译文。')).toBeInTheDocument();
    expect(screen.getByText('整段译文')).toBeInTheDocument();
    expect(screen.getByText('日本語一。日本語二。')).toHaveAttribute('lang', 'ja');
    expect(screen.queryByText('中文第一段。')).not.toBeInTheDocument();
  });

  it('marks assistant raw-Japanese fallback with lang=ja', () => {
    render(
      <BilingualMessage
        segments={[]}
        streaming={false}
        fallbackJa="これは原文フォールバックです。"
        fallbackZh=""
      />,
    );
    const ja = screen.getByText('これは原文フォールバックです。');
    expect(ja).toHaveClass('japanese-raw');
    expect(ja).toHaveAttribute('lang', 'ja');
  });
});
