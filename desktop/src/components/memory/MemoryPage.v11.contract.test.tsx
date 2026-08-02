/**
 * MEMORY-V11 S0 — structure smoke only (REWORK).
 *
 * These checks prove the current tree still lacks the S4 Memory IA modules.
 * They are NOT acceptance evidence for U01 / U04 / D26.
 *
 * S4 MUST add (separate files, behavior-level):
 *   - rendered MemoryPage identity default from activeConversationIdentityMode
 *   - interaction: pin/edit/delete/topic/search (no LLM)
 *   - request payload scope + epoch discard of stale responses
 *   - dialog behavior: edit draft-on-fail, delete second confirm, conversation
 *     delete with delete_derived_memory true/false payloads
 *   - U01/U04/D26 named acceptance tests on the above behaviors
 */
import { describe, expect, it } from 'vitest';
import * as fs from 'node:fs';
import * as path from 'node:path';

const componentsDir = path.resolve(__dirname, '..');
const settingsPath = path.join(componentsDir, 'SettingsModal.tsx');
const workstationPath = path.join(componentsDir, 'Workstation.tsx');
const memoryLedgerPath = path.join(componentsDir, 'MemoryLedger.tsx');
const memoryPagePath = path.join(__dirname, 'MemoryPage.tsx');
const memoryDir = __dirname;

function read(filePath: string): string {
  return fs.readFileSync(filePath, 'utf8');
}

describe('MEMORY-V11 S0 structure smoke (not U01/U04/D26 acceptance)', () => {
  it('smoke: fixed memory component directory is prepared for S4 split', () => {
    expect(fs.existsSync(memoryDir)).toBe(true);
  });

  it('smoke: MemoryPage controller module not yet present (S4 target)', () => {
    expect(fs.existsSync(memoryPagePath)).toBe(false);
  });

  it('smoke: SettingsModal still lacks activeConversationIdentityMode prop name', () => {
    const source = read(settingsPath);
    expect(source).not.toMatch(/activeConversationIdentityMode/);
    // Default new-conversation identity remains the existing prop.
    expect(source).toMatch(/identityMode\?:/);
  });

  it('smoke: Workstation still uses window.confirm for conversation delete', () => {
    const source = read(workstationPath);
    expect(source).toMatch(/window\.confirm\(/);
    expect(source).not.toMatch(/delete_derived_memory|deleteDerivedMemory/);
  });

  it('smoke: legacy MemoryLedger still owns promote/reclassify product surface', () => {
    const source = read(memoryLedgerPath);
    expect(source).toMatch(/promote|reclassify|写入跨线|确认为真实信息/);
  });
});
