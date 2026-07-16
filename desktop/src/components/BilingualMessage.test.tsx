import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { BilingualMessage } from './BilingualMessage';


const segments = [
  { id: 0, ja: '日本語一。', zh: '中文第一段。' },
  { id: 1, ja: '日本語二。', zh: '中文第二段。' },
];


describe('BilingualMessage', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('accelerates with backlog and click-reveals the current segment', () => {
    vi.useFakeTimers();
    vi.stubGlobal('matchMedia', () => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    render(<BilingualMessage segments={segments} streaming />);

    act(() => vi.advanceTimersByTime(28));
    expect(screen.getByText('中文第')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Reveal current response segment'));
    expect(screen.getByText('中文第一段。')).toBeInTheDocument();
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
    expect(screen.getByText('中文第一段。中文第二段。')).toBeInTheDocument();

    rerender(<BilingualMessage segments={segments} streaming />);
    expect(screen.getByText('日本語一。日本語二。')).toBeInTheDocument();
  });
});
