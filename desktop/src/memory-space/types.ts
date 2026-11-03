export type IdentityMode = 'self' | 'okabe';
export type Worldline = 'steins_gate' | 'beta';

// GET /api/memory/chart: every active cognition and fragment of one scope, flat.
export type ChartTopic = { id: string; label: string; created_at: string; fact_count: number };
export type ChartFact = {
  id: string;
  label: string;
  topic_id: string | null;
  created_at: string;
  updated_at: string;
  is_pinned: boolean;
  evidence_count: number;
  version_count: number;
  resident: boolean;
};
export type ChartExperience = {
  id: string;
  label: string;
  created_at: string;
  updated_at: string;
  is_pinned: boolean;
  expires_at: string | null;
};
export type MemoryChart = {
  scope: { session_id: string; worldline: Worldline; identity_mode: IdentityMode };
  generated_at: string;
  origin_at: string | null;
  topics: ChartTopic[];
  facts: ChartFact[];
  experiences: ChartExperience[];
  counts: { facts: number; experiences: number; topics: number };
  truncated: boolean;
};
