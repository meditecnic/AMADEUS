import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { Tip } from './Tip';

afterEach(cleanup);

/** Focus as a Tab press would, so :focus-visible holds (the tip only follows keyboard focus). */
const tabTo = (el: HTMLElement) => {
  fireEvent.keyDown(document.body, { key: 'Tab' });
  el.focus();
  fireEvent.keyUp(el, { key: 'Tab' });
};

const size = (el: HTMLElement, scrollWidth: number, clientWidth: number) => {
  Object.defineProperty(el, 'scrollWidth', { configurable: true, value: scrollWidth });
  Object.defineProperty(el, 'clientWidth', { configurable: true, value: clientWidth });
};

describe('Tip', () => {
  it('replaces title: keyboard focus shows the tip and links it as the description', async () => {
    render(<Tip label="断开连接"><button type="button" aria-label="断开连接">⎋</button></Tip>);
    const button = screen.getByRole('button', { name: '断开连接' });
    expect(button).not.toHaveAttribute('title');
    expect(screen.queryByRole('tooltip')).toBeNull();
    tabTo(button);
    expect(await screen.findByRole('tooltip')).toHaveTextContent('断开连接');
    fireEvent.keyDown(button, { key: 'Escape' });
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('with truncated, repeats the text only when it is actually cut off', async () => {
    render(
      <Tip label="一个很长很长的会话标题" truncated=".t"><button type="button"><span className="t">一个很长很长的会话标题</span></button></Tip>,
    );
    const button = screen.getByRole('button');
    const text = button.querySelector('.t') as HTMLElement;
    size(text, 120, 120);
    tabTo(button);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole('tooltip')).toBeNull();
    button.blur();
    size(text, 260, 120);
    tabTo(button);
    expect(await screen.findByRole('tooltip')).toHaveTextContent('一个很长很长的会话标题');
  });

  it('renders the bare child when there is nothing to say', () => {
    render(<Tip label={undefined}><button type="button">x</button></Tip>);
    expect(screen.getByRole('button')).not.toHaveAttribute('data-state');
  });
});
