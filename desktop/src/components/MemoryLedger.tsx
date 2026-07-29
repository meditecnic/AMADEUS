import React, { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Memory archive panel (Settings → MEMORY).
 *
 * Shows self-local core facts for the current worldline, the cross-worldline
 * shared profile, and pending Okabe-scope candidates for explicit reclassify.
 * Identity-agnostic UI: always the user's self facts, never Okabe-as-chat
 * mode. Client submits only fact ids; server re-verifies from persistence.
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

export interface LedgerOkabeFact {
  id: number;
  fact_key: string;
  fact_value: string;
  confidence: number;
  importance: number;
  is_pinned: boolean;
  created_at: string;
  already_reclassified: boolean;
}

export interface LedgerSnapshot {
  local: LedgerLocalFact[];
  shared: LedgerSharedFact[];
  okabe: LedgerOkabeFact[];
}

/** Confirm sheet copy — promotion is cross-worldline and needs explicit consent. */
export const PROMOTE_CONFIRM_TEXT =
  '此信息会在命运石之门与 β 两条世界线的自我模式中使用。';

/** Stable ineligibility reasons from the ledger listing (short, user-facing). */
export const PROMOTION_REASON_TEXT: Record<string, string> = {
  source_evidence_unavailable: '来源已失效，仅保存在本线',
  identity_scope_violation: '仅保存在本线',
  fact_not_promotable: '仅保存在本线',
};

/** Stable error codes from the promote endpoint. */
export const PROMOTE_ERROR_TEXT: Record<string, string> = {
  fact_not_found: '该记忆已不存在，请刷新后重试。',
  fact_not_promotable: '该记忆暂不可写入跨线记忆。',
  source_evidence_unavailable: '来源证据已不可用，无法写入跨线记忆。',
  identity_scope_violation: '当前范围不允许写入跨线记忆。',
  stale_fact: '该记忆已被更新，请刷新后重试。',
};

export const PROMOTE_GENERIC_ERROR = '请求失败，请稍后重试。';

/** Stable error codes from the reclassify endpoint. */
export const RECLASSIFY_ERROR_TEXT: Record<string, string> = {
  fact_not_found: '该记忆已不存在，请刷新后重试。',
  not_okabe_fact: '这条不是冈部会话中的候选记忆。',
  already_reclassified: '已经确认过了。',
  evidence_chain_broken: '来源证据已不可用，无法确认。',
  fact_dismissed: '该记忆已被忽略。',
  stale_fact: '该记忆已被更新，请刷新后重试。',
  reclassify_failed: '确认失败，请稍后重试。',
  network_error: '网络异常，请稍后重试。',
};

export const DISMISS_ERROR_TEXT: Record<string, string> = {
  fact_not_found: '该记忆已不存在，请刷新后重试。',
  not_okabe_fact: '只能忽略冈部会话中的候选记忆。',
};

export const DISMISS_GENERIC_ERROR = '忽略失败，请稍后重试。';

export const DELETE_ERROR_TEXT: Record<string, string> = {
  fact_not_found: '该记忆已不存在，请刷新后重试。',
  wrong_scope: '当前范围不允许删除。',
};

export const DELETE_GENERIC_ERROR = '删除失败，请稍后重试。';

export const DELETE_LOCAL_CONFIRM_TEXT =
  '将从本线记忆中移除。对话中不再使用。若曾写入跨线记忆，跨线侧默认保留。';

export const DELETE_SHARED_CONFIRM_TEXT =
  '将从跨线记忆中移除。两条世界线的自我模式都不再使用这条。本线若还有副本，默认保留。';

export const RECLASSIFY_GENERIC_ERROR = '确认失败，请稍后重试。';

export function worldlineShortLabel(worldline: 'steins_gate' | 'beta'): string {
  return worldline === 'steins_gate' ? '命运石之门' : 'β';
}

/** Expanded Chinese labels — never fall back to English Title Case. */
const FACT_KEY_LABEL: Record<string, string> = {
  favorite_drink: '喜欢的饮品',
  preferred_drink: '偏好饮品',
  favorite_food: '喜欢的食物',
  food_preferences: '饮食偏好',
  food_preference: '饮食偏好',
  likes_hamburgers: '喜欢汉堡',
  hobby: '爱好',
  hobby_reading: '阅读爱好',
  name: '名字',
  nickname: '昵称',
  age: '年龄',
  occupation: '职业',
  location: '所在地',
  personality_trait: '性格特征',
  game_name: '游戏',
  game: '游戏',
  fan_of: '喜欢的作品/人物',
  game_enjoyment: '游戏体验',
  favorite_sci_fi_author: '喜欢的科幻作者',
  favorite_author: '喜欢的作者',
  user_comparison_gentleness: '性格印象',
  fable5_release_status: '发售状态',
  release_status: '发售状态',
};

/**
 * Human-readable category label. Unknown keys become 「其他」— never
 * English Title Case in the main path.
 */
export function displayFactKey(key: string): string {
  const normalized = (key || '').trim().toLowerCase();
  if (!normalized) return '其他';
  if (FACT_KEY_LABEL[normalized]) return FACT_KEY_LABEL[normalized];
  // snake / kebab common aliases
  const underscored = normalized.replace(/-/g, '_');
  if (FACT_KEY_LABEL[underscored]) return FACT_KEY_LABEL[underscored];
  return '其他';
}

export function displayFactValue(_key: string, value: string): string {
  const v = (value ?? '').trim();
  if (v === 'true') return '是';
  if (v === 'false') return '否';
  if (v === 'already_launched' || v === 'launched') return '已发售';
  if (v === 'not_launched' || v === 'unreleased') return '未发售';
  return value;
}

export interface MemoryLedgerProps {
  sessionId: string;
  worldline: 'steins_gate' | 'beta';
  /** Fetch only while the Memory tab is actually visible. */
  active: boolean;
}

interface PendingPromotion {
  fact: LedgerLocalFact;
  sessionId: string;
  worldline: 'steins_gate' | 'beta';
}

interface PendingOkabeReclassify {
  fact: LedgerOkabeFact;
  sessionId: string;
  worldline: 'steins_gate' | 'beta';
}

interface PendingDelete {
  scope: 'local' | 'shared';
  id: number;
  fact_key: string;
  fact_value: string;
  sessionId: string;
  worldline: 'steins_gate' | 'beta';
}

function mapReclassifyError(code: string): string {
  return RECLASSIFY_ERROR_TEXT[code] ?? RECLASSIFY_GENERIC_ERROR;
}

export const MemoryLedger: React.FC<MemoryLedgerProps> = ({ sessionId, worldline, active }) => {
  const [snapshot, setSnapshot] = useState<LedgerSnapshot | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingPromotion | null>(null);
  const [isPromoting, setIsPromoting] = useState(false);
  const [promoteError, setPromoteError] = useState<string | null>(null);
  const [okabePending, setOkabePending] = useState<PendingOkabeReclassify | null>(null);
  const [isReclassifying, setIsReclassifying] = useState(false);
  const [reclassifyError, setReclassifyError] = useState<string | null>(null);
  const [isDismissing, setIsDismissing] = useState(false);
  const [dismissError, setDismissError] = useState<string | null>(null);
  const [deletePending, setDeletePending] = useState<PendingDelete | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'local' | 'shared'>('local');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const requestEpochRef = useRef(0);
  const contextKey = `${sessionId}::${worldline}`;
  const contextRef = useRef(contextKey);

  if (contextRef.current !== contextKey) {
    contextRef.current = contextKey;
    requestEpochRef.current += 1;
  }

  useEffect(() => {
    setSnapshot(null);
    setLoadError(null);
    setPromoteError(null);
    setPending(null);
    setIsPromoting(false);
    setOkabePending(null);
    setReclassifyError(null);
    setIsReclassifying(false);
    setIsDismissing(false);
    setDismissError(null);
    setDeletePending(null);
    setIsDeleting(false);
    setDeleteError(null);
    setActiveTab('local');
    setDrawerOpen(false);
  }, [contextKey]);

  const loadFacts = useCallback(async () => {
    const epoch = ++requestEpochRef.current;
    setIsLoading(true);
    setLoadError(null);
    try {
      const response = await fetch(
        `/api/memory/facts?session_id=${encodeURIComponent(sessionId)}&worldline=${encodeURIComponent(worldline)}`,
      );
      if (epoch !== requestEpochRef.current) return;
      if (!response.ok) {
        throw new Error(`ledger_http_${response.status}`);
      }
      const body = (await response.json()) as LedgerSnapshot;
      if (epoch !== requestEpochRef.current) return;
      setSnapshot({ local: body.local ?? [], shared: body.shared ?? [], okabe: body.okabe ?? [] });
    } catch {
      if (epoch === requestEpochRef.current) {
        setLoadError('记忆档案读取失败，请稍后重试。');
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
      requestEpochRef.current += 1;
      setPending(null);
      setPromoteError(null);
      setIsPromoting(false);
      setOkabePending(null);
      setReclassifyError(null);
      setIsReclassifying(false);
      setIsDismissing(false);
      setDismissError(null);
      setDeletePending(null);
      setIsDeleting(false);
      setDeleteError(null);
      setActiveTab('local');
      setDrawerOpen(false);
    }
  }, [active, loadFacts]);

  const confirmPromotion = useCallback(async () => {
    if (!pending || isPromoting) return;
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
      if (requestEpochRef.current !== epochAtConfirm) return;
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
        return;
      }
      setPending(null);
      if (requestEpochRef.current === epochAtConfirm) {
        setIsPromoting(false);
        await loadFacts();
      }
    } catch {
      if (requestEpochRef.current === epochAtConfirm) {
        setPromoteError(PROMOTE_GENERIC_ERROR);
      }
    } finally {
      if (requestEpochRef.current === epochAtConfirm) {
        setIsPromoting(false);
      }
    }
  }, [pending, isPromoting, sessionId, worldline, loadFacts]);

  const confirmReclassify = useCallback(async () => {
    if (!okabePending || isReclassifying) return;
    if (okabePending.sessionId !== sessionId || okabePending.worldline !== worldline) {
      setOkabePending(null);
      return;
    }
    const epochAtConfirm = ++requestEpochRef.current;
    setIsReclassifying(true);
    setReclassifyError(null);
    try {
      const resp = await fetch(
        `/api/memory/facts/${okabePending.fact.id}/reclassify?session_id=${encodeURIComponent(okabePending.sessionId)}&worldline=${encodeURIComponent(okabePending.worldline)}&confirmed=true`,
        { method: 'POST' },
      );
      if (epochAtConfirm !== requestEpochRef.current) return;
      if (!resp.ok) {
        let code = '';
        try {
          code = String((await resp.json())?.detail ?? '');
        } catch {
          /* non-JSON */
        }
        if (epochAtConfirm === requestEpochRef.current) {
          setReclassifyError(mapReclassifyError(code || 'reclassify_failed'));
        }
        return;
      }
      setOkabePending(null);
      if (epochAtConfirm === requestEpochRef.current) {
        setIsReclassifying(false);
        await loadFacts();
      }
    } catch {
      if (epochAtConfirm === requestEpochRef.current) {
        setReclassifyError(mapReclassifyError('network_error'));
      }
    } finally {
      if (epochAtConfirm === requestEpochRef.current) {
        setIsReclassifying(false);
      }
    }
  }, [okabePending, isReclassifying, sessionId, worldline, loadFacts]);

  const unconfirmedOkabe = snapshot?.okabe?.filter((f) => !f.already_reclassified) ?? [];
  const unconfirmedCount = unconfirmedOkabe.length;

  const dismissOne = useCallback(
    async (factId: number) => {
      if (isDismissing || isReclassifying) return;
      const epoch = requestEpochRef.current;
      setIsDismissing(true);
      setDismissError(null);
      try {
        const resp = await fetch(
          `/api/memory/facts/${factId}/dismiss?session_id=${encodeURIComponent(sessionId)}&worldline=${encodeURIComponent(worldline)}`,
          { method: 'POST' },
        );
        if (epoch !== requestEpochRef.current) return;
        if (!resp.ok) {
          let code = '';
          try {
            code = String((await resp.json())?.detail ?? '');
          } catch {
            /* non-JSON */
          }
          if (epoch === requestEpochRef.current) {
            setDismissError(DISMISS_ERROR_TEXT[code] ?? DISMISS_GENERIC_ERROR);
          }
          return;
        }
        if (epoch === requestEpochRef.current) {
          setIsDismissing(false);
          await loadFacts();
        }
      } catch {
        if (epoch === requestEpochRef.current) {
          setDismissError(DISMISS_GENERIC_ERROR);
        }
      } finally {
        if (epoch === requestEpochRef.current) {
          setIsDismissing(false);
        }
      }
    },
    [isDismissing, isReclassifying, sessionId, worldline, loadFacts],
  );

  const dismissAllPending = useCallback(async () => {
    if (isDismissing || isReclassifying || unconfirmedOkabe.length === 0) return;
    const ids = unconfirmedOkabe.map((f) => f.id);
    const epoch = requestEpochRef.current;
    setIsDismissing(true);
    setDismissError(null);
    try {
      const resp = await fetch(
        `/api/memory/facts/dismiss-batch?session_id=${encodeURIComponent(sessionId)}&worldline=${encodeURIComponent(worldline)}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ids }),
        },
      );
      if (epoch !== requestEpochRef.current) return;
      if (!resp.ok) {
        if (epoch === requestEpochRef.current) {
          setDismissError(DISMISS_GENERIC_ERROR);
        }
        return;
      }
      if (epoch === requestEpochRef.current) {
        setIsDismissing(false);
        await loadFacts();
      }
    } catch {
      if (epoch === requestEpochRef.current) {
        setDismissError(DISMISS_GENERIC_ERROR);
      }
    } finally {
      if (epoch === requestEpochRef.current) {
        setIsDismissing(false);
      }
    }
  }, [isDismissing, isReclassifying, sessionId, worldline, loadFacts, unconfirmedOkabe]);

  const confirmAllPending = useCallback(async () => {
    if (isDismissing || isReclassifying || unconfirmedOkabe.length === 0) return;
    const epoch = ++requestEpochRef.current;
    setIsReclassifying(true);
    setDismissError(null);
    setReclassifyError(null);
    try {
      for (const fact of unconfirmedOkabe) {
        if (epoch !== requestEpochRef.current) return;
        const resp = await fetch(
          `/api/memory/facts/${fact.id}/reclassify?session_id=${encodeURIComponent(sessionId)}&worldline=${encodeURIComponent(worldline)}&confirmed=true`,
          { method: 'POST' },
        );
        if (epoch !== requestEpochRef.current) return;
        if (!resp.ok) {
          let code = '';
          try {
            code = String((await resp.json())?.detail ?? '');
          } catch {
            /* non-JSON */
          }
          if (epoch === requestEpochRef.current) {
            setReclassifyError(mapReclassifyError(code || 'reclassify_failed'));
          }
          return;
        }
      }
      if (epoch === requestEpochRef.current) {
        setIsReclassifying(false);
        await loadFacts();
      }
    } catch {
      if (epoch === requestEpochRef.current) {
        setReclassifyError(mapReclassifyError('network_error'));
      }
    } finally {
      if (epoch === requestEpochRef.current) {
        setIsReclassifying(false);
      }
    }
  }, [isDismissing, isReclassifying, sessionId, worldline, loadFacts, unconfirmedOkabe]);

  const confirmDelete = useCallback(async () => {
    if (!deletePending || isDeleting) return;
    if (deletePending.sessionId !== sessionId || deletePending.worldline !== worldline) {
      setDeletePending(null);
      return;
    }
    const epoch = requestEpochRef.current;
    setIsDeleting(true);
    setDeleteError(null);
    try {
      const url =
        deletePending.scope === 'local'
          ? `/api/memory/facts/${deletePending.id}/delete-local?session_id=${encodeURIComponent(deletePending.sessionId)}&worldline=${encodeURIComponent(deletePending.worldline)}`
          : `/api/memory/shared-facts/${deletePending.id}/delete?session_id=${encodeURIComponent(deletePending.sessionId)}`;
      const resp = await fetch(url, { method: 'POST' });
      if (epoch !== requestEpochRef.current) return;
      if (!resp.ok) {
        let code = '';
        try {
          code = String((await resp.json())?.detail ?? '');
        } catch {
          /* non-JSON */
        }
        if (epoch === requestEpochRef.current) {
          setDeleteError(DELETE_ERROR_TEXT[code] ?? DELETE_GENERIC_ERROR);
        }
        return;
      }
      setDeletePending(null);
      if (epoch === requestEpochRef.current) {
        setIsDeleting(false);
        await loadFacts();
      }
    } catch {
      if (epoch === requestEpochRef.current) {
        setDeleteError(DELETE_GENERIC_ERROR);
      }
    } finally {
      if (epoch === requestEpochRef.current) {
        setIsDeleting(false);
      }
    }
  }, [deletePending, isDeleting, sessionId, worldline, loadFacts]);

  const visiblePending =
    pending && pending.sessionId === sessionId && pending.worldline === worldline
      ? pending
      : null;

  const visibleDelete =
    deletePending &&
    deletePending.sessionId === sessionId &&
    deletePending.worldline === worldline
      ? deletePending
      : null;

  const selectTab = (tab: 'local' | 'shared') => {
    setActiveTab(tab);
    setDrawerOpen(false);
  };

  return (
    <div className="memory-ledger" data-testid="memory-ledger">
      <p className="memory-ledger-note">本页修改立即生效，无需点击底部「保存修改」。</p>
      {isLoading && <p className="memory-ledger-loading">正在读取记忆档案…</p>}
      {loadError && (
        <p className="memory-ledger-error" role="alert">
          {loadError}
        </p>
      )}
      {snapshot && (
        <>
          <div className="memory-tab-bar" aria-label="记忆分区">
            <button
              type="button"
              className={`memory-tab-pill ${activeTab === 'local' && !drawerOpen ? 'active' : ''}`}
              onClick={() => selectTab('local')}
            >
              本线记忆
            </button>
            <button
              type="button"
              className={`memory-tab-pill ${activeTab === 'shared' && !drawerOpen ? 'active' : ''}`}
              onClick={() => selectTab('shared')}
            >
              跨线记忆
            </button>
            {unconfirmedCount > 0 && (
              <button
                type="button"
                className={`memory-tab-pill memory-tab-pill-drawer ${drawerOpen ? 'active' : ''}`}
                onClick={() => setDrawerOpen(!drawerOpen)}
                aria-expanded={drawerOpen}
              >
                待确认
                <span className="memory-tab-badge">{unconfirmedCount}</span>
              </button>
            )}
          </div>

          {activeTab === 'local' && !drawerOpen && (
            <section className="memory-ledger-section memory-section-enter" aria-label="本线记忆">
              {snapshot.local.length === 0 ? (
                <p className="memory-ledger-empty">
                  还没有本线记忆。在「自我」模式下聊天时，明确说过的重要信息会逐渐沉淀到这里。
                </p>
              ) : (
                <ul className="memory-ledger-list">
                  {snapshot.local.map((fact, index) => (
                    <li
                      key={`local-${fact.id}`}
                      className="memory-archive-card"
                      data-testid={`local-fact-${fact.id}`}
                      style={{ animationDelay: `${Math.min(index, 8) * 40}ms` }}
                    >
                      <div className="memory-card-body">
                        <div className="memory-card-head">
                          <span className="memory-card-label">{displayFactKey(fact.fact_key)}</span>
                          {fact.is_pinned && <span className="memory-fact-pinned">置顶</span>}
                          <span className="memory-card-chip">本线</span>
                        </div>
                        <p className="memory-card-value">
                          {displayFactValue(fact.fact_key, fact.fact_value)}
                        </p>
                        {fact.already_shared ? (
                          <p className="memory-card-meta memory-card-meta-ok" data-testid={`already-shared-${fact.id}`}>
                            已在跨线记忆
                          </p>
                        ) : fact.promotion_eligible ? null : (
                          <p className="memory-card-meta" data-testid={`reason-${fact.id}`}>
                            {PROMOTION_REASON_TEXT[fact.promotion_reason ?? ''] ??
                              PROMOTION_REASON_TEXT.fact_not_promotable}
                          </p>
                        )}
                      </div>
                      <div className="memory-card-actions">
                        <button
                          type="button"
                          className="memory-delete-btn"
                          data-testid={`delete-local-${fact.id}`}
                          disabled={isDeleting || isPromoting}
                          onClick={() => {
                            setDeleteError(null);
                            setDeletePending({
                              scope: 'local',
                              id: fact.id,
                              fact_key: fact.fact_key,
                              fact_value: fact.fact_value,
                              sessionId,
                              worldline,
                            });
                          }}
                        >
                          删除
                        </button>
                        {fact.promotion_eligible && !fact.already_shared && (
                          <button
                            type="button"
                            className="salieri-btn memory-share-btn"
                            disabled={isPromoting || isDeleting}
                            onClick={() => {
                              setPromoteError(null);
                              setPending({ fact, sessionId, worldline });
                            }}
                          >
                            写入跨线记忆
                          </button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          {activeTab === 'shared' && !drawerOpen && (
            <section className="memory-ledger-section memory-section-enter" aria-label="跨线记忆">
              {snapshot.shared.length === 0 ? (
                <p className="memory-ledger-empty">
                  还没有跨线记忆。在本线记忆中把重要信息「写入跨线记忆」后，两条世界线的自我模式都能用到。
                </p>
              ) : (
                <ul className="memory-ledger-list">
                  {snapshot.shared.map((fact, index) => (
                    <li
                      key={`shared-${fact.id}`}
                      className="memory-archive-card"
                      data-testid={`shared-fact-${fact.id}`}
                      style={{ animationDelay: `${Math.min(index, 8) * 40}ms` }}
                    >
                      <div className="memory-card-body">
                        <div className="memory-card-head">
                          <span className="memory-card-label">{displayFactKey(fact.fact_key)}</span>
                          {fact.is_pinned && <span className="memory-fact-pinned">置顶</span>}
                          <span className="memory-card-chip memory-card-chip-shared">跨线</span>
                        </div>
                        <p className="memory-card-value">
                          {displayFactValue(fact.fact_key, fact.fact_value)}
                        </p>
                        <p className="memory-card-meta">
                          来源：{worldlineShortLabel(fact.origin_worldline)}
                        </p>
                      </div>
                      <div className="memory-card-actions">
                        <button
                          type="button"
                          className="memory-delete-btn"
                          data-testid={`delete-shared-${fact.id}`}
                          disabled={isDeleting}
                          onClick={() => {
                            setDeleteError(null);
                            setDeletePending({
                              scope: 'shared',
                              id: fact.id,
                              fact_key: fact.fact_key,
                              fact_value: fact.fact_value,
                              sessionId,
                              worldline,
                            });
                          }}
                        >
                          删除
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          {drawerOpen && (
            <section className="memory-ledger-section memory-section-enter" aria-label="待确认">
              <h3 className="memory-section-title">待确认 · 冈部候选</h3>
              <p className="memory-section-hint">
                这些内容来自冈部模式对话，不会自动当作你的真实资料。确认后会复制到本线记忆（原记录保留，不可撤销）；忽略后不再显示，也不再用于对话。
              </p>
              {unconfirmedOkabe.length > 0 && (
                <div className="memory-bulk-bar">
                  <button
                    type="button"
                    className="memory-bulk-btn"
                    disabled={isDismissing || isReclassifying}
                    onClick={() => {
                      void dismissAllPending();
                    }}
                    data-testid="bulk-dismiss"
                  >
                    全部忽略
                  </button>
                  <button
                    type="button"
                    className="memory-bulk-btn memory-bulk-btn-primary"
                    disabled={isDismissing || isReclassifying}
                    onClick={() => {
                      void confirmAllPending();
                    }}
                    data-testid="bulk-confirm"
                  >
                    全部确认
                  </button>
                </div>
              )}
              {dismissError && (
                <p className="memory-ledger-error" role="alert" data-testid="dismiss-error">
                  {dismissError}
                </p>
              )}
              {reclassifyError && !okabePending && (
                <p className="memory-ledger-error" role="alert" data-testid="bulk-reclassify-error">
                  {reclassifyError}
                </p>
              )}
              {unconfirmedOkabe.length === 0 ? (
                <p className="memory-ledger-empty">没有待确认的候选。</p>
              ) : (
                <ul className="memory-ledger-list">
                  {unconfirmedOkabe.map((fact, index) => (
                    <li
                      key={`okabe-${fact.id}`}
                      className="memory-archive-card memory-archive-card-pending"
                      data-testid={`okabe-fact-${fact.id}`}
                      style={{ animationDelay: `${Math.min(index, 8) * 40}ms` }}
                    >
                      <div className="memory-card-body">
                        <div className="memory-card-head">
                          <span className="memory-fact-scope">来自冈部会话</span>
                          <span className="memory-card-label">{displayFactKey(fact.fact_key)}</span>
                          {fact.is_pinned && <span className="memory-fact-pinned">置顶</span>}
                        </div>
                        <p className="memory-card-value">
                          {displayFactValue(fact.fact_key, fact.fact_value)}
                        </p>
                      </div>
                      <div className="memory-card-actions">
                        <button
                          type="button"
                          className="memory-dismiss-btn"
                          onClick={() => {
                            void dismissOne(fact.id);
                          }}
                          disabled={isDismissing || isReclassifying}
                          data-testid={`dismiss-${fact.id}`}
                        >
                          忽略
                        </button>
                        <button
                          type="button"
                          className="memory-reclassify-btn"
                          onClick={() => setOkabePending({ fact, sessionId, worldline })}
                          disabled={isReclassifying || isDismissing}
                        >
                          确认为真实信息
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}
        </>
      )}

      {okabePending &&
        okabePending.sessionId === sessionId &&
        okabePending.worldline === worldline && (
          <div className="memory-confirm-overlay" role="dialog" aria-label="确认为真实信息">
            <div
              className="memory-confirm-backdrop"
              onClick={() => {
                if (isReclassifying) return;
                setOkabePending(null);
                setReclassifyError(null);
              }}
            />
            <div className="memory-confirm-panel memory-confirm-panel-enter memory-confirm-panel-okabe">
              <p className="memory-confirm-title">确认为真实信息</p>
              <p className="memory-confirm-desc">
                将把这条内容复制到你的真实档案（本线记忆）。冈部侧原记录保留。当前版本不可撤销。
              </p>
              <div className="memory-confirm-target">
                <span className="memory-card-label">
                  {displayFactKey(okabePending.fact.fact_key)}
                </span>
                <span className="memory-card-value">
                  {displayFactValue(okabePending.fact.fact_key, okabePending.fact.fact_value)}
                </span>
              </div>
              {reclassifyError && (
                <p className="memory-ledger-error" data-testid="reclassify-error">
                  {reclassifyError}
                </p>
              )}
              <div className="memory-confirm-actions">
                <button
                  type="button"
                  onClick={() => {
                    setOkabePending(null);
                    setReclassifyError(null);
                  }}
                  disabled={isReclassifying}
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={() => {
                    void confirmReclassify();
                  }}
                  disabled={isReclassifying}
                >
                  确认
                </button>
              </div>
            </div>
          </div>
        )}

      {visiblePending && (
        <div className="memory-confirm-overlay" role="dialog" aria-label="写入跨线记忆确认">
          <div
            className="memory-confirm-backdrop"
            onClick={() => {
              if (isPromoting) return;
              setPending(null);
            }}
          />
          <div className="memory-confirm-panel memory-confirm-panel-enter">
            <p className="memory-confirm-title">写入跨线记忆</p>
            <p className="memory-confirm-desc">
              {PROMOTE_CONFIRM_TEXT}
              本线记忆会保留，不会被删除。
            </p>
            <div className="memory-confirm-target">
              <span className="memory-card-label">
                {displayFactKey(visiblePending.fact.fact_key)}
              </span>
              <span className="memory-card-value">
                {displayFactValue(visiblePending.fact.fact_key, visiblePending.fact.fact_value)}
              </span>
            </div>
            {promoteError && (
              <p className="memory-ledger-error" data-testid="promote-error">
                {promoteError}
              </p>
            )}
            <div className="memory-confirm-actions">
              <button type="button" onClick={() => setPending(null)} disabled={isPromoting}>
                取消
              </button>
              <button
                type="button"
                onClick={() => {
                  void confirmPromotion();
                }}
                disabled={isPromoting}
              >
                确认写入
              </button>
            </div>
          </div>
        </div>
      )}

      {visibleDelete && (
        <div className="memory-confirm-overlay" role="dialog" aria-label="删除记忆确认">
          <div
            className="memory-confirm-backdrop"
            onClick={() => {
              if (isDeleting) return;
              setDeletePending(null);
              setDeleteError(null);
            }}
          />
          <div className="memory-confirm-panel memory-confirm-panel-enter">
            <p className="memory-confirm-title">
              {visibleDelete.scope === 'local' ? '删除本线记忆' : '删除跨线记忆'}
            </p>
            <p className="memory-confirm-desc">
              {visibleDelete.scope === 'local'
                ? DELETE_LOCAL_CONFIRM_TEXT
                : DELETE_SHARED_CONFIRM_TEXT}
            </p>
            <div className="memory-confirm-target">
              <span className="memory-card-label">{displayFactKey(visibleDelete.fact_key)}</span>
              <span className="memory-card-value">
                {displayFactValue(visibleDelete.fact_key, visibleDelete.fact_value)}
              </span>
            </div>
            {deleteError && (
              <p className="memory-ledger-error" data-testid="delete-error">
                {deleteError}
              </p>
            )}
            <div className="memory-confirm-actions">
              <button
                type="button"
                onClick={() => {
                  setDeletePending(null);
                  setDeleteError(null);
                }}
                disabled={isDeleting}
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => {
                  void confirmDelete();
                }}
                disabled={isDeleting}
                data-testid="confirm-delete"
              >
                确认删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default MemoryLedger;
