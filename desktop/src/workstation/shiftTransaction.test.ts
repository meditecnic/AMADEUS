import { describe, expect, it, vi } from 'vitest';
import { ShiftTransaction } from './shiftTransaction';
import { evaluateShow, parseReading, successEndFrame } from '../worldline-engine';

function setup() {
  const commit = vi.fn();
  const send = vi.fn(() => true);
  const run = new ShiftTransaction('run-1', 'steins_gate', 'beta', commit);
  const lease = run.attach();
  run.start(send);
  return { run, lease, send, commit };
}
const target = parseReading('1.129848')!;
const final = successEndFrame(target);

describe('shift transaction publication', () => {
  it('sends once and requires matching readiness plus final digit lock', async () => {
    const { run, lease, send, commit } = setup();
    run.start(send);
    run.observe(lease, final);
    expect(commit).not.toHaveBeenCalled();
    expect(run.accept('old', 'beta', 'success')).toBe(false);
    expect(run.accept('run-1', 'steins_gate', 'success')).toBe(false);
    run.accept('run-1', 'beta', 'success');
    await expect(run.outcome).resolves.toBe('success');
    const ctx = { origin: parseReading('1.048596')!, target, outcome: 'success' as const, waitT: 0, resultT: 0 };
    run.observe(lease, evaluateShow(6.15, ctx));
    run.observe(lease, evaluateShow(7.6, ctx));
    run.observe(lease, successEndFrame(ctx.origin));
    expect(commit).not.toHaveBeenCalled();
    run.observe(lease, evaluateShow(7.64, ctx));
    run.observe(lease, final);
    expect(commit).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledExactlyOnceWith('beta', 'run-1');
  });

  it.each(['unavailable', 'context-lost', 'low-fps', 'reduced-motion'])('keeps the same outcome across %s presentation replacement', async () => {
    const { run, lease, send, commit } = setup();
    run.detach(lease);
    const replacement = run.attach();
    run.accept('run-1', 'beta', 'success');
    run.observe(lease, final);
    expect(commit).not.toHaveBeenCalled();
    run.observe(replacement, final);
    run.observe(replacement, final);
    expect(commit).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it('failure is terminal, keeps source and never publishes success', async () => {
    const { run, lease, commit } = setup();
    run.accept('run-1', 'beta', 'fail', 'switch_failed');
    expect(run.accept('run-1', 'beta', 'success')).toBe(false);
    run.observe(lease, final);
    await expect(run.outcome).resolves.toBe('fail');
    expect(run.error).toBe('switch_failed');
    expect(commit).not.toHaveBeenCalled();
  });

  it('detaches before lock with zero late commits but retains the server result for reconciliation', async () => {
    const { run, lease, commit } = setup();
    run.detach(lease);
    run.accept('run-1', 'beta', 'success');
    run.observe(lease, final);
    await expect(run.outcome).resolves.toBe('success');
    expect(run.result).toBe('success');
    expect(commit).not.toHaveBeenCalled();
    run.dispose();
    expect(run.accept('run-1', 'beta', 'fail')).toBe(false);
  });

  it('settles a synchronous send failure without claiming success', async () => {
    const run = new ShiftTransaction('offline', 'steins_gate', 'beta', vi.fn());
    run.start(() => false);
    await expect(run.outcome).resolves.toBe('fail');
    expect(run.error).toBe('socket_not_ready');
  });
});
