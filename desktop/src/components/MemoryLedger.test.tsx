import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  MemoryLedger,
  PROMOTE_ERROR_TEXT,
  PROMOTION_REASON_TEXT,
  worldlineShortLabel,
} from './MemoryLedger';

const LEDGER_FIXTURE = {
  local: [
    {
      id: 7,
      fact_key: 'favorite_drink',
      fact_value: 'コーヒー',
      confidence: 0.9,
      importance: 0.7,
      is_pinned: false,
      created_at: '2026-07-26T00:00:00+00:00',
      promotion_eligible: true,
      promotion_reason: null,
    },
    {
      id: 8,
      fact_key: 'hometown',
      fact_value: '秋葉原の近く',
      confidence: 0.8,
      importance: 0.6,
      is_pinned: false,
      created_at: '2026-07-26T00:00:00+00:00',
      promotion_eligible: false,
      promotion_reason: 'source_evidence_unavailable',
      // Hostile extra fields must never be rendered even if a server ever
      // leaked them; the display model is minimal by contract.
      source_message_ids: [101, 102],
      raw_message_text: '私は秋葉原の近くに住んでいる。',
    },
  ],
  shared: [
    {
      id: 3,
      fact_key: 'hobby',
      fact_value: '読書',
      confidence: 0.9,
      importance: 0.8,
      is_pinned: true,
      created_at: '2026-07-25T00:00:00+00:00',
      origin_worldline: 'beta',
    },
  ],
};

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

function deferredResponse(): { promise: Promise<Response>; resolve: (r: Response) => void } {
  let resolve!: (r: Response) => void;
  const promise = new Promise<Response>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

const BETA_FIXTURE = {
  local: [
    {
      id: 21,
      fact_key: 'beta_only_fact',
      fact_value: '紅茶',
      confidence: 0.9,
      importance: 0.7,
      is_pinned: false,
      created_at: '2026-07-26T00:00:00+00:00',
      promotion_eligible: true,
      promotion_reason: null,
    },
  ],
  shared: [],
};

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe('MemoryLedger (Gate 7A)', () => {
  it('makes zero requests while inactive', () => {
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={false} />);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('renders local and shared scopes with worldline origins', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);

    await waitFor(() => expect(screen.getByTestId('local-fact-7')).toBeTruthy());
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      '/api/memory/facts?session_id=s-1&worldline=steins_gate',
    );
    // Switch to shared tab to see shared facts
    fireEvent.click(screen.getByRole('button', { name: '跨线记忆' }));
    await waitFor(() => expect(screen.getByTestId('shared-fact-3')).toBeTruthy());
    expect(screen.getByText(`来源：${worldlineShortLabel('beta')}`)).toBeTruthy();
    expect(screen.getByText('読書')).toBeTruthy();
  });

  it('enables promotion only for eligible facts and shows the stable reason', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('local-fact-7')).toBeTruthy());

    const buttons = screen.getAllByRole('button', { name: '写入跨线记忆' });
    expect(buttons).toHaveLength(1);
    expect((buttons[0] as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByTestId('reason-8').textContent).toBe(
      PROMOTION_REASON_TEXT.source_evidence_unavailable,
    );
  });

  it('disables re-sharing for a local fact that is already shared', async () => {
    // Display-bug regression: after a promotion the reloaded ledger marks the
    // fact already_shared; the button must stop offering the action.
    const fixture = {
      local: [
        {
          id: 9,
          fact_key: 'favorite_drink',
          fact_value: 'コーヒー',
          confidence: 0.9,
          importance: 0.7,
          is_pinned: false,
          created_at: '2026-07-26T00:00:00+00:00',
          promotion_eligible: true,
          promotion_reason: null,
          already_shared: true,
        },
      ],
      shared: [
        {
          id: 4,
          fact_key: 'favorite_drink',
          fact_value: 'コーヒー',
          confidence: 0.9,
          importance: 0.7,
          is_pinned: false,
          created_at: '2026-07-26T00:00:00+00:00',
          origin_worldline: 'steins_gate',
        },
      ],
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(fixture));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('local-fact-9')).toBeTruthy());

    // In new UI, already-shared facts show '已共享' mark, no promote button
    expect(screen.getByTestId('already-shared-9').textContent).toMatch(/已在跨线记忆|已共享/);
    expect(screen.queryByRole('button', { name: '写入跨线记忆' })).toBeNull();
  });

  it('sends nothing before confirmation and exactly one POST after it', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('local-fact-7')).toBeTruthy());

    fireEvent.click(screen.getAllByRole('button', { name: '写入跨线记忆' })[0]);
    expect(screen.getByRole('dialog', { name: '写入跨线记忆确认' })).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1); // still just the initial GET

    const refreshed = {
      local: LEDGER_FIXTURE.local,
      shared: [
        ...LEDGER_FIXTURE.shared,
        {
          id: 4,
          fact_key: 'favorite_drink',
          fact_value: 'コーヒー',
          confidence: 0.9,
          importance: 0.7,
          is_pinned: false,
          created_at: '2026-07-26T01:00:00+00:00',
          origin_worldline: 'steins_gate',
        },
      ],
    };
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ id: 4, scope: 'shared', changed: true }))
      .mockResolvedValueOnce(jsonResponse(refreshed));

    fireEvent.click(screen.getByRole('button', { name: '确认写入' }));
    // Switch to shared tab to see the promoted fact
    fireEvent.click(screen.getByRole('button', { name: '跨线记忆' }));
    await waitFor(() => expect(screen.getByTestId('shared-fact-4')).toBeTruthy());

    const postCalls = fetchMock.mock.calls.filter(
      (call) => (call[1] as RequestInit | undefined)?.method === 'POST',
    );
    expect(postCalls).toHaveLength(1);
    expect(String(postCalls[0][0])).toContain(
      '/api/memory/facts/7/promote?session_id=s-1&worldline=steins_gate',
    );
    // Success refreshed the list (GET, POST, GET).
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('re-enables eligible share buttons after a successful same-context promotion', async () => {
    // Review P0 regression: the post-success reload bumps the request epoch;
    // isPromoting must still be cleared or every share button stays disabled.
    fetchMock.mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('local-fact-7')).toBeTruthy());

    const refreshed = {
      local: [
        { ...LEDGER_FIXTURE.local[0], already_shared: true },
        {
          id: 10,
          fact_key: 'hobby',
          fact_value: '読書',
          confidence: 0.9,
          importance: 0.6,
          is_pinned: false,
          created_at: '2026-07-26T02:00:00+00:00',
          promotion_eligible: true,
          promotion_reason: null,
          already_shared: false,
        },
      ],
      shared: [
        {
          id: 4,
          fact_key: 'favorite_drink',
          fact_value: 'コーヒー',
          confidence: 0.9,
          importance: 0.7,
          is_pinned: false,
          created_at: '2026-07-26T01:00:00+00:00',
          origin_worldline: 'steins_gate',
        },
      ],
    };
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ id: 4, scope: 'shared', changed: true }))
      .mockResolvedValueOnce(jsonResponse(refreshed));

    fireEvent.click(screen.getAllByRole('button', { name: '写入跨线记忆' })[0]);
    fireEvent.click(screen.getByRole('button', { name: '确认写入' }));
    await waitFor(() => expect(screen.getByTestId('local-fact-10')).toBeTruthy());

    // The just-shared fact is disabled via already_shared — but the other
    // eligible fact must be immediately clickable again.
    const buttons = screen.getAllByRole('button', { name: '写入跨线记忆' });
    const freshButton = buttons.find(
      (button) => button.closest('li')?.getAttribute('data-testid') === 'local-fact-10',
    ) as HTMLButtonElement;
    expect(freshButton.disabled).toBe(false);
    fireEvent.click(freshButton);
    expect(screen.getByRole('dialog', { name: '写入跨线记忆确认' })).toBeTruthy();
  });

  it('keeps the list untouched and shows a stable error when the API rejects', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('local-fact-7')).toBeTruthy());

    fireEvent.click(screen.getAllByRole('button', { name: '写入跨线记忆' })[0]);
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: 'source_evidence_unavailable' }, 409),
    );
    fireEvent.click(screen.getByRole('button', { name: '确认写入' }));

    await waitFor(() =>
      expect(screen.getByTestId('promote-error').textContent).toBe(
        PROMOTE_ERROR_TEXT.source_evidence_unavailable,
      ),
    );
    // No optimistic pollution: still exactly the original shared row.
    fireEvent.click(screen.getByRole('button', { name: '跨线记忆' }));
    await waitFor(() => expect(screen.getByTestId('shared-fact-3')).toBeTruthy());
    expect(screen.queryByTestId('shared-fact-4')).toBeNull();
    // Local facts still intact (switch back to verify)
    fireEvent.click(screen.getByRole('button', { name: '本线记忆' }));
    expect(screen.getByTestId('local-fact-7')).toBeTruthy();
    // Only GET + failed POST happened; no refresh on failure.
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('never renders raw source ids or message text', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE));
    const { container } = render(
      <MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />,
    );
    await waitFor(() => expect(screen.getByTestId('local-fact-7')).toBeTruthy());
    expect(container.textContent).not.toContain('source_message_ids');
    expect(container.textContent).not.toContain('101');
    expect(container.textContent).not.toContain('私は秋葉原の近くに住んでいる。');
  });

  // -------------------------------------------------------------------
  // P0 rework: stale UI / cross-context promotion protection
  // -------------------------------------------------------------------

  it('a) drops a late GET from the previous worldline after switching', async () => {
    const sgLoad = deferredResponse();
    const betaLoad = deferredResponse();
    fetchMock
      .mockReturnValueOnce(sgLoad.promise)
      .mockReturnValueOnce(betaLoad.promise);

    const { rerender } = render(
      <MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />,
    );
    rerender(<MemoryLedger sessionId="s-1" worldline="beta" active={true} />);

    // β answers first; the stale SG payload lands afterwards.
    betaLoad.resolve(jsonResponse(BETA_FIXTURE));
    await waitFor(() => expect(screen.getByTestId('local-fact-21')).toBeTruthy());
    sgLoad.resolve(jsonResponse(LEDGER_FIXTURE));
    await new Promise((r) => setTimeout(r, 0));

    expect(screen.getByTestId('local-fact-21')).toBeTruthy();
    expect(screen.queryByTestId('local-fact-7')).toBeNull(); // SG data must not win
  });

  it('b) closes the confirm sheet on worldline switch and never POSTs across contexts', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE))
      .mockResolvedValueOnce(jsonResponse(BETA_FIXTURE));

    const { rerender } = render(
      <MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />,
    );
    await waitFor(() => expect(screen.getByTestId('local-fact-7')).toBeTruthy());
    fireEvent.click(screen.getAllByRole('button', { name: '写入跨线记忆' })[0]);
    expect(screen.getByRole('dialog', { name: '写入跨线记忆确认' })).toBeTruthy();

    rerender(<MemoryLedger sessionId="s-1" worldline="beta" active={true} />);
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '写入跨线记忆确认' })).toBeNull());

    const postCalls = fetchMock.mock.calls.filter(
      (call) => (call[1] as RequestInit | undefined)?.method === 'POST',
    );
    expect(postCalls).toHaveLength(0);
  });

  it('b2) closes the confirm sheet on session switch as well', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE))
      .mockResolvedValueOnce(jsonResponse(BETA_FIXTURE));

    const { rerender } = render(
      <MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />,
    );
    await waitFor(() => expect(screen.getByTestId('local-fact-7')).toBeTruthy());
    fireEvent.click(screen.getAllByRole('button', { name: '写入跨线记忆' })[0]);
    expect(screen.getByRole('dialog', { name: '写入跨线记忆确认' })).toBeTruthy();

    rerender(<MemoryLedger sessionId="s-2" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '写入跨线记忆确认' })).toBeNull());
    const postCalls = fetchMock.mock.calls.filter(
      (call) => (call[1] as RequestInit | undefined)?.method === 'POST',
    );
    expect(postCalls).toHaveLength(0);
  });

  it('c) a late post-promotion reload from the old worldline cannot overwrite the new list', async () => {
    const staleReload = deferredResponse();
    fetchMock
      .mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE))                     // SG initial GET
      .mockResolvedValueOnce(jsonResponse({ id: 4, scope: 'shared', changed: true })) // POST
      .mockReturnValueOnce(staleReload.promise)                                // SG reload (slow)
      .mockResolvedValueOnce(jsonResponse(BETA_FIXTURE));                      // β GET after switch

    const { rerender } = render(
      <MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />,
    );
    await waitFor(() => expect(screen.getByTestId('local-fact-7')).toBeTruthy());
    fireEvent.click(screen.getAllByRole('button', { name: '写入跨线记忆' })[0]);
    fireEvent.click(screen.getByRole('button', { name: '确认写入' }));
    // Wait until the slow SG reload request is actually in flight.
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    rerender(<MemoryLedger sessionId="s-1" worldline="beta" active={true} />);
    await waitFor(() => expect(screen.getByTestId('local-fact-21')).toBeTruthy());

    staleReload.resolve(jsonResponse(LEDGER_FIXTURE)); // old SG reload lands late
    await new Promise((r) => setTimeout(r, 0));

    expect(screen.getByTestId('local-fact-21')).toBeTruthy();
    expect(screen.queryByTestId('local-fact-7')).toBeNull();
  });

  it('d1) a hanging old-context POST never disables new-context share buttons (late success)', async () => {
    const hangingPost = deferredResponse();
    fetchMock
      .mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE)) // SG GET
      .mockReturnValueOnce(hangingPost.promise)            // SG POST (hangs)
      .mockResolvedValueOnce(jsonResponse(BETA_FIXTURE));  // β GET

    const { rerender } = render(
      <MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />,
    );
    await waitFor(() => expect(screen.getByTestId('local-fact-7')).toBeTruthy());
    fireEvent.click(screen.getAllByRole('button', { name: '写入跨线记忆' })[0]);
    fireEvent.click(screen.getByRole('button', { name: '确认写入' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2)); // POST in flight

    rerender(<MemoryLedger sessionId="s-1" worldline="beta" active={true} />);
    await waitFor(() => expect(screen.getByTestId('local-fact-21')).toBeTruthy());

    // The new context's eligible button must be immediately usable.
    const betaShare = screen.getAllByRole('button', { name: '写入跨线记忆' })[0];
    expect((betaShare as HTMLButtonElement).disabled).toBe(false);

    // The confirmed old POST completes in the background — allowed — but its
    // success must not reload, pollute the list or re-disable anything.
    hangingPost.resolve(jsonResponse({ id: 4, scope: 'shared', changed: true }));
    await new Promise((r) => setTimeout(r, 0));

    expect(fetchMock).toHaveBeenCalledTimes(3); // no reload from the stale POST
    expect(screen.getByTestId('local-fact-21')).toBeTruthy();
    expect(screen.queryByTestId('local-fact-7')).toBeNull();
    expect(screen.queryByTestId('promote-error')).toBeNull();
    expect((betaShare as HTMLButtonElement).disabled).toBe(false);
  });

  it('d3) inactive-tab lifecycle: hanging promote POST + active=false prevents stale state on re-activate', async () => {
    const hangingPost = deferredResponse();
    fetchMock
      .mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE)) // initial GET
      .mockReturnValueOnce(hangingPost.promise)            // POST (hangs)
      .mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE)); // re-activate GET

    const { rerender } = render(
      <MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />,
    );
    await waitFor(() => expect(screen.getByTestId('local-fact-7')).toBeTruthy());

    // Open promote confirm + confirm → POST in flight
    fireEvent.click(screen.getAllByRole('button', { name: '写入跨线记忆' })[0]);
    fireEvent.click(screen.getByRole('button', { name: '确认写入' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    // Go inactive — epoch advances, isPromoting cleared
    rerender(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={false} />);

    // Late POST resolves while inactive
    hangingPost.resolve(jsonResponse({ id: 4, scope: 'shared', changed: true }));
    await new Promise((r) => setTimeout(r, 0));

    // Re-activate — fresh GET, share buttons enabled, no stale error
    rerender(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('local-fact-7')).toBeTruthy());

    const shareBtn = screen.getAllByRole('button', { name: '写入跨线记忆' })[0];
    expect((shareBtn as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByTestId('promote-error')).toBeNull();
  });

  it('d2) a late old-context POST failure never surfaces an error in the new context', async () => {
    const hangingPost = deferredResponse();
    fetchMock
      .mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE)) // SG GET
      .mockReturnValueOnce(hangingPost.promise)            // SG POST (hangs)
      .mockResolvedValueOnce(jsonResponse(BETA_FIXTURE));  // β GET

    const { rerender } = render(
      <MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />,
    );
    await waitFor(() => expect(screen.getByTestId('local-fact-7')).toBeTruthy());
    fireEvent.click(screen.getAllByRole('button', { name: '写入跨线记忆' })[0]);
    fireEvent.click(screen.getByRole('button', { name: '确认写入' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    rerender(<MemoryLedger sessionId="s-1" worldline="beta" active={true} />);
    await waitFor(() => expect(screen.getByTestId('local-fact-21')).toBeTruthy());

    hangingPost.resolve(jsonResponse({ detail: 'stale_fact' }, 409)); // late failure
    await new Promise((r) => setTimeout(r, 0));

    expect(screen.queryByTestId('promote-error')).toBeNull(); // no cross-context error
    expect(screen.getByTestId('local-fact-21')).toBeTruthy();
    const betaShare = screen.getAllByRole('button', { name: '写入跨线记忆' })[0];
    expect((betaShare as HTMLButtonElement).disabled).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});

// ---------------------------------------------------------------------------
// Gate 7B: okabe candidate fact reclassification
// ---------------------------------------------------------------------------

const OKABE_FIXTURE = {
  local: [],
  shared: [],
  okabe: [
    {
      id: 50,
      fact_key: 'favorite_drink',
      fact_value: 'コーヒー',
      confidence: 0.9,
      importance: 0.7,
      is_pinned: false,
      created_at: '2026-07-28T00:00:00Z',
      already_reclassified: false,
    },
    {
      id: 51,
      fact_key: 'hobby',
      fact_value: '読書',
      confidence: 0.8,
      importance: 0.5,
      is_pinned: false,
      created_at: '2026-07-28T00:00:00Z',
      already_reclassified: true,
    },
  ],
};

const OKABE_BETA_FIXTURE = {
  local: [],
  shared: [],
  okabe: [
    {
      id: 60,
      fact_key: 'beta_okabe_fact',
      fact_value: '紅茶',
      confidence: 0.9,
      importance: 0.7,
      is_pinned: false,
      created_at: '2026-07-28T00:00:00Z',
      already_reclassified: false,
    },
  ],
};

describe('MemoryLedger (Gate 7B: okabe candidates)', () => {
  it('T13: renders okabe in drawer with correct label and reclassify button', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(OKABE_FIXTURE));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);

    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());
    // Open drawer to see okabe facts
    fireEvent.click(screen.getByRole('button', { name: /待确认/ }));
    // Section heading
    expect(screen.getByText(/待确认 · 冈部候选/)).toBeTruthy();
    // Only unconfirmed okabe facts shown in drawer
    expect(screen.getByTestId('okabe-fact-50')).toBeTruthy();
    // fact_key and fact_value visible
    expect(screen.getByText('喜欢的饮品')).toBeTruthy();
    expect(screen.getByText('コーヒー')).toBeTruthy();
    // already_reclassified=true (id=51) NOT shown in drawer
    expect(screen.queryByTestId('okabe-fact-51')).toBeNull();
    // Only one reclassify button (for unconfirmed id=50)
    const reclassifyButtons = screen.queryAllByRole('button', { name: '确认为真实信息' });
    expect(reclassifyButtons).toHaveLength(1);
  });

  it('T14: reclassify button opens a confirmation dialog', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(OKABE_FIXTURE));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());

    // Open drawer
    fireEvent.click(screen.getByRole('button', { name: /待确认/ }));
    fireEvent.click(screen.getByRole('button', { name: '确认为真实信息' }));
    expect(screen.getByRole('dialog')).toBeTruthy();
    // Dialog contains mapped fact_key / fact_value
    const dialog = screen.getByRole('dialog');
    expect(dialog.textContent).toContain('喜欢的饮品');
    expect(dialog.textContent).toContain('コーヒー');
  });

  it('T15: cancel confirmation sends no POST request', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(OKABE_FIXTURE));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());

    // Open drawer
    fireEvent.click(screen.getByRole('button', { name: /待确认/ }));
    fireEvent.click(screen.getByRole('button', { name: '确认为真实信息' }));
    expect(screen.getByRole('dialog')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    const postCalls = fetchMock.mock.calls.filter(
      (call) => (call[1] as RequestInit | undefined)?.method === 'POST',
    );
    expect(postCalls).toHaveLength(0);
  });

  it('T16: confirm calls the reclassify API and refreshes the list', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(OKABE_FIXTURE));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());

    // Open drawer
    fireEvent.click(screen.getByRole('button', { name: /待确认/ }));
    fireEvent.click(screen.getByRole('button', { name: '确认为真实信息' }));
    // Mock POST reclassify response + GET refresh
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ id: 50, scope: 'self', changed: true }))
      .mockResolvedValueOnce(jsonResponse({ ...OKABE_FIXTURE, okabe: [] }));

    fireEvent.click(screen.getByRole('button', { name: '确认' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3)); // GET + POST + GET
    const postCalls = fetchMock.mock.calls.filter(
      (call) => (call[1] as RequestInit | undefined)?.method === 'POST',
    );
    expect(postCalls).toHaveLength(1);
    expect(String(postCalls[0][0])).toContain(
      '/api/memory/facts/50/reclassify?session_id=s-1&worldline=steins_gate&confirmed=true',
    );
  });

  it('T17: after success, okabe candidate is refreshed and self section shows new fact', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(OKABE_FIXTURE));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());

    const refreshedSnapshot = {
      local: [
        {
          id: 100,
          fact_key: 'favorite_drink',
          fact_value: 'コーヒー',
          confidence: 0.9,
          importance: 0.7,
          is_pinned: false,
          created_at: '2026-07-28T01:00:00Z',
          promotion_eligible: false,
          promotion_reason: null,
          already_shared: false,
        },
      ],
      shared: [],
      okabe: [],
    };
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ id: 100, scope: 'self', changed: true }))
      .mockResolvedValueOnce(jsonResponse(refreshedSnapshot));

    // Open drawer to access okabe facts
    fireEvent.click(screen.getByRole('button', { name: /待确认/ }));
    fireEvent.click(screen.getByRole('button', { name: '确认为真实信息' }));
    fireEvent.click(screen.getByRole('button', { name: '确认' }));

    // After reclassify, switch back to local tab to see the new fact
    fireEvent.click(screen.getByRole('button', { name: '本线记忆' }));
    await waitFor(() => expect(screen.getByTestId('local-fact-100')).toBeTruthy());
    // okabe section should be gone (empty list)
    expect(screen.queryByTestId('okabe-fact-50')).toBeNull();
  });

  it('T18: API failure shows a stable error without optimistic update', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(OKABE_FIXTURE));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());

    // Open drawer
    fireEvent.click(screen.getByRole('button', { name: /待确认/ }));
    fireEvent.click(screen.getByRole('button', { name: '确认为真实信息' }));
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: 'evidence_chain_broken' }, 409),
    );
    fireEvent.click(screen.getByRole('button', { name: '确认' }));

    await waitFor(() => expect(screen.getByTestId('reclassify-error')).toBeTruthy());
    // List remains untouched — okabe fact 50 still visible
    expect(screen.getByTestId('okabe-fact-50')).toBeTruthy();
    // No refresh GET happened (only GET + failed POST = 2 calls)
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('T19: worldline switch invalidates in-flight reclassify and pending dialog', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(OKABE_FIXTURE))
      .mockResolvedValueOnce(jsonResponse(OKABE_BETA_FIXTURE));

    const { rerender } = render(
      <MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />,
    );
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());

    // Open drawer to see okabe facts
    fireEvent.click(screen.getByRole('button', { name: /待确认/ }));
    fireEvent.click(screen.getByRole('button', { name: '确认为真实信息' }));
    expect(screen.getByRole('dialog')).toBeTruthy();

    // Switch worldline — dialog must close
    rerender(<MemoryLedger sessionId="s-1" worldline="beta" active={true} />);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    // New context shows beta okabe data (in drawer)
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: /待确认/ }));
    expect(screen.getByTestId('okabe-fact-60')).toBeTruthy();
    expect(screen.queryByTestId('okabe-fact-50')).toBeNull();

    // No POST was made (dialog was cancelled by context switch)
    const postCalls = fetchMock.mock.calls.filter(
      (call) => (call[1] as RequestInit | undefined)?.method === 'POST',
    );
    expect(postCalls).toHaveLength(0);
  });

  it('extra: session switch invalidates pending confirmation, in-flight POST, and error', async () => {
    const hangingPost = deferredResponse();
    fetchMock
      .mockResolvedValueOnce(jsonResponse(OKABE_FIXTURE))   // s-1 GET
      .mockReturnValueOnce(hangingPost.promise)              // s-1 POST (hangs)
      .mockResolvedValueOnce(jsonResponse(OKABE_BETA_FIXTURE)); // s-2 GET

    const { rerender } = render(
      <MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />,
    );
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());

    // Open drawer to see okabe facts
    fireEvent.click(screen.getByRole('button', { name: /待确认/ }));
    fireEvent.click(screen.getByRole('button', { name: '确认为真实信息' }));
    fireEvent.click(screen.getByRole('button', { name: '确认' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2)); // POST in flight

    // Switch session — must dismiss dialog and error
    rerender(<MemoryLedger sessionId="s-2" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());
    // Open drawer to verify okabe data
    fireEvent.click(screen.getByRole('button', { name: /待确认/ }));
    expect(screen.getByTestId('okabe-fact-60')).toBeTruthy();

    // Late POST failure must not pollute new session
    hangingPost.resolve(jsonResponse({ detail: 'evidence_chain_broken' }, 409));
    await new Promise((r) => setTimeout(r, 0));

    expect(screen.queryByTestId('reclassify-error')).toBeNull();
    expect(screen.getByTestId('okabe-fact-60')).toBeTruthy();
    expect(screen.queryByTestId('okabe-fact-50')).toBeNull();
  });

  // -----------------------------------------------------------------------
  // Inactive tab lifecycle — Gate7B state must be invalidated
  // -----------------------------------------------------------------------

  it('e) clears okabe confirm dialog on active=false and sends no POST on re-activate', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(OKABE_FIXTURE))   // initial GET
      .mockResolvedValueOnce(jsonResponse(OKABE_FIXTURE));   // re-activate GET

    const { rerender } = render(
      <MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />,
    );
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());

    // Open drawer to access okabe facts
    fireEvent.click(screen.getByRole('button', { name: /待确认/ }));
    // Open confirm dialog
    fireEvent.click(screen.getByRole('button', { name: '确认为真实信息' }));
    expect(screen.getByRole('dialog')).toBeTruthy();

    // Go inactive — dialog must close, epoch must advance
    rerender(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={false} />);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    // Re-activate — fresh GET, no stale dialog
    rerender(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());
    expect(screen.queryByRole('dialog')).toBeNull();

    // No POST was ever sent (only GETs)
    const postCalls = fetchMock.mock.calls.filter(
      (call) => (call[1] as RequestInit | undefined)?.method === 'POST',
    );
    expect(postCalls).toHaveLength(0);
  });

  it('f) hanging reclassify POST + active=false prevents stale error on re-activate', async () => {
    const hangingPost = deferredResponse();
    fetchMock
      .mockResolvedValueOnce(jsonResponse(OKABE_FIXTURE))   // initial GET
      .mockReturnValueOnce(hangingPost.promise)              // POST (hangs)
      .mockResolvedValueOnce(jsonResponse({                  // re-activate GET
        local: [], shared: [], okabe: [
          { id: 70, fact_key: 'new_fact', fact_value: '水', confidence: 0.8,
            importance: 0.5, is_pinned: false, created_at: '2026-07-28T00:00:00Z',
            already_reclassified: false },
        ],
      }));

    const { rerender } = render(
      <MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />,
    );
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());

    // Open drawer to access okabe facts
    fireEvent.click(screen.getByRole('button', { name: /待确认/ }));
    // Open confirm + confirm → POST in flight
    fireEvent.click(screen.getByRole('button', { name: '确认为真实信息' }));
    fireEvent.click(screen.getByRole('button', { name: '确认' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2)); // GET + POST

    // Go inactive — epoch advances, state cleared
    rerender(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={false} />);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    // Late POST resolves with error while inactive
    hangingPost.resolve(jsonResponse({ detail: 'evidence_chain_broken' }, 409));
    await new Promise((r) => setTimeout(r, 0));

    // Re-activate — fresh GET, no stale error
    rerender(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());
    // Open drawer to verify new okabe fact
    fireEvent.click(screen.getByRole('button', { name: /待确认/ }));
    expect(screen.getByTestId('okabe-fact-70')).toBeTruthy();
    expect(screen.queryByTestId('reclassify-error')).toBeNull();
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// UX v3: overlay, section ordering, fact_key mapping, scope badge, note
// ---------------------------------------------------------------------------

const ORDER_FIXTURE_UNCONFIRMED = {
  local: [
    {
      id: 200,
      fact_key: 'hobby',
      fact_value: '読書',
      confidence: 0.9,
      importance: 0.7,
      is_pinned: false,
      created_at: '2026-07-28T00:00:00Z',
      promotion_eligible: true,
      promotion_reason: null,
      already_shared: false,
    },
  ],
  shared: [
    {
      id: 201,
      fact_key: 'age',
      fact_value: '18',
      confidence: 0.9,
      importance: 0.5,
      is_pinned: false,
      created_at: '2026-07-28T00:00:00Z',
      origin_worldline: 'steins_gate' as const,
    },
  ],
  okabe: [
    {
      id: 202,
      fact_key: 'favorite_drink',
      fact_value: 'コーヒー',
      confidence: 0.8,
      importance: 0.6,
      is_pinned: false,
      created_at: '2026-07-28T00:00:00Z',
      already_reclassified: false,
    },
    {
      id: 203,
      fact_key: 'name',
      fact_value: '岡部',
      confidence: 0.9,
      importance: 0.5,
      is_pinned: false,
      created_at: '2026-07-28T00:00:00Z',
      already_reclassified: true,
    },
  ],
};

const ORDER_FIXTURE_CONFIRMED_ONLY = {
  local: [
    {
      id: 300,
      fact_key: 'hobby',
      fact_value: '読書',
      confidence: 0.9,
      importance: 0.7,
      is_pinned: false,
      created_at: '2026-07-28T00:00:00Z',
      promotion_eligible: true,
      promotion_reason: null,
      already_shared: false,
    },
  ],
  shared: [
    {
      id: 301,
      fact_key: 'age',
      fact_value: '18',
      confidence: 0.9,
      importance: 0.5,
      is_pinned: false,
      created_at: '2026-07-28T00:00:00Z',
      origin_worldline: 'steins_gate' as const,
    },
  ],
  okabe: [
    {
      id: 302,
      fact_key: 'name',
      fact_value: '岡部',
      confidence: 0.9,
      importance: 0.5,
      is_pinned: false,
      created_at: '2026-07-28T00:00:00Z',
      already_reclassified: true,
    },
  ],
};

describe('MemoryLedger UX v3', () => {
  it('U1: renders okabe in drawer when unconfirmed candidates exist', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(ORDER_FIXTURE_UNCONFIRMED));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);

    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());
    // Okabe facts are in the drawer — open it
    fireEvent.click(screen.getByRole('button', { name: /待确认/ }));
    expect(screen.getByTestId('okabe-fact-202')).toBeTruthy();
    // Local facts visible on local tab (drawer is open, but local tab is still 'active')
    // In drawer mode, tab content is replaced by drawer content
  });

  it('U2: confirmed okabe facts not shown in drawer when no unconfirmed', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(ORDER_FIXTURE_CONFIRMED_ONLY));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);

    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());
    // No unconfirmed → no 待确认 tab
    expect(screen.queryByRole('button', { name: /待确认/ })).toBeNull();
    // Shared tab works
    fireEvent.click(screen.getByRole('button', { name: '跨线记忆' }));
    expect(screen.getByTestId('shared-fact-301')).toBeTruthy();
  });

  it('U3: confirm overlay renders as overlay not sticky', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE));
    const { container } = render(
      <MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />,
    );
    await waitFor(() => expect(screen.getByTestId('local-fact-7')).toBeTruthy());

    fireEvent.click(screen.getAllByRole('button', { name: '写入跨线记忆' })[0]);
    expect(container.querySelector('.memory-confirm-overlay')).toBeTruthy();
    expect(container.querySelector('.memory-promote-confirm')).toBeNull();
  });

  it('U4: overlay backdrop click cancels promotion', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE));
    const { container } = render(
      <MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />,
    );
    await waitFor(() => expect(screen.getByTestId('local-fact-7')).toBeTruthy());

    fireEvent.click(screen.getAllByRole('button', { name: '写入跨线记忆' })[0]);
    expect(screen.getByRole('dialog', { name: '写入跨线记忆确认' })).toBeTruthy();

    const backdrop = container.querySelector('.memory-confirm-backdrop') as HTMLElement;
    fireEvent.click(backdrop);
    expect(screen.queryByRole('dialog', { name: '写入跨线记忆确认' })).toBeNull();

    const postCalls = fetchMock.mock.calls.filter(
      (call) => (call[1] as RequestInit | undefined)?.method === 'POST',
    );
    expect(postCalls).toHaveLength(0);
  });

  it('U5: fact_key mapping displays readable labels', async () => {
    const fixture = {
      local: [
        {
          id: 400,
          fact_key: 'favorite_drink',
          fact_value: 'コーヒー',
          confidence: 0.9,
          importance: 0.7,
          is_pinned: false,
          created_at: '2026-07-28T00:00:00Z',
          promotion_eligible: true,
          promotion_reason: null,
          already_shared: false,
        },
      ],
      shared: [],
      okabe: [],
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(fixture));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('local-fact-400')).toBeTruthy());
    expect(screen.getByText('喜欢的饮品')).toBeTruthy();
    expect(screen.queryByText('favorite_drink')).toBeNull();
  });

  it('U6: fact_key fallback for unknown keys', async () => {
    const fixture = {
      local: [
        {
          id: 401,
          fact_key: 'my_custom_key',
          fact_value: 'some value',
          confidence: 0.9,
          importance: 0.7,
          is_pinned: false,
          created_at: '2026-07-28T00:00:00Z',
          promotion_eligible: true,
          promotion_reason: null,
          already_shared: false,
        },
      ],
      shared: [],
      okabe: [],
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(fixture));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('local-fact-401')).toBeTruthy());
    // Unknown keys must not fall back to English Title Case.
    expect(screen.getByText('其他')).toBeTruthy();
    expect(screen.queryByText('My Custom Key')).toBeNull();
  });

  it('U7: fact_value boolean display', async () => {
    const fixture = {
      local: [
        {
          id: 402,
          fact_key: 'likes_hamburgers',
          fact_value: 'true',
          confidence: 0.9,
          importance: 0.7,
          is_pinned: false,
          created_at: '2026-07-28T00:00:00Z',
          promotion_eligible: true,
          promotion_reason: null,
          already_shared: false,
        },
      ],
      shared: [],
      okabe: [],
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(fixture));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('local-fact-402')).toBeTruthy());
    expect(screen.getByText('是')).toBeTruthy();
    expect(screen.queryByText('true')).toBeNull();
  });

  it('U8: scope badge shows 来自冈部会话 for okabe in drawer', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(OKABE_FIXTURE));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());
    // Open drawer to see okabe facts
    fireEvent.click(screen.getByRole('button', { name: /待确认/ }));
    expect(screen.getByTestId('okabe-fact-50')).toBeTruthy();
    expect(screen.getAllByText('来自冈部会话').length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText('okabe')).toBeNull();
  });

  it('U9: memory ledger note text is visible', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());
    const note = screen.getByText(/本页修改立即生效/);
    expect(note).toBeTruthy();
  });

  it('U10: backdrop click ignored while reclassify POST in flight', async () => {
    const hangingPost = deferredResponse();
    fetchMock
      .mockResolvedValueOnce(jsonResponse(OKABE_FIXTURE))
      .mockReturnValueOnce(hangingPost.promise);

    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());

    // Open drawer to see okabe facts
    fireEvent.click(screen.getByRole('button', { name: /待确认/ }));
    fireEvent.click(screen.getByRole('button', { name: '确认为真实信息' }));
    fireEvent.click(screen.getByRole('button', { name: '确认' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    // Click backdrop while POST is in flight — must NOT dismiss overlay
    const backdrop = document.querySelector('.memory-confirm-backdrop');
    fireEvent.click(backdrop!);
    expect(screen.getByRole('dialog')).toBeTruthy(); // overlay still visible

    // Clean up
    hangingPost.resolve(jsonResponse({ id: 50, scope: 'self', changed: true }));
  });

  it('U11: backdrop click ignored while promote POST in flight', async () => {
    const hangingPost = deferredResponse();
    fetchMock
      .mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE))
      .mockReturnValueOnce(hangingPost.promise);

    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('local-fact-7')).toBeTruthy());

    fireEvent.click(screen.getAllByRole('button', { name: '写入跨线记忆' })[0]);
    fireEvent.click(screen.getByRole('button', { name: '确认写入' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    // Click backdrop while POST is in flight — must NOT dismiss overlay
    const backdrop = document.querySelector('.memory-confirm-backdrop');
    fireEvent.click(backdrop!);
    expect(screen.getByRole('dialog')).toBeTruthy(); // overlay still visible

    // Clean up
    hangingPost.resolve(jsonResponse({ id: 4, scope: 'shared', changed: true }));
  });
});

// ---------------------------------------------------------------------------
// M1: Tab-based UI restructure
// ---------------------------------------------------------------------------

describe('MemoryLedger M1: Tab UI', () => {
  it('M1-1: renders tab bar with 本线记忆 and 跨线记忆', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());
    expect(screen.getByRole('button', { name: '本线记忆' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '跨线记忆' })).toBeTruthy();
  });

  it('M1-2: switching to 跨线记忆 tab shows shared facts', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());

    // Shared facts not visible on local tab
    expect(screen.queryByTestId('shared-fact-3')).toBeNull();

    // Click shared tab
    fireEvent.click(screen.getByRole('button', { name: '跨线记忆' }));
    expect(screen.getByTestId('shared-fact-3')).toBeTruthy();
    expect(screen.getByText('読書')).toBeTruthy();
  });

  it('M1-3: 待确认 badge shows count of unconfirmed okabe facts', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(ORDER_FIXTURE_UNCONFIRMED));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());
    // ORDER_FIXTURE_UNCONFIRMED has 1 unconfirmed okabe fact (id=202)
    expect(screen.getByText('1')).toBeTruthy(); // badge shows 1
  });

  it('M1-4: clicking 待确认 opens drawer with okabe candidates', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(OKABE_FIXTURE));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());

    // OKABE_FIXTURE has 1 unconfirmed (id=50)
    fireEvent.click(screen.getByRole('button', { name: /待确认/ }));
    expect(screen.getByTestId('okabe-fact-50')).toBeTruthy();
    expect(screen.getByText('来自冈部会话')).toBeTruthy();
  });

  it('M1-5: no badge when all okabe facts are confirmed', async () => {
    const allConfirmed = {
      local: [],
      shared: [],
      okabe: [
        {
          id: 500,
          fact_key: 'name',
          fact_value: '岡部',
          confidence: 0.9,
          importance: 0.5,
          is_pinned: false,
          created_at: '2026-07-28T00:00:00Z',
          already_reclassified: true,
        },
      ],
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(allConfirmed));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());
    // No 待确认 tab when all confirmed
    expect(screen.queryByRole('button', { name: /待确认/ })).toBeNull();
  });

  it('M1-6: empty state shows helpful message', async () => {
    const empty = { local: [], shared: [], okabe: [] };
    fetchMock.mockResolvedValueOnce(jsonResponse(empty));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());
    expect(screen.getByText(/还没有本线记忆/)).toBeTruthy();

    // Switch to shared tab
    fireEvent.click(screen.getByRole('button', { name: '跨线记忆' }));
    expect(screen.getByText(/还没有跨线记忆/)).toBeTruthy();
  });

  it('M1-7: CJK text is readable (no raw okabe/promote/reclassify terms in main path)', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE));
    const { container } = render(
      <MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />,
    );
    await waitFor(() => expect(screen.getByTestId('local-fact-7')).toBeTruthy());
    const text = container.textContent ?? '';
    // Main path should not expose raw English technical terms
    expect(text.toLowerCase()).not.toContain('okabe');
    expect(text.toLowerCase()).not.toContain('promote');
    expect(text.toLowerCase()).not.toContain('reclassify');
  });
});

describe('MemoryLedger M3: dismiss', () => {
  it('M3-1: dismiss button posts and reloads', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(OKABE_FIXTURE))
      .mockResolvedValueOnce(jsonResponse({ id: 50, changed: true, dismissed: true }))
      .mockResolvedValueOnce(
        jsonResponse({
          ...OKABE_FIXTURE,
          okabe: OKABE_FIXTURE.okabe.filter((f: { id: number }) => f.id !== 50),
        }),
      );
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: /待确认/ }));
    fireEvent.click(screen.getByTestId('dismiss-50'));
    await waitFor(() => {
      expect(String(fetchMock.mock.calls[1][0])).toContain('/api/memory/facts/50/dismiss');
    });
    expect(fetchMock.mock.calls[1][1]?.method).toBe('POST');
  });

  it('M3-2: bulk dismiss posts batch endpoint', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(OKABE_FIXTURE))
      .mockResolvedValueOnce(jsonResponse({ dismissed: [50], skipped: [] }))
      .mockResolvedValueOnce(jsonResponse({ local: [], shared: [], okabe: [] }));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: /待确认/ }));
    fireEvent.click(screen.getByTestId('bulk-dismiss'));
    await waitFor(() => {
      expect(String(fetchMock.mock.calls[1][0])).toContain('/api/memory/facts/dismiss-batch');
    });
    const body = JSON.parse(String(fetchMock.mock.calls[1][1]?.body ?? '{}'));
    expect(body.ids).toContain(50);
  });
});

describe('MemoryLedger delete established facts', () => {
  it('DEL-1: local delete confirms then posts delete-local', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE))
      .mockResolvedValueOnce(jsonResponse({ id: 7, changed: true, deleted: true }))
      .mockResolvedValueOnce(
        jsonResponse({
          ...LEDGER_FIXTURE,
          local: LEDGER_FIXTURE.local.filter((f: { id: number }) => f.id !== 7),
        }),
      );
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('local-fact-7')).toBeTruthy());
    fireEvent.click(screen.getByTestId('delete-local-7'));
    expect(screen.getByRole('dialog', { name: '删除记忆确认' })).toBeTruthy();
    fireEvent.click(screen.getByTestId('confirm-delete'));
    await waitFor(() => {
      expect(String(fetchMock.mock.calls[1][0])).toContain('/api/memory/facts/7/delete-local');
    });
  });

  it('DEL-2: shared delete posts shared-facts delete', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE))
      .mockResolvedValueOnce(jsonResponse({ id: 3, changed: true, deleted: true }))
      .mockResolvedValueOnce(
        jsonResponse({
          ...LEDGER_FIXTURE,
          shared: [],
        }),
      );
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('memory-ledger')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: '跨线记忆' }));
    await waitFor(() => expect(screen.getByTestId('shared-fact-3')).toBeTruthy());
    fireEvent.click(screen.getByTestId('delete-shared-3'));
    fireEvent.click(screen.getByTestId('confirm-delete'));
    await waitFor(() => {
      expect(String(fetchMock.mock.calls[1][0])).toContain('/api/memory/shared-facts/3/delete');
    });
  });
});
