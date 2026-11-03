import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createMemoryStore, handleMemory } from '../dev-mock/memory';
import { hm } from './chartFormat';
import { MemorySpace, type MemoryLink } from './MemorySpace';

vi.mock('../design-system/useReducedMotion', () => ({ useReducedMotion: () => true }));

function serve() {
  const store = createMemoryStore({ facts: [], topics: [], preset: 'normal' });
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://mock');
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) as Record<string, unknown> : {};
    const result = handleMemory(url, method, body, store);
    if (!result) throw new Error(`unexpected fetch ${url.pathname}`);
    return new Response(JSON.stringify(result.body), { status: result.status, headers: { 'Content-Type': 'application/json' } });
  }));
}

function mount(link: MemoryLink | null, props: { chatWorldline?: 'steins_gate' | 'beta' } = {}) {
  const onClose = vi.fn();
  const view = render(<MemorySpace sessionId="s" chatWorldline={props.chatWorldline ?? 'steins_gate'} chatIdentityMode="okabe" onClose={onClose} link={link} />);
  return { ...view, onClose };
}
const plateReady = () => waitFor(() => expect(document.querySelectorAll('.p-star').length).toBeGreaterThan(0));
const refIds = () => [...document.querySelectorAll('[data-testid="plate-ref"]')].map((el) => el.getAttribute('data-ref-id'));

beforeEach(() => localStorage.clear());
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('MemorySpace opened from a chat mark', () => {
  it('without a link nothing is selected, marked or hinted', async () => {
    serve();
    mount(null);
    await plateReady();
    expect(screen.queryByTestId('plate-reticle')).toBeNull();
    expect(refIds()).toEqual([]);
    expect(screen.queryByTestId('memory-ref-hint')).toBeNull();
  });

  it('a "记下了" link selects the first record like a star click and marks the others', async () => {
    serve();
    mount({ select: ['f12', 'f01', 'e1'], mark: ['f01', 'e1'], hintAt: null });
    expect(await screen.findByTestId('reading-her')).toHaveTextContent('你养了一只叫小黑的猫。');
    expect(screen.getByTestId('plate-reticle')).toBeInTheDocument();
    expect(refIds()).toEqual(['f01', 'e1']);
    expect(screen.queryByTestId('memory-ref-hint')).toBeNull();
  });

  it('selects the first record that is on the plate when the first is gone', async () => {
    serve();
    mount({ select: ['gone', 'f12'], mark: [], hintAt: null });
    expect(await screen.findByTestId('reading-her')).toHaveTextContent('你养了一只叫小黑的猫。');
  });

  it('a "参照" link marks the found records, selects nothing, and counts only what it finds', async () => {
    serve();
    const hintAt = new Date(2026, 10, 3, 21, 40).getTime();
    mount({ select: [], mark: ['f12', 'f01', 'not-on-the-plate'], hintAt });
    await plateReady();
    await waitFor(() => expect(refIds()).toEqual(['f12', 'f01']));
    expect(screen.queryByTestId('plate-reticle')).toBeNull();
    expect(screen.queryByTestId('reading-her')).toBeNull();
    expect(screen.getByTestId('memory-ref-hint')).toHaveTextContent(`这一轮参照了 2 条 · ${hm(hintAt)}清除`);
    expect(document.querySelector('[data-testid="plate-ref"] text')).toHaveTextContent('本轮参照');
  });

  it('［清除］removes the reticles and the hint', async () => {
    serve();
    mount({ select: [], mark: ['f12'], hintAt: Date.now() });
    await waitFor(() => expect(refIds()).toEqual(['f12']));
    fireEvent.click(screen.getByTestId('memory-ref-clear'));
    expect(refIds()).toEqual([]);
    expect(screen.queryByTestId('memory-ref-hint')).toBeNull();
  });

  it('when none of the referenced records are on the plate the hint says so and still clears', async () => {
    serve();
    mount({ select: [], mark: ['gone-1', 'gone-2'], hintAt: Date.now() });
    await plateReady();
    expect(await screen.findByTestId('memory-ref-hint')).toHaveTextContent('这一轮参照的记忆已不在星图上');
    expect(refIds()).toEqual([]);
    fireEvent.click(screen.getByTestId('memory-ref-clear'));
    expect(screen.queryByTestId('memory-ref-hint')).toBeNull();
  });

  it('Esc steps back record → topic → overview → out of the space', async () => {
    serve();
    const { onClose } = mount({ select: ['f12'], mark: [], hintAt: null });
    await screen.findByTestId('reading-her');
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.getByTestId('reading-dom')).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByTestId('reading-dom')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('browsing another scope leaves the link behind', async () => {
    serve();
    mount({ select: [], mark: ['f12'], hintAt: Date.now() });
    await waitFor(() => expect(refIds()).toEqual(['f12']));
    fireEvent.click(screen.getByTestId('memory-scope'));
    fireEvent.click(screen.getByRole('option', { name: /OKABE · β/ }));
    await waitFor(() => expect(refIds()).toEqual([]));
    expect(screen.queryByTestId('memory-ref-hint')).toBeNull();
  });
});
