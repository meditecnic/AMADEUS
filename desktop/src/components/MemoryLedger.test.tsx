import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  MemoryLedger,
  PROMOTE_CONFIRM_TEXT,
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
    expect(screen.getAllByText('本世界线').length).toBe(2);
    expect(screen.getAllByText('SG + β').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(`来源：${worldlineShortLabel('beta')}`)).toBeTruthy();
    expect(screen.getByText('読書')).toBeTruthy();
  });

  it('enables promotion only for eligible facts and shows the stable reason', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('local-fact-7')).toBeTruthy());

    const buttons = screen.getAllByRole('button', { name: '共享至 SG + β' });
    expect(buttons).toHaveLength(2);
    expect((buttons[0] as HTMLButtonElement).disabled).toBe(false);
    expect((buttons[1] as HTMLButtonElement).disabled).toBe(true);
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

    const button = screen.getByRole('button', { name: '共享至 SG + β' });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId('already-shared-9').textContent).toBe('已共享');
    // No confirm sheet can appear for an already shared fact.
    fireEvent.click(button);
    expect(screen.queryByRole('dialog', { name: '共享确认' })).toBeNull();
  });

  it('sends nothing before confirmation and exactly one POST after it', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('local-fact-7')).toBeTruthy());

    fireEvent.click(screen.getAllByRole('button', { name: '共享至 SG + β' })[0]);
    expect(screen.getByRole('dialog', { name: '共享确认' })).toBeTruthy();
    expect(screen.getByText(PROMOTE_CONFIRM_TEXT)).toBeTruthy();
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

    fireEvent.click(screen.getByRole('button', { name: '确认共享' }));
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

    fireEvent.click(screen.getAllByRole('button', { name: '共享至 SG + β' })[0]);
    fireEvent.click(screen.getByRole('button', { name: '确认共享' }));
    await waitFor(() => expect(screen.getByTestId('local-fact-10')).toBeTruthy());

    // The just-shared fact is disabled via already_shared — but the other
    // eligible fact must be immediately clickable again.
    const buttons = screen.getAllByRole('button', { name: '共享至 SG + β' });
    const freshButton = buttons.find(
      (button) => button.closest('li')?.getAttribute('data-testid') === 'local-fact-10',
    ) as HTMLButtonElement;
    expect(freshButton.disabled).toBe(false);
    fireEvent.click(freshButton);
    expect(screen.getByRole('dialog', { name: '共享确认' })).toBeTruthy();
  });

  it('keeps the list untouched and shows a stable error when the API rejects', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(LEDGER_FIXTURE));
    render(<MemoryLedger sessionId="s-1" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.getByTestId('local-fact-7')).toBeTruthy());

    fireEvent.click(screen.getAllByRole('button', { name: '共享至 SG + β' })[0]);
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: 'source_evidence_unavailable' }, 409),
    );
    fireEvent.click(screen.getByRole('button', { name: '确认共享' }));

    await waitFor(() =>
      expect(screen.getByTestId('promote-error').textContent).toBe(
        PROMOTE_ERROR_TEXT.source_evidence_unavailable,
      ),
    );
    // No optimistic pollution: still exactly the original shared row.
    expect(screen.getByTestId('shared-fact-3')).toBeTruthy();
    expect(screen.queryByTestId('shared-fact-4')).toBeNull();
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
    fireEvent.click(screen.getAllByRole('button', { name: '共享至 SG + β' })[0]);
    expect(screen.getByRole('dialog', { name: '共享确认' })).toBeTruthy();

    rerender(<MemoryLedger sessionId="s-1" worldline="beta" active={true} />);
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '共享确认' })).toBeNull());

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
    fireEvent.click(screen.getAllByRole('button', { name: '共享至 SG + β' })[0]);
    expect(screen.getByRole('dialog', { name: '共享确认' })).toBeTruthy();

    rerender(<MemoryLedger sessionId="s-2" worldline="steins_gate" active={true} />);
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '共享确认' })).toBeNull());
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
    fireEvent.click(screen.getAllByRole('button', { name: '共享至 SG + β' })[0]);
    fireEvent.click(screen.getByRole('button', { name: '确认共享' }));
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
    fireEvent.click(screen.getAllByRole('button', { name: '共享至 SG + β' })[0]);
    fireEvent.click(screen.getByRole('button', { name: '确认共享' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2)); // POST in flight

    rerender(<MemoryLedger sessionId="s-1" worldline="beta" active={true} />);
    await waitFor(() => expect(screen.getByTestId('local-fact-21')).toBeTruthy());

    // The new context's eligible button must be immediately usable.
    const betaShare = screen.getAllByRole('button', { name: '共享至 SG + β' })[0];
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
    fireEvent.click(screen.getAllByRole('button', { name: '共享至 SG + β' })[0]);
    fireEvent.click(screen.getByRole('button', { name: '确认共享' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    rerender(<MemoryLedger sessionId="s-1" worldline="beta" active={true} />);
    await waitFor(() => expect(screen.getByTestId('local-fact-21')).toBeTruthy());

    hangingPost.resolve(jsonResponse({ detail: 'stale_fact' }, 409)); // late failure
    await new Promise((r) => setTimeout(r, 0));

    expect(screen.queryByTestId('promote-error')).toBeNull(); // no cross-context error
    expect(screen.getByTestId('local-fact-21')).toBeTruthy();
    const betaShare = screen.getAllByRole('button', { name: '共享至 SG + β' })[0];
    expect((betaShare as HTMLButtonElement).disabled).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
