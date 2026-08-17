export type IdentityMode = 'self' | 'okabe';
export type Worldline = 'steins_gate' | 'beta';

export type ProjectionNode =
  | {
      kind: 'continuity_hub';
      projection_id: string;
      label_primary: string;
      label_secondary: string;
    }
  | {
      kind: 'topic';
      projection_id: string;
      topic_id: string;
      label: string;
      fact_count: number;
      experience_count: number;
    }
  | {
      kind: 'fact';
      projection_id: string;
      fact_id: string;
      label: string;
      is_pinned: boolean;
      updated_at: string;
      topic_id: string | null;
    }
  | {
      kind: 'experience';
      projection_id: string;
      experience_id: string;
      label: string;
      is_pinned: boolean;
      created_at: string;
      updated_at: string;
      expires_at: string | null;
      is_expired: boolean;
      conversation_id: string;
    };

export type ProjectionEdge = {
  kind: 'hub_to_anchor' | 'has_topic' | 'hub_to_evidence';
  from: string;
  to: string;
};

export type MemoryProjection = {
  scope: { session_id: string; worldline: Worldline; identity_mode: IdentityMode };
  view: 'overview';
  projection_version: string;
  generated_at: string;
  criteria: {
    query: string | null;
    kinds: string[];
    topic_id: string | null;
    pinned_only: boolean;
    updated_from: string | null;
    updated_to: string | null;
  };
  center: {
    kind: 'continuity_hub';
    projection_id: string;
    label_primary: string;
    label_secondary: string;
  };
  composition: {
    active_facts: number;
    active_experiences: number;
    eligible_topics: number;
    latest_memory_change_at: string | null;
    person_anchors_supported: boolean;
  };
  budgets: { nodes: number; edges: number; hard_max_nodes: number; hard_max_edges: number };
  eligible: { nodes: number; edges: number; records: number; results: number };
  shown: { nodes: number; edges: number; records: number; results: number };
  truncated: { nodes: boolean; edges: boolean; records: boolean; results: boolean };
  empty: boolean;
  result_ids: string[];
  nodes: ProjectionNode[];
  edges: ProjectionEdge[];
};

export type Point3 = { x: number; y: number; z: number };

export type DisplayNode = {
  id: string;
  kind: ProjectionNode['kind'];
  label: string;
  radius: number;
  position: Point3;
  node: ProjectionNode;
};
