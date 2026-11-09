// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { createMockRuntime } from './runtime';

const url = (path: string) => new URL(path, 'http://localhost:1420');

describe('dev mock data routes', () => {
  it('answers only /health, the data routes and the diagnostics bundle while the store is corrupt', () => {
    const mock = createMockRuntime();
    const corrupt = (path: string, method = 'GET') => mock.rest(url(`${path}${path.includes('?') ? '&' : '?'}scenario=data-corrupt`), method);
    expect(corrupt('/api/auth/state')).toEqual({ status: 503, body: { detail: { code: 'data_unavailable' } } });
    expect(corrupt('/api/conversations?session_id=s').status).toBe(503);
    expect(corrupt('/health').status).toBe(200);
    expect(corrupt('/api/data/status').body).toMatchObject({ status: 'corrupt', backups: expect.any(Array) });
    expect(corrupt('/api/diagnostics/bundle', 'POST').body).toMatchObject({ path: expect.stringMatching(/diagnostics\\amadeus-diagnostics-.+\.zip$/) });
    expect(mock.rest(url('/api/auth/state'), 'GET').body).toEqual({ passcode_set: false });
  });

  it('keeps a passcode set on the system page for the next login, and turns it off only with it', () => {
    const mock = createMockRuntime();
    const put = (current: string | null, next: string | null) => mock.rest(url('/api/auth/passcode'), 'PUT', { current, new: next });
    const login = (passcode: string) => mock.rest(url('/api/auth/login'), 'POST', { passcode }).status;
    expect(put(null, '  ').status).toBe(422);
    expect(put(null, 'lab-member-001').status).toBe(200);
    expect(mock.rest(url('/api/auth/state'), 'GET').body).toEqual({ passcode_set: true });
    expect(login('wrong')).toBe(401);
    expect(login('lab-member-001')).toBe(200);
    expect(put('wrong', null).body).toEqual({ detail: { code: 'invalid_passcode' } });
    expect(put('lab-member-001', null).status).toBe(200);
    expect(mock.rest(url('/api/auth/state'), 'GET').body).toEqual({ passcode_set: false });
    // a scenario with a passcode starts with the scenario's password as the passcode
    expect(mock.rest(url('/api/auth/state?scenario=login-passcode'), 'GET').body).toEqual({ passcode_set: true });
  });

  it('makes, restores to and deletes backups as the backend does', () => {
    const mock = createMockRuntime();
    type Row = { id: string; can_restore: boolean; reason: string };
    const list = () => mock.rest(url('/api/data/backups'), 'GET').body as Row[];
    const before = list();
    const made = mock.rest(url('/api/data/backups'), 'POST').body as { id: string };
    expect(made.id).toMatch(/^\d{8}T\d{12}Z-manual$/);
    expect(list()[0]).toMatchObject({ id: made.id, reason: 'manual', can_restore: true });
    const broken = before.find(row => !row.can_restore)!;
    expect(mock.rest(url(`/api/data/backups/${broken.id}/restore`), 'POST').status).toBe(400);
    expect(mock.rest(url(`/api/data/backups/${made.id}/restore`), 'POST').body).toEqual({ restart_required: true });
    expect(mock.rest(url(`/api/data/backups/${made.id}`), 'DELETE')).toEqual({ status: 409, body: { detail: { code: 'restore_pending' } } });
    expect(mock.rest(url(`/api/data/backups/${broken.id}`), 'DELETE').status).toBe(200);
    expect(list().some(row => row.id === broken.id)).toBe(false);
  });

  it('serves the export as a zip under the name the backend gives it, and memory only as JSON', () => {
    const mock = createMockRuntime();
    const reply = mock.rest(url('/api/data/export?kind=conversations&format=md&worldline=sg'), 'GET');
    expect(reply.headers).toMatchObject({ 'Content-Type': 'application/zip' });
    expect(reply.headers?.['Content-Disposition']).toMatch(/^attachment; filename="amadeus-\d{8}T\d{6}Z-conversations-sg\.zip"$/);
    expect(Array.from(reply.raw!.slice(0, 4))).toEqual([0x50, 0x4b, 0x03, 0x04]);
    expect(mock.rest(url('/api/data/export?kind=memory&format=md'), 'GET').status).toBe(422);
  });

  it('estimates usage per currency and names what it could not price', () => {
    const mock = createMockRuntime();
    const report = mock.rest(url('/api/data/usage?range=month&tz=480'), 'GET').body as {
      totals_by_currency: Record<string, number>; unreported_calls: number; unpriced_models: unknown[]; tz: number;
    };
    expect(Object.keys(report.totals_by_currency).sort()).toEqual(['CNY', 'USD']);
    expect(report.unreported_calls).toBeGreaterThan(0);
    expect(report.unpriced_models).toEqual([{ provider_id: 'openai-compatible', model_id: 'local-model' }]);
    expect(report.tz).toBe(480);
    expect(mock.rest(url('/api/data/usage?range=conversation'), 'GET').status).toBe(422);
  });
});
