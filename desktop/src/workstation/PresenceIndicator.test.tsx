import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { PresenceIndicator, PRESENCE_LABELS, ThinkingMark, type PresenceState } from './PresenceIndicator';

afterEach(cleanup);

describe('PresenceIndicator', () => {
  it('switches the label in the same render, with no stale or blank state', () => {
    const { rerender } = render(<PresenceIndicator state="connecting" />);
    const order: PresenceState[] = ['listening', 'thinking', 'voicing', 'speaking', 'offline', 'connecting'];
    for (const state of order) {
      rerender(<PresenceIndicator state={state} />);
      const status = screen.getByRole('status');
      expect(status.dataset.presence).toBe(state);
      expect(status.textContent).toBe(PRESENCE_LABELS[state]);
      expect(status.querySelectorAll('.ws2-presence-label')).toHaveLength(1);
    }
  });

  it('carries the small loop only while connecting or voicing', () => {
    const { rerender, container } = render(<PresenceIndicator state="listening" />);
    for (const state of Object.keys(PRESENCE_LABELS) as PresenceState[]) {
      rerender(<PresenceIndicator state={state} />);
      const ticks = container.querySelector('.ws2-presence-ticks');
      expect(Boolean(ticks), state).toBe(state === 'connecting' || state === 'voicing');
      if (ticks) expect(ticks.getAttribute('aria-hidden')).toBe('true');
    }
  });

  it('draws the thinking mark from each sheet and keeps it out of the accessibility tree', () => {
    const { container, rerender } = render(<ThinkingMark />);
    let mark = container.querySelector('.ws2-thinking-mark')!;
    expect(mark.getAttribute('aria-hidden')).toBe('true');
    expect(mark.querySelectorAll('.gear')).toHaveLength(2);
    rerender(<ThinkingMark worldline="beta" />);
    mark = container.querySelector('.ws2-thinking-mark')!;
    expect(mark.getAttribute('aria-hidden')).toBe('true');
    expect(mark.querySelector('.tracer')).not.toBeNull();
    expect(mark.querySelector('.gear')).toBeNull();
  });
});
