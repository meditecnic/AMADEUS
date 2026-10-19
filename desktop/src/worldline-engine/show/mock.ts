// Local mock of the world-line switch backend. Nothing here talks to a network.
import type { Scenario, Outcome } from './timeline';
import { MOCK_DELAY, MOCK_OUTCOME } from './timeline';

export interface MockResult {
  outcome: Outcome;
  reading: string; // the reading the "backend" reports after the call
}

export function mockSwitch(scenario: Scenario, origin: string, target: string, signal: AbortSignal): Promise<MockResult> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('aborted', 'AbortError'));
      return;
    }
    const id = window.setTimeout(() => {
      signal.removeEventListener('abort', onAbort);
      const outcome = MOCK_OUTCOME[scenario];
      resolve({ outcome, reading: outcome === 'success' ? target : origin });
    }, MOCK_DELAY[scenario] * 1000);
    const onAbort = (): void => {
      window.clearTimeout(id);
      reject(new DOMException('aborted', 'AbortError'));
    };
    signal.addEventListener('abort', onAbort, { once: true });
  });
}
