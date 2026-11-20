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
          pending_count: 0,
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
    expect(dialog.textContent).toContain('认知');
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
    expect(disclosure.textContent).toContain('模型服务商');
    expect(disclosure.textContent).toContain('保存在本机不代表对话时不外发');
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

  it('defaults pending to expanded with guidance when items exist and allows collapsing, and collapses empty by default', async () => {
    await renderArchive();
    await waitFor(() => {
      expect(screen.getByTestId('archive-pending-toggle')).toHaveAttribute('aria-expanded', 'true');
    });
    expect(screen.getByTestId('archive-pending-count').textContent).toContain('3');
    expect(screen.getByTestId('archive-pending').textContent).toContain('确认后会记入「认知」，忽略则不再保留。');

    fireEvent.click(screen.getByTestId('archive-pending-toggle'));
    expect(screen.getByTestId('archive-pending-toggle')).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByTestId('archive-ignore-o1')).toBeNull();

    cleanup();
    stubFetch((url) => {
      if (url.startsWith('/api/memory/status?')) {
        return {
          status: 200,
          body: {
            session_id: SCOPE.sessionId,
            worldline: SCOPE.worldline,
            identity_mode: SCOPE.identityMode,
            pending_count: 0,
            processing_count: 0,
            failed_count: 0,
            failed_jobs: [],
          },
        };
      }
      if (url.startsWith('/api/memory/observations?')) {
        return {
          status: 200,
          body: { scope: scopeBody(), observations: [], pagination: { limit: 20, offset: 0, total: 0, has_more: false } },
        };
      }
      return viewResponder({ value: false })(url);
    });
    const emptyModule = createArchiveModule();
    await act(async () => {
      emptyModule.open(SCOPE);
    });
    await act(async () => {
      render(<ArchiveView module={emptyModule} />);
    });
    await waitFor(() => {
      expect(screen.getByTestId('archive-pending-count').textContent).toContain('0');
    });
    const toggle = screen.getByTestId('archive-pending-toggle');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByTestId('archive-pending').textContent).not.toContain('当前范围内没有待处理项');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByTestId('archive-pending').textContent).toContain('当前范围内没有待处理项。');
  });

  it('uses plain Chinese copy for empty provenance excerpts', async () => {
    await renderArchive();
    await waitFor(() => {
      expect(screen.getByText('喜欢黑咖啡')).toBeTruthy();
    });
    fireEvent.click(screen.getByText('喜欢黑咖啡'));
    await waitFor(() => {
      expect(screen.getByTestId('archive-detail').textContent).toContain('暂无可显示的原话摘录');
    });
  });
});

describe('ArchiveView diaries and topic management', () => {
  it('reads bilingual diary without leaking verified_phrases, confirms deletion, and renders empty state', async () => {
    let diaries = [
      {
        diary_id: 'd1',
        conversation_id: 'conv-lab',
        body_ja: '方眼紙に実験記録を手書きするのは悪くない。',
        body_zh: '在方格纸上手写实验记录倒不坏。',
        written_at: '2026-11-18T12:00:00Z',
        source_count: 8,
        verified_phrases_json: '["SECRET_PHRASE_NEVER_RENDER"]',
      },
    ];
    stubFetch((url, init) => {
      if (url.startsWith('/api/conversations?')) {
        return { status: 200, body: [{ id: 'conv-lab', title: '实验记录' }] };
      }
      if (url.startsWith('/api/memory/diaries/d1?') && init?.method === 'DELETE') {
        diaries = [];
        return { status: 204, body: null };
      }
      if (url.startsWith('/api/memory/diaries?')) {
        return { status: 200, body: { scope: scopeBody(), diaries } };
      }
      return viewResponder({ value: false })(url, init);
    });
    const module = createArchiveModule();
    await act(async () => { module.open(SCOPE); });
    render(<ArchiveView module={module} />);

    await waitFor(() => {
      expect(screen.getByTestId('archive-tab-diary')).toHaveTextContent('日记（1）');
    });
    fireEvent.click(screen.getByTestId('archive-tab-diary'));
    await waitFor(() => {
      expect(screen.getByTestId('archive-diaries-section')).toBeInTheDocument();
    });
    expect(screen.getByTestId('archive-diaries-section').textContent).toContain('来自对话「实验记录」');
    fireEvent.click(screen.getByText('在方格纸上手写实验记录倒不坏。'));

    expect(screen.getByTestId('archive-diary-ja')).toHaveTextContent('方眼紙に実験記録を手書きするのは悪くない。');
    expect(screen.getByTestId('archive-diary-zh')).toHaveTextContent('在方格纸上手写实验记录倒不坏。');
    expect(screen.getByTestId('archive-diary-meta')).toHaveTextContent('来自对话「实验记录」');
    expect(screen.getByTestId('archive-view').textContent).not.toContain('SECRET_PHRASE_NEVER_RENDER');
    // Diaries cannot be edited, pinned, or assigned a topic.
    expect(screen.queryByTestId('archive-edit-fact')).toBeNull();
    expect(screen.queryByTestId('archive-toggle-pin')).toBeNull();
    expect(screen.queryByTestId('archive-assign-topic')).toBeNull();

    // Two-step deletion confirmation explains permanent erasure and no rewrite.
    fireEvent.click(screen.getByTestId('archive-delete-record'));
    const confirm = screen.getByTestId('archive-delete-confirm');
    expect(confirm.textContent).toContain('确认删除这条日记？');
    expect(confirm.textContent).toContain('正文会永久删除');
    expect(confirm.textContent).toContain('不会为这段对话重新写一篇');
    fireEvent.click(screen.getByTestId('archive-delete-confirm-button'));

    await waitFor(() => {
      expect(screen.getByTestId('archive-diaries-empty')).toBeInTheDocument();
    });
    expect(screen.getByTestId('archive-tab-diary')).toHaveTextContent('日记（0）');
    expect(screen.getByTestId('archive-diaries-empty').textContent).toContain('聊够几轮');
    module.dispose();
  });

  it('shows empty user-retained topics, creates new or reuses duplicate topics, and deletes empty vs refuses non-empty topics', async () => {
    let topics = [
      { topic_id: 't1', display_label: '咖啡', fact_count: 1 },
      { topic_id: 't-empty', display_label: '想读的论文', fact_count: 0 },
    ];
    stubFetch((url, init) => {
      if (url.startsWith('/api/memory/topics?') && init?.method === 'POST') {
        const label = (JSON.parse(String(init.body)) as { display_label: string }).display_label.trim();
        const existing = topics.find((item) => item.display_label === label);
        if (existing) {
          return {
            status: 200,
            body: {
              topic_id: existing.topic_id,
              display_label: existing.display_label,
              normalized_label: existing.display_label.toLowerCase(),
              user_retained: true,
              scope: scopeBody(),
            },
          };
        }
        const created = { topic_id: 't-new', display_label: label, fact_count: 0 };
        topics = [...topics, created];
        return {
          status: 200,
          body: {
            topic_id: created.topic_id,
            display_label: created.display_label,
            normalized_label: created.display_label.toLowerCase(),
            user_retained: true,
            scope: scopeBody(),
          },
        };
      }
      if (url.startsWith('/api/memory/topics/t1?') && init?.method === 'DELETE') {
        return { status: 409, body: { detail: { code: 'topic_not_empty', message: 'not empty' } } };
      }
      if (url.startsWith('/api/memory/topics/t-empty?') && init?.method === 'DELETE') {
        topics = topics.filter((item) => item.topic_id !== 't-empty');
        return { status: 204, body: null };
      }
      if (url.startsWith('/api/memory/topics?')) {
        return { status: 200, body: { scope: scopeBody(), topics } };
      }
      if (url.startsWith('/api/memory/diaries?')) {
        return { status: 200, body: { scope: scopeBody(), diaries: [] } };
      }
      return viewResponder({ value: false })(url, init);
    });

    const module = createArchiveModule();
    await act(async () => { module.open(SCOPE); });
    render(<ArchiveView module={module} />);

    await waitFor(() => {
      expect(module.snapshot().topics.items.map((item) => `${item.displayLabel}（${item.activeFactCount}）`)).toContain('想读的论文（0）');
    });
    act(() => { module.dispatch({ type: 'set-topic', topicId: 't-empty' }); });
    expect(screen.getByTestId('archive-topic-select')).toHaveTextContent('想读的论文（0）');
    act(() => { module.dispatch({ type: 'set-topic', topicId: null }); });

    // Create a brand new topic.
    fireEvent.click(screen.getByTestId('archive-topic-create'));
    fireEvent.change(screen.getByTestId('archive-topic-create-input'), { target: { value: '临时灵感' } });
    fireEvent.click(screen.getByTestId('archive-topic-create-save'));
    await waitFor(() => {
      expect(screen.getByTestId('archive-topic-notice')).toHaveTextContent('已新建主题「临时灵感」。');
    });
    act(() => { module.dispatch({ type: 'set-topic', topicId: 't-new' }); });
    expect(screen.getByTestId('archive-topic-select')).toHaveTextContent('临时灵感（0）');

    // Create a duplicate topic name -> clarifies reuse of existing topic.
    fireEvent.click(screen.getByTestId('archive-topic-create'));
    fireEvent.change(screen.getByTestId('archive-topic-create-input'), { target: { value: '咖啡' } });
    fireEvent.click(screen.getByTestId('archive-topic-create-save'));
    await waitFor(() => {
      expect(screen.getByTestId('archive-topic-notice')).toHaveTextContent('已存在同名主题「咖啡」，已沿用原主题。');
    });

    // Select non-empty topic and try to delete -> human-readable refusal.
    act(() => { module.dispatch({ type: 'set-topic', topicId: 't1' }); });
    fireEvent.click(screen.getByTestId('archive-topic-delete'));
    await waitFor(() => {
      expect(screen.getByTestId('archive-topic-notice')).toHaveTextContent('「咖啡」里还有认知，不能直接删除');
    });

    // Stale count (local 0, server returns 409 topic_not_empty) -> also shows human-readable refusal.
    topics = [...topics, { topic_id: 't-stale', display_label: '刚新增认知', fact_count: 0 }];
    act(() => { module.dispatch({ type: 'set-topic', topicId: 't-empty' }); });
    fireEvent.click(screen.getByTestId('archive-topic-delete'));
    await waitFor(() => {
      expect(screen.getByTestId('archive-topic-notice')).toHaveTextContent('已删除空主题「想读的论文」。');
    });
    expect(module.snapshot().topics.items.some((item) => item.topicId === 't-empty')).toBe(false);
    expect(screen.getByTestId('archive-topic-select')).toHaveTextContent('全部主题');
    module.dispose();
  });

  it('refuses topic deletion with human copy when backend returns 409 topic_not_empty on stale count', async () => {
    stubFetch((url, init) => {
      if (url.startsWith('/api/memory/topics/t-stale?') && init?.method === 'DELETE') {
        return { status: 409, body: { detail: { code: 'topic_not_empty', message: 'not empty' } } };
      }
      if (url.startsWith('/api/memory/topics?')) {
        return { status: 200, body: { scope: scopeBody(), topics: [{ topic_id: 't-stale', display_label: '刚新增认知', fact_count: 0 }] } };
      }
      if (url.startsWith('/api/memory/diaries?')) {
        return { status: 200, body: { scope: scopeBody(), diaries: [] } };
      }
      return viewResponder({ value: false })(url, init);
    });
    const module = createArchiveModule();
    await act(async () => { module.open(SCOPE); });
    render(<ArchiveView module={module} />);
    await waitFor(() => {
      expect(module.snapshot().topics.items).toHaveLength(1);
    });
    act(() => { module.dispatch({ type: 'set-topic', topicId: 't-stale' }); });
    fireEvent.click(screen.getByTestId('archive-topic-delete'));
    await waitFor(() => {
      expect(screen.getByTestId('archive-topic-notice')).toHaveTextContent('「刚新增认知」里还有认知，不能直接删除');
    });
    module.dispose();
  });
});
