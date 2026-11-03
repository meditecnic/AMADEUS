import { parseMicros } from './chartTime';

// Two server -> client frames mark a turn in the transcript: `memory.referenced`
// (records put in front of her for the turn) and `memory.recorded` (what the
// ingestion job wrote from the user's line). Both live in front-end memory only.

export type MemoryRefItem = {
  kind: 'fact' | 'experience';
  id: string;
  via: 'supplement' | 'episodic' | 'recall';
  label: string;
  createdAt: string;
};
export type MemoryRefs = { items: MemoryRefItem[]; at: number };
export type MemoryChange = {
  kind: 'fact' | 'experience';
  id: string;
  change: 'create' | 'refine' | 'supersede';
  label: string;
  createdAt: string;
};

export type ParsedReferenced = { conversationId: string; turnId: string; generation: number; items: MemoryRefItem[] };
export type ParsedRecorded = { conversationId: string; turnId: string; changes: MemoryChange[] };

export const MAX_REFERENCED = 32;

const KINDS: readonly unknown[] = ['fact', 'experience'];
const VIAS: readonly unknown[] = ['supplement', 'episodic', 'recall'];
const CHANGES: readonly unknown[] = ['create', 'refine', 'supersede'];

const isText = (value: unknown): value is string => typeof value === 'string' && value.length > 0;
const isRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value);

// Every field must be present with its documented type; one bad row drops the frame.
export function parseMemoryReferenced(frame: unknown): ParsedReferenced | null {
  if (!isRecord(frame)) return null;
  const { conversation_id: conversationId, turn_id: turnId, generation, items } = frame;
  if (!isText(conversationId) || !isText(turnId) || typeof generation !== 'number' || !Number.isInteger(generation)) return null;
  if (!Array.isArray(items) || items.length === 0) return null;
  const seen = new Set<string>();
  const rows: MemoryRefItem[] = [];
  for (const row of items) {
    if (!isRecord(row) || !KINDS.includes(row.kind) || !VIAS.includes(row.via) || !isText(row.id)
      || typeof row.label !== 'string' || parseMicros(row.created_at) === null) return null;
    const key = `${row.kind}:${row.id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({
      kind: row.kind as MemoryRefItem['kind'], id: row.id, via: row.via as MemoryRefItem['via'],
      label: row.label, createdAt: row.created_at as string,
    });
  }
  return { conversationId, turnId, generation, items: rows.slice(0, MAX_REFERENCED) };
}

export function parseMemoryRecorded(frame: unknown): ParsedRecorded | null {
  if (!isRecord(frame)) return null;
  const { conversation_id: conversationId, turn_id: turnId, source_message_id: source, changes } = frame;
  if (!isText(conversationId) || !isText(turnId) || typeof source !== 'number' || !Number.isFinite(source)) return null;
  if (!Array.isArray(changes) || changes.length === 0) return null;
  const rows: MemoryChange[] = [];
  for (const row of changes) {
    if (!isRecord(row) || !KINDS.includes(row.kind) || !CHANGES.includes(row.change) || !isText(row.id)
      || typeof row.label !== 'string' || parseMicros(row.created_at) === null) return null;
    rows.push({
      kind: row.kind as MemoryChange['kind'], id: row.id, change: row.change as MemoryChange['change'],
      label: row.label, createdAt: row.created_at as string,
    });
  }
  return { conversationId, turnId, changes: rows };
}

type Markable = { role: 'user' | 'assistant'; turnId?: string; memoryRefs?: MemoryRefs; memoryRecorded?: MemoryChange[] };

/** The assistant line of the turn carries the references; no such line drops them. */
export function attachReferences<T extends Markable>(messages: T[], turnId: string, refs: MemoryRefs): T[] {
  const index = messages.findIndex((m) => m.role === 'assistant' && m.turnId === turnId);
  if (index < 0) return messages;
  return messages.map((m, i) => (i === index ? { ...m, memoryRefs: refs } : m));
}

/** The user line just before the turn's assistant line carries what was recorded; several frames merge by id. */
export function attachRecorded<T extends Markable>(messages: T[], turnId: string, changes: MemoryChange[]): T[] {
  const assistant = messages.findIndex((m) => m.role === 'assistant' && m.turnId === turnId);
  if (assistant < 0) return messages;
  let target = -1;
  for (let i = assistant - 1; i >= 0; i -= 1) {
    if (messages[i].role === 'user') { target = i; break; }
  }
  if (target < 0) return messages;
  const merged = new Map((messages[target].memoryRecorded ?? []).map((c) => [c.id, c] as const));
  for (const change of changes) merged.set(change.id, change);
  return messages.map((m, i) => (i === target ? { ...m, memoryRecorded: [...merged.values()] } : m));
}

/** Groups for the user-line marks: "记下了" counts creates, "更新了" counts refine + supersede. */
export function recordedGroups(changes: readonly MemoryChange[] | undefined) {
  const created = (changes ?? []).filter((c) => c.change === 'create');
  const updated = (changes ?? []).filter((c) => c.change !== 'create');
  return { created, updated };
}
