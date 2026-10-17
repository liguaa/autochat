export type Message = { id: string; role: "me" | "contact"; state: "active" | "revoked"; text?: string; recall_id: string | null };
export type Hypothesis = {
  id: string;
  hypothesis: string;
  status?: string;
  support_probability?: number | null;
  evidence_status?: string;
  evidence_refs?: {
    source_type: string;
    source_id: number | string;
    quote: string;
  }[];
  missing_context?: string[];
  evidence_probabilities?: Record<string, number>;
};
export type Candidate = { style?: string; text: string };
export type ChoiceAnswer = {
  choice?: string;
  probabilities?: Record<string, number>;
};
export type ScorecardSignal = {
  label?: string;
  score?: number;
  reason?: string;
};
export type Output = {
  context_version: string;
  pipeline_mode: string;
  request_id?: string;
  config_fingerprint?: string;
  subtext_hypotheses?: Hypothesis[];
  dynamic_analysis?: {
    stages?: Record<string, { status: string; error?: string | null }>;
    fallback_reason?: string | null;
  };
  result?: {
    analysis?: Record<string, string>;
    scorecard?: {
      emotion?: ScorecardSignal;
      intent?: ScorecardSignal;
    };
    candidates?: Candidate[];
    needs_user_review?: boolean;
    action?: string;
  } | null;
  jev?: {
    answers?: {
      emotion?: ChoiceAnswer;
      intent?: ChoiceAnswer;
      relationship_tension?: { score?: number };
      [key: string]: unknown;
    };
  } | null;
  jev_reply_direction?: {
    choice?: string;
    probabilities?: Record<string, number>;
  };
  history_guard_applied?: boolean;
};
export type Card = {
  state: "progress" | "complete" | "error";
  context_version: string;
  output?: Output;
  message?: string;
  source_message_ids?: Record<string, string>;
  session_revision?: number;
  config_revision?: number;
};
export type Snapshot = {
  schema_version: number;
  sequence: number;
  session_revision: number;
  config_revision: number;
  active_message_count: number;
  facts_pending_review: boolean;
  facts_reviewed_indices: number[];
  ending: boolean;
  selected_id: string;
  sessions: { id: string; name: string; count: number }[];
  messages: Message[];
  cards: Record<string, Card>;
  context_version: string | null;
  confirmed_facts: string[];
  mode: string;
  style: string;
  samples: string[];
  active: {
    session_id: string;
    index: number;
    message_id: string;
    request_id: string;
    context_version: string;
    mode: string;
  } | null;
  status: string;
  storage_location: string;
  session_storage_location?: string;
  session_storage_errors?: string[];
  session_storage_pending?: boolean;
  credentials: { deepseek: boolean; typesafe: boolean };
  styles: { id: string; label: string }[];
  persona_directory?: string;
  persona_errors?: string[];
  persona_preview?: { name: string; label: string; description: string; instructions: string; sha256: string } | null;
};
export type Adapter = {
  snapshot(): Promise<Snapshot>;
  invoke(action: string, payload: Record<string, unknown>): Promise<any>;
  subscribe(fn: () => void): () => void;
  demo: boolean;
};
