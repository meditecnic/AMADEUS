import React, { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Gate 7A Memory Ledger (Settings-only).
 *
 * Shows the current worldline's self-local core facts plus the user's whole
 * shared profile, and lets the user explicitly promote one local fact to the
 * shared (SG + β) scope. The ledger is identity-agnostic UI: it always shows
 * the user's own self facts, never Okabe facts, regardless of which chat
 * conversation is open. The client submits only the fact id — the server
 * re-reads and re-verifies everything from its persistent stores.
 */

export interface LedgerLocalFact {
  id: number;
  fact_key: string;
  fact_value: string;
  confidence: number;
  importance: number;
  is_pinned: boolean;
  created_at: string;
  promotion_eligible: boolean;
  promotion_reason: string | null;
  /** True when re-promoting would be an exact no-op (server-computed). */
  already_shared: boolean;
}

export interface LedgerSharedFact {
  id: number;
  fact_key: string;
  fact_value: string;
  confidence: number;
  importance: number;
  is_pinned: boolean;
  created_at: string;
  origin_worldline: 'steins_gate' | 'beta';
}

export interface LedgerSnapshot {
  local: LedgerLocalFact[];
  shared: LedgerSharedFact[];
}

/** Confirm sheet copy — promotion is cross-worldline and needs explicit consent. */
export const PROMOTE_CONFIRM_TEXT = '此信息会在 SG 与 β 的 self 模式中使用。';

/** Stable ineligibility reasons from the ledger listing. */
export const PROMOTION_REASON_TEXT: Record<string, string> = {
  source_evidence_unavailable: '来源证据已不可用',
  identity_scope_violation: '身份范围不允许共享',
  fact_not_promotable: '暂不可共享',
};

/** Stable error codes from the promote endpoint. */
export const PROMOTE_ERROR_TEXT: Record<string, string> = {
  fact_not_found: '该记忆已不存在，请刷新后重试。',
  fact_not_promotable: '该记忆暂不可共享。',
  source_evidence_unavailable: '来源证据已不可用，无法共享。',
  identity_scope_violation: '身份范围不允许共享。',
  stale_fact: '该记忆已被更新，请刷新后重试。',
};

export const PROMOTE_GENERIC_ERROR = '请求失败，请稍后重试。';

export function worldlineShortLabel(worldline: 'steins_gate' | 'beta'): string {
  return worldline === 'steins_gate' ? 'SG' : 'β';
}

export interface MemoryLedgerProps {
  sessionId: string;
  worldline: 'steins_gate' | 'beta';
  /** Fetch only while the Memory tab is actually visible. */
  active: boolean;
}

/** Confirm state carries its immutable origin context: a promotion prepared
 * in one session/worldline must never execute against another. */
interface PendingPromotion {
  fact: LedgerLocalFact;
  sessionId: string;
  worldline: 'steins_gate' | 'beta';
}

export const MemoryLedger: React.FC<MemoryLedgerProps> = ({ sessionId, worldline, active }) => {
  const [snapshot, setSnapshot] = useState<LedgerSnapshot | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingPromotion | null>(null);
  const [isPromoting, setIsPromoting] = useState(false);
  const [promoteError, setPromoteError] = useState<string | null>(null);
  // Request epoch: any context switch invalidates every in-flight request so
  // a late GET/POST/reload from the old context can never overwrite state.
  const requestEpochRef = useRef(0);
  const contextKey = `${sessionId}::${worldline}`;
  const contextRef = useRef(contextKey);

  if (contextRef.current !== contextKey) {
    // Render-time context change: invalidate synchronously before any
    // effect or late promise resolution can touch state for the old context.
    contextRef.current = contextKey;
    requestEpochRef.current += 1;
  }

  useEffect(() => {
    // State cleanup for a new context (snapshot, errors, confirm sheet).
    // isPromoting is also context state: a hanging old-context POST must
    // never keep the new context's share buttons disabled.
    setSnapshot(null);
    setLoadError(null);
    setPromoteError(null);
    setPending(null);
    setIsPromoting(false);
  }, [contextKey]);

  const loadFacts = useCallback(async () => {
    const epoch = ++requestEpochRef.current;
    setIsLoading(true);
    setLoadError(null);
    try {
      const response = await fetch(
        `/api/memory/facts?session_id=${encodeURIComponent(sessionId)}&worldline=${encodeURIComponent(worldline)}`,
      );
      if (epoch !== requestEpochRef.current) return; // stale context/request
      if (!response.ok) {
        throw new Error(`ledger_http_${response.status}`);
      }
      const body = (await response.json()) as LedgerSnapshot;
      if (epoch !== requestEpochRef.current) return;
      setSnapshot({ local: body.local ?? [], shared: body.shared ?? [] });
    } catch {
      if (epoch === requestEpochRef.current) {
        setLoadError('记忆清单读取失败，请稍后重试。');
      }
    } finally {
      if (epoch === requestEpochRef.current) {
        setIsLoading(false);
      }
    }
  }, [sessionId, worldline]);

  useEffect(() => {
    if (active) {
      void loadFacts();
    } else {
      // Leaving the tab dismisses any half-open confirm state.
      setPending(null);
      setPromoteError(null);
    }
  }, [active, loadFacts]);

  const confirmPromotion = useCallback(async () => {
    if (!pending || isPromoting) return;
    // The prepared context must still be the live context — otherwise close
    // the sheet with zero network traffic (same-id facts in another
    // worldline/session must never be promoted by accident).
    if (pending.sessionId !== sessionId || pending.worldline !== worldline) {
      setPending(null);
      return;
    }
    const epochAtConfirm = requestEpochRef.current;
    setIsPromoting(true);
    setPromoteError(null);
    try {
      const response = await fetch(
        `/api/memory/facts/${pending.fact.id}/promote?session_id=${encodeURIComponent(pending.sessionId)}&worldline=${encodeURIComponent(pending.worldline)}`,
        { method: 'POST' },
      );
      if (requestEpochRef.current !== epochAtConfirm) return; // context switched mid-flight
      if (!response.ok) {
        let code = '';
        try {
          code = String((await response.json())?.detail ?? '');
        } catch {
          /* non-JSON error body */
        }
        if (requestEpochRef.current === epochAtConfirm) {
          setPromoteError(PROMOTE_ERROR_TEXT[code] ?? PROMOTE_GENERIC_ERROR);
        }
        return; // keep the current list untouched — no optimistic pollution
      }
      setPending(null);
      // Reload only while the original request context is still live. The
      // promoting flag must be cleared BEFORE the reload: loadFacts bumps the
      // request epoch itself, so the finally guard below would otherwise skip
      // the reset and leave every share button disabled (review P0).
      if (requestEpochRef.current === epochAtConfirm) {
        setIsPromoting(false);
        await loadFacts();
      }
    } catch {
      if (requestEpochRef.current === epochAtConfirm) {
        setPromoteError(PROMOTE_GENERIC_ERROR);
      }
    } finally {
      // Only the live epoch may clear the flag; a stale POST finishing late
      // must not write into the new context (cleanup already reset it).
      if (requestEpochRef.current === epochAtConfirm) {
        setIsPromoting(false);
      }
    }
  }, [pending, isPromoting, sessionId, worldline, loadFacts]);

  // Never render a confirm sheet that was prepared for another context.
  const visiblePending =
    pending && pending.sessionId === sessionId && pending.worldline === worldline
      ? pending
      : null;

  return (
    <div className="memory-ledger" data-testid="memory-ledger">
      <p className="memory-ledger-note">
        新记忆默认只属于当前世界线；只有你显式共享的信息才会进入 SG + β。
      </p>
      {isLoading && <p className="memory-ledger-loading">读取中…</p>}
      {loadError && (
        <p className="memory-ledger-error" role="alert">{loadError}</p>
      )}
      {promoteError && (
        <p className="memory-ledger-error" role="alert" data-testid="promote-error">{promoteError}</p>
      )}
      {snapshot && (
        <>
          <section className="memory-ledger-section">
            <h3>本世界线记忆（{worldlineShortLabel(worldline)}）</h3>
            {snapshot.local.length === 0 && (
              <p className="memory-ledger-empty">当前世界线还没有 self 模式的稳定记忆。</p>
            )}
            <ul className="memory-ledger-list">
              {snapshot.local.map((fact) => (
                <li key={`local-${fact.id}`} className="memory-ledger-item" data-testid={`local-fact-${fact.id}`}>
                  <span className="memory-fact-scope">本世界线</span>
                  <span className="memory-fact-key">{fact.fact_key}</span>
                  <span className="memory-fact-value">{fact.fact_value}</span>
                  {fact.is_pinned && <span className="memory-fact-pinned">置顶</span>}
                  <button
                    type="button"
                    className="salieri-btn memory-share-btn"
                    disabled={!fact.promotion_eligible || fact.already_shared === true || isPromoting}
                    onClick={() => {
                      setPromoteError(null);
                      setPending({ fact, sessionId, worldline });
                    }}
                  >
                    共享至 SG + β
                  </button>
                  {fact.already_shared === true && (
                    <span className="memory-fact-shared-mark" data-testid={`already-shared-${fact.id}`}>
                      已共享
                    </span>
                  )}
                  {!fact.promotion_eligible && (
                    <span className="memory-fact-reason" data-testid={`reason-${fact.id}`}>
                      {PROMOTION_REASON_TEXT[fact.promotion_reason ?? ''] ?? PROMOTION_REASON_TEXT.fact_not_promotable}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </section>
          <section className="memory-ledger-section">
            <h3>共享记忆（SG + β）</h3>
            {snapshot.shared.length === 0 && (
              <p className="memory-ledger-empty">还没有跨世界线共享的记忆。</p>
            )}
            <ul className="memory-ledger-list">
              {snapshot.shared.map((fact) => (
                <li key={`shared-${fact.id}`} className="memory-ledger-item" data-testid={`shared-fact-${fact.id}`}>
                  <span className="memory-fact-scope">SG + β</span>
                  <span className="memory-fact-key">{fact.fact_key}</span>
                  <span className="memory-fact-value">{fact.fact_value}</span>
                  {fact.is_pinned && <span className="memory-fact-pinned">置顶</span>}
                  <span className="memory-fact-origin">
                    来源：{worldlineShortLabel(fact.origin_worldline)}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
      {visiblePending && (
        <div className="memory-promote-confirm" role="dialog" aria-label="共享确认">
          <p>{PROMOTE_CONFIRM_TEXT}</p>
          <p className="memory-promote-target">
            {visiblePending.fact.fact_key}：{visiblePending.fact.fact_value}
          </p>
          <div className="memory-promote-actions">
            <button
              type="button"
              className="salieri-btn cancel-btn"
              onClick={() => setPending(null)}
              disabled={isPromoting}
            >
              取消
            </button>
            <button
              type="button"
              className="salieri-btn save-btn"
              onClick={() => { void confirmPromotion(); }}
              disabled={isPromoting}
            >
              {isPromoting ? '共享中…' : '确认共享'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default MemoryLedger;
