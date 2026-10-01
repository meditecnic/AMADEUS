import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createArchiveModule, type ArchiveModule } from './archiveState';
import { ArchiveView } from './ArchiveView';
import type { ArchiveScope } from './archiveApi';

const SCOPE: ArchiveScope = { sessionId: 's-view', worldline: 'steins_gate', identityMode: 'okabe' };

function scopeBody() {
  return { session_id: SCOPE.sessionId, worldline: SCOPE.worldline, identity_mode: SCOPE.identityMode };
}

function viewResponder(deletedRef: { value: boolean }) {
  return (url: string, init?: RequestInit): { status: number; body: unknown } | undefined => {
    if (!url.includes(`session_id=${SCOPE.sessionId}`)) return undefined;
    if (url.startsWith('/api/memory/facts?')) {
      if (deletedRef.value) {
        return {
          status: 200,
          body: { facts: [], scope: scopeBody(), pagination: { limit: 20, offset: 0, total: 0, has_more: false } },
        };
      }
      return {
        status: 200,
        body: {
          facts: [
            {
              fact_id: 'f1',
              display_text: '喜欢黑咖啡',
              version_no: 1,
              active_version: 1,
              is_pinned: true,
              topic_id: 't1',
              confidence: 0.93,
              updated_at: '2026-08-20T00:00:00Z',
            },
          ],
          scope: scopeBody(),
          pagination: { limit: 20, offset: 0, total: 1, has_more: false },
        },
      };
    }
    if (url.startsWith('/api/memory/experiences?')) {
      return {
        status: 200,
        body: {
          experiences: [
            {
              experience_id: 'e1',
              conversation_id: 'c1',
              display_text: '深夜实验室',
              confidence: 0.81,
              status: 'active',
              expires_at: '2026-01-01T00:00:00Z',
              created_at: '2025-12-31T00:00:00Z',
              is_expired: true,
              is_pinned: false,
            },
          ],
          scope: scopeBody(),
          pagination: { limit: 20, offset: 0, total: 1, has_more: false },
        },
      };
    }
    if (url.startsWith('/api/memory/topics?')) {
      return { status: 200, body: { scope: scopeBody(), topics: [{ topic_id: 't1', display_label: '咖啡', fact_count: 1 }] } };
    }
    if (url.startsWith('/api/memory/status?')) {
      return {
        status: 200,
        body: {
          session_id: SCOPE.sessionId,
          worldline: SCOPE.worldline,
          identity_mode: SCOPE.identityMode,
          runtime_mode: 'v11', pending_count: 0,
          processing_count: 0,
          failed_count: 2,
          failed_jobs: [
            {
              job_id: 'j1',
              attempt_count: 3,
              last_error_code: 'provider_unavailable',
              updated_at: '2026-08-02T00:00:00+00:00',
              retryable: true,
            },
            {
              job_id: 'j2-nope',
              attempt_count: 1,
              last_error_code: 'validation_failed',
              updated_at: '2026-08-01T00:00:00+00:00',
              retryable: false,
            },
          ],
        },
      };
    }
    if (url.startsWith('/api/memory/observations?')) {
      return {
        status: 200,
        body: {
          scope: scopeBody(),
          observations: [
            {
              observation_id: 'o1',
              display_text: '候选观察',
              evidence_kind: 'direct_user',
              memory_class: 'stable_candidate',
              confidence: 0.5,
              status: 'candidate',
              reanalysis_count: 0,
              expires_at: null,
              created_at: '2026-08-01T00:00:00Z',
            },
          ],
          pagination: { limit: 20, offset: 0, total: 1, has_more: false },
        },
      };
    }
    if (url.startsWith('/api/memory/facts/f1/details')) {
      return {
        status: 200,
        body: {
          scope: scopeBody(),
          fact: { fact_id: 'f1', topic_id: 't1', is_pinned: true, active_version: 1, display_text: '喜欢黑咖啡' },
          versions: [
            {
              version_no: 1,
              display_text: '喜欢黑咖啡',
              change_kind: 'create',
              previous_version: null,
              valid_from: '2026-08-20T00:00:00Z',
              invalid_at: null,
              is_active: true,
            },
          ],
          provenance: [],
        },
      };
    }
    if (url.startsWith('/api/memory/experiences/e1/details')) {
      return {
        status: 200,
        body: {
          scope: scopeBody(),
          experience: {
            experience_id: 'e1',
            conversation_id: 'c1',
            display_text: '深夜实验室',
            status: 'active',
            expires_at: '2026-01-01T00:00:00Z',
            created_at: '2025-12-31T00:00:00Z',
            is_expired: true,
          },
          observation_id: 'o-exp',
          provenance: [],
        },
      };
    }
    if (url.startsWith('/api/memory/facts/f1?') && (init as RequestInit)?.method === 'DELETE') {
      deletedRef.value = true;
      return { status: 200, body: { fact_id: 'f1', state: 'deleted', deleted: true } };
    }
    if (url.includes('/presentation')) {
      return { status: 200, body: { fact_id: 'f1', experience_id: 'e1', is_pinned: false, topic_id: null } };
    }
    return undefined;
  };
}

function stubFetch(responder: (url: string, init?: RequestInit) => { status: number; body: unknown } | undefined) {
  const calls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(url);
      const scripted = responder(url, init);
      const body = scripted?.body ?? {};
      const status = scripted?.status ?? 200;
      return Promise.resolve({
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
      });
    }),
  );
  return calls;
}

async function renderArchive(): Promise<{ module: ArchiveModule }> {
  const deletedRef = { value: false };
  stubFetch(viewResponder(deletedRef));
  const module = createArchiveModule();
  await act(async () => {
    module.open(SCOPE);
  });
  await act(async () => {
    render(<ArchiveView module={module} />);
  });
  await waitFor(() => {
    expect(screen.getByTestId('archive-view')).toBeTruthy();
  });
  return { module };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('ArchiveView list and reading', () => {
  it('explains a disconnected runtime and disables record mutations while preserving reading', async () => {
    const base = viewResponder({ value: false });
    stubFetch((url, init) => {
      const response = base(url, init);
      if (response && url.startsWith('/api/memory/status?')) {
        response.body = { ...(response.body as object), runtime_mode: 'legacy' };
      }
      return response;
    });
    const module = createArchiveModule();
    await act(async () => { module.open(SCOPE, { focus: { kind: 'fact', recordId: 'f1' } }); });
    render(<ArchiveView module={module} />);
    await screen.findByTestId('archive-detail-title');
    expect(screen.getByTestId('archive-runtime-notice')).toHaveTextContent('当前对话尚未使用本页的记忆');
    expect(screen.getByTestId('archive-edit-fact')).toBeDisabled();
    expect(screen.getByTestId('archive-delete-record')).toBeDisabled();
    expect(screen.getByTestId('archive-toggle-pin')).toBeDisabled();
    expect(screen.getByTestId('archive-detail-title')).toHaveTextContent('喜欢黑咖啡');
    module.dispose();
  });
  it('keeps correction and delete confirmation usable for a record outside the current page', async () => {
    const respond = viewResponder({ value: false });
    stubFetch((url, init) => url.startsWith('/api/memory/facts?') ? {
      status: 200,
      body: { facts: [], scope: scopeBody(), pagination: { limit: 20, offset: 20, total: 40, has_more: false } },
    } : respond(url, init));
    const module = createArchiveModule();
    await act(async () => { module.open(SCOPE, { focus: { kind: 'fact', recordId: 'f1' } }); });
    render(<ArchiveView module={module} />);
    await screen.findByTestId('archive-detail-title');
    expect(screen.getByText('这条记忆不在当前页，已读取完整记录。')).toBeInTheDocument();
    expect(screen.getByTestId('archive-assign-topic')).toHaveValue('t1');
    expect(screen.getByTestId('archive-toggle-pin')).toHaveTextContent('取消置顶');
    fireEvent.click(screen.getByTestId('archive-edit-fact'));
    expect(screen.getByTestId('archive-editor-input')).toHaveValue('喜欢黑咖啡');
    expect(module.snapshot().editor?.baseVersion).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: '放弃草稿' }));
    fireEvent.click(screen.getByTestId('archive-delete-record'));
    expect(screen.getByTestId('archive-delete-confirm')).toHaveTextContent('喜欢黑咖啡');
    expect(module.snapshot().deleteFlow?.expectedVersion).toBe(1);
    fireEvent.click(screen.getByTestId('archive-delete-cancel'));
    expect(screen.queryByTestId('archive-delete-confirm')).toBeNull();
    module.dispose();
  });

  it('renders fact and experience rows with text-based pin/expiry markers', async () => {
    await renderArchive();
    await waitFor(() => {
      expect(screen.getByText('喜欢黑咖啡')).toBeTruthy();
    });
    // Pin is announced with text on the fact panel (never color alone).
    expect(screen.getByTestId('archive-view').textContent).toContain('置顶');
    fireEvent.click(screen.getByTestId('archive-tab-experience'));
    await waitFor(() => {
      expect(screen.getByText('深夜实验室')).toBeTruthy();
    });
    // Expiry is announced with text on the experience panel.
    expect(screen.getByTestId('archive-view').textContent).toContain('已过期');
  });

  it('kind tabs really switch panels: only the active kind list renders', async () => {
    await renderArchive();
    await waitFor(() => {
      expect(screen.getByText('喜欢黑咖啡')).toBeTruthy();
    });
    const factTab = screen.getByTestId('archive-tab-fact');
    // Proper tab wiring: aria-controls on the tab, matching panel id.
    expect(factTab.getAttribute('aria-controls')).toBe('archive-panel-fact');
    // Fact tab active: the fact list renders, the experience list does not.
    expect(screen.queryByTestId('archive-facts-section')).toBeTruthy();
    expect(screen.queryByTestId('archive-experiences-section')).toBeNull();
    fireEvent.click(screen.getByTestId('archive-tab-experience'));
    await waitFor(() => {
      expect(screen.getByText('深夜实验室')).toBeTruthy();
    });
    expect(screen.queryByTestId('archive-experiences-section')).toBeTruthy();
    expect(screen.queryByTestId('archive-facts-section')).toBeNull();
    const experiencePanel = screen.getByTestId('archive-experiences-section');
    expect(experiencePanel.getAttribute('role')).toBe('tabpanel');
    expect(experiencePanel.getAttribute('aria-labelledby')).toBe('archive-tab-experience');
    // Switching back keeps the committed per-kind Archive state.
    fireEvent.click(screen.getByTestId('archive-tab-fact'));
    await waitFor(() => {
      expect(screen.getByText('喜欢黑咖啡')).toBeTruthy();
    });
    expect(screen.queryByTestId('archive-experiences-section')).toBeNull();
  });

  it('never renders confidence anywhere in the archive surface', async () => {
    await renderArchive();
    await waitFor(() => {
      expect(screen.getByText('喜欢黑咖啡')).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId('archive-tab-experience'));
    await waitFor(() => {
      expect(screen.getByText('深夜实验室')).toBeTruthy();
    });
    const root = screen.getByTestId('archive-view');
    expect(root.textContent).not.toContain('0.93');
    expect(root.textContent).not.toContain('0.81');
    expect(root.textContent).not.toContain('confidence');
  });

  it('opens P1R-3 details on selection and shows governance only where authorized', async () => {
    await renderArchive();
    fireEvent.click(screen.getByText('喜欢黑咖啡'));
    await waitFor(() => {
      expect(screen.getByTestId('archive-detail')).toBeTruthy();
    });
    // Fact governance: correct text, pin, topic, delete.
    expect(screen.getByTestId('archive-edit-fact')).toBeTruthy();
    expect(screen.getByTestId('archive-toggle-pin')).toBeTruthy();
    expect(screen.getByTestId('archive-delete-record')).toBeTruthy();
    // Experience selection exposes pin + delete ONLY (no text editor).
    fireEvent.click(screen.getByTestId('archive-tab-experience'));
    await waitFor(() => {
      expect(screen.getByText('深夜实验室')).toBeTruthy();
    });
    fireEvent.click(screen.getByText('深夜实验室'));
    await waitFor(() => {
      expect(screen.getByTestId('archive-detail')).toBeTruthy();
    });
    expect(screen.queryByTestId('archive-edit-fact')).toBeNull();
    expect(screen.getByTestId('archive-toggle-pin')).toBeTruthy();
    expect(screen.getByTestId('archive-delete-record')).toBeTruthy();
  });
});

describe('ArchiveView deletion closure', () => {
  it('confirmation names kind, summary and scope; success purges all readable copies', async () => {
    await renderArchive();
    fireEvent.click(screen.getByText('喜欢黑咖啡'));
    await waitFor(() => {
      expect(screen.getByTestId('archive-delete-record')).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId('archive-delete-record'));
    const dialog = screen.getByTestId('archive-delete-confirm');
    expect(dialog.textContent).toContain('事实');
    expect(dialog.textContent).toContain('喜欢黑咖啡');
    expect(dialog.textContent).toContain('OKABE');
    // No conversation-wide derivative erasure choice exists.
    expect(dialog.textContent).not.toContain('会话');
    fireEvent.click(screen.getByTestId('archive-delete-confirm-button'));
    await waitFor(() => {
      expect(screen.queryByText('喜欢黑咖啡')).toBeNull();
    });
    expect(screen.queryByTestId('archive-delete-confirm')).toBeNull();
    expect(screen.getByTestId('archive-view').textContent).not.toContain('喜欢黑咖啡');
  });
});

describe('ArchiveView forbidden controls and disclosure', () => {
  it('exposes no create/promote/reclassify/bulk/consolidation/conversation-delete controls', async () => {
    await renderArchive();
    await waitFor(() => {
      expect(screen.getByText('喜欢黑咖啡')).toBeTruthy();
    });
    const root = screen.getByTestId('archive-view');
    const text = root.textContent ?? '';
    for (const forbidden of ['手动创建', '提升', '重新分类', '批量确认', '合并主题', '整合', '删除会话', '会话删除']) {
      expect(text).not.toContain(forbidden);
    }
    expect(screen.queryByTestId('archive-create-record')).toBeNull();
  });

  it('discloses remote processing truthfully without a per-record consent ceremony', async () => {
    await renderArchive();
    const disclosure = screen.getByTestId('archive-disclosure');
    expect(disclosure.textContent).toContain('远程服务商');
    // Never promises local-only processing.
    expect(disclosure.textContent).not.toContain('从不发送');
    expect(disclosure.textContent).not.toContain('仅在本地处理');
  });
});

describe('ArchiveView deep-link fallback', () => {
  it('a narrow screen returns to the scoped list when the deep-link target is gone', async () => {
    stubFetch((url) => {
      if (url.startsWith('/api/memory/facts/missing/details')) {
        return { status: 404, body: { detail: { code: 'fact_not_found', message: '' } } };
      }
      return viewResponder({ value: false })(url);
    });
    const module = createArchiveModule();
    await act(async () => {
      module.open(SCOPE, { focus: { kind: 'fact', recordId: 'missing' } });
    });
    await act(async () => {
      render(<ArchiveView module={module} width={504} />);
    });
    await waitFor(() => {
      expect(screen.getByTestId('archive-focus-fallback')).toBeTruthy();
    });
    // Honest notice is shown, the scoped list is back, and no empty detail
    // pane lingers on the narrow surface.
    expect(screen.getByTestId('archive-focus-fallback').textContent).toContain('无法在此范围内找到');
    expect(screen.queryByTestId('archive-facts-section')).toBeTruthy();
    expect(screen.queryByTestId('archive-detail')).toBeNull();
  });
});

describe('ArchiveView secondary 待处理 region', () => {
  it('offers ignore/retry only for backend-declared eligible items', async () => {
    await renderArchive();
    await waitFor(() => {
      expect(screen.getByTestId('archive-pending')).toBeTruthy();
    });
    // candidate observation → ignore offered (backend-declared status).
    expect(screen.getByTestId('archive-ignore-o1')).toBeTruthy();
    // failed job with retryable=true → retry offered.
    expect(screen.getByTestId('archive-retry-j1')).toBeTruthy();
  });

  it('retryable=false jobs show no retry control and are never inferred from error text', async () => {
    await renderArchive();
    await waitFor(() => {
      expect(screen.getByTestId('archive-pending')).toBeTruthy();
    });
    // Server said retryable=false: no retry button, honest 不可重试 marker.
    expect(screen.queryByTestId('archive-retry-j2-nope')).toBeNull();
    expect(screen.getByTestId('archive-pending').textContent).toContain('不可重试');
  });

  it('failed pending sources must not masquerade as the empty state', async () => {
    stubFetch((url) => {
      const base = viewResponder({ value: false })(url);
      if (url.startsWith('/api/memory/observations?')) {
        return { status: 500, body: { detail: { code: 'memory_operation_failed', message: '' } } };
      }
      if (url.startsWith('/api/memory/status?')) {
        return { status: 500, body: { detail: { code: 'memory_operation_failed', message: '' } } };
      }
      return base;
    });
    const module = createArchiveModule();
    await act(async () => {
      module.open(SCOPE);
    });
    await act(async () => {
      render(<ArchiveView module={module} />);
    });
    await waitFor(() => {
      expect(screen.getByTestId('archive-pending')).toBeTruthy();
    });
    const pending = screen.getByTestId('archive-pending');
    // A failure is NOT an empty result: the honest alert appears…
    expect(pending.querySelector('[role="alert"]')?.textContent).toContain('暂时不可用');
    // …and the "no pending items" empty state is suppressed.
    expect(pending.textContent).not.toContain('没有待处理项');
  });

  it('one failed pending source keeps the successful side visible with an alert', async () => {
    stubFetch((url) => {
      const base = viewResponder({ value: false })(url);
      if (url.startsWith('/api/memory/status?')) {
        return { status: 500, body: { detail: { code: 'memory_operation_failed', message: '' } } };
      }
      return base;
    });
    const module = createArchiveModule();
    await act(async () => {
      module.open(SCOPE);
    });
    await act(async () => {
      render(<ArchiveView module={module} />);
    });
    await waitFor(() => {
      expect(screen.getByTestId('archive-pending')).toBeTruthy();
    });
    const pending = screen.getByTestId('archive-pending');
    // The successful observations side still shows its row…
    expect(screen.getByTestId('archive-ignore-o1')).toBeTruthy();
    // …the failure is announced…
    expect(pending.querySelector('[role="alert"]')?.textContent).toContain('暂时不可用');
    // …and the empty state never masquerades behind a failure.
    expect(pending.textContent).not.toContain('没有待处理项');
  });
});
