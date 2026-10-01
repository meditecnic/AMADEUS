import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MemorySelect } from './MemorySelect';

const windowKey = vi.fn();
afterEach(() => { window.removeEventListener('keydown', windowKey); cleanup(); });

it('keeps the keyboard popup inside its worldline and does not send menu keys to Memory Space', async () => {
  if (!HTMLElement.prototype.scrollIntoView) HTMLElement.prototype.scrollIntoView = vi.fn();
  const changed = vi.fn();
  const outsideKey = vi.fn();
  window.addEventListener('keydown', windowKey);
  render(<section className="memory-space" data-worldline="beta" onKeyDown={outsideKey}>
    <MemorySelect aria-label="浏览身份" value="self" onValueChange={changed}>
      <option value="okabe">OKABE</option><option value="self">USER</option>
    </MemorySelect>
  </section>);
  const trigger = screen.getByRole('combobox', { name: '浏览身份' });
  fireEvent.keyDown(trigger, { key: 'ArrowDown' });
  const option = await screen.findByRole('option', { name: 'OKABE' });
  expect(option.closest('.memory-space')).toHaveAttribute('data-worldline', 'beta');
  fireEvent.keyDown(option, { key: 'Enter' });
  expect(changed).toHaveBeenCalledWith('okabe');
  await waitFor(() => expect(trigger).toHaveAttribute('data-state', 'closed'));
  fireEvent.keyDown(trigger, { key: 'ArrowDown' });
  fireEvent.keyDown(await screen.findByRole('option', { name: 'USER' }), { key: 'Escape' });
  await waitFor(() => expect(trigger).toHaveAttribute('data-state', 'closed'));
  expect(outsideKey).not.toHaveBeenCalled();
  expect(windowKey).not.toHaveBeenCalled();
  expect(changed).toHaveBeenCalledTimes(1);
});
