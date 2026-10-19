import type { FrameState, Outcome } from '../worldline-engine';
import { DIVERGENCE } from './Nixie';

export type Worldline = 'steins_gate' | 'beta';

/** Request ownership outlives a presentation. A detached canvas cannot publish. */
export class ShiftTransaction {
  readonly outcome: Promise<Outcome>;
  result: Outcome | null = null;
  error: string | null = null;
  committed = false;
  private resolve!: (outcome: Outcome) => void;
  private sent = false;
  private disposed = false;
  private generation = 0;
  private attached = false;

  constructor(
    readonly id: string,
    readonly from: Worldline,
    readonly to: Worldline,
    private publish: () => void,
  ) {
    this.outcome = new Promise(resolve => { this.resolve = resolve; });
  }

  start(send: (to: Worldline, requestId: string) => boolean): void {
    if (this.sent || this.disposed) return;
    this.sent = true;
    if (!send(this.to, this.id)) this.accept(this.id, this.to, 'fail', 'socket_not_ready');
  }

  accept(id: string, to: Worldline, outcome: Outcome, error?: string): boolean {
    if (this.disposed || id !== this.id || to !== this.to || this.result !== null) return false;
    this.result = outcome;
    this.error = outcome === 'fail' ? error || 'switch_failed' : null;
    this.resolve(outcome);
    return true;
  }

  attach(): number {
    this.attached = true;
    return ++this.generation;
  }

  observe(lease: number, frame: FrameState): void {
    if (this.disposed || !this.attached || lease !== this.generation || this.committed || this.result !== 'success') return;
    // Eight cells, including the decimal cell. Text alone can match while rolling.
    if (frame.readout.locked.length !== 8 || !frame.readout.locked.every(Boolean)) return;
    if (frame.readout.shown !== DIVERGENCE[this.to]) return;
    this.committed = true;
    this.publish();
  }

  detach(lease: number): void {
    if (lease === this.generation) this.attached = false;
  }

  dispose(): void {
    this.disposed = true;
    this.attached = false;
  }
}
