// Cliente tipado de la API de VerifiCargo (app/api.py).

export type Outcome = "RESOLVED" | "CLARIFY" | "ESCALATED" | "DENIED" | "ABSTAINED" | "BLOCKED" | "CANCELLED"

export interface Scenario {
  id: string
  path: "normal" | "ambiguous" | "human" | "attack"
  title: string
  lang: "es" | "pt"
  customer: string
  message: string
  hint: string
}

export interface SessionInfo {
  valid: boolean
  customer_id?: string
  country?: string
  language?: string
  auth_level?: "low" | "high"
  high_active?: boolean
  seconds_remaining?: number
  error?: string
}

export interface Action {
  action: string
  verified?: boolean
  evidence_ids?: string[]
  case_id?: string
  ticket_id?: string
  reason?: string
}

export interface TurnResult {
  outcome: Outcome
  reply: string
  language: string
  latency_ms: number
  states: string[]
  intent: {
    intent: string
    confidence: number
    prediction_set: string[]
    groups: string[]
    route: string
    reason: string
  } | null
  extraction_source: string | null
  extracted: Record<string, unknown>
  policy_trace: string[]
  escalation_reasons: string[]
  actions_taken: Action[]
  actions_not_taken: Action[]
  evidence: { id: string; source: string; fact: string }[]
  candidates: number
  awaiting: "intent" | "details" | "confirmation" | null
  options: { key: string; label: string }[]
  grounding_violations: string[]
  handoff: { handoff_id: string | null; queued: boolean; error?: string } | null
  error: string | null
}

export interface HandoffPackage {
  handoff_id: string
  created_at: string
  language: string
  country: string
  customer_ref: string
  request_summary: string
  trigger_rules: string[]
  policy_trace: string[]
  verified_facts: { fact: string; source: string; evidence_id: string }[]
  customer_claims_unverified: { claim: string }[]
  actions_taken: { action: string; verified: boolean; evidence_ids: string[] }[]
  actions_not_taken: { action: string; reason: string }[]
  open_questions: string[]
  sla: { regulatory_deadline: string | null; days_remaining: number | null; provenance: string }
  priority: "low" | "normal" | "high" | "critical"
}

export interface Txn {
  transaction_id: string
  date: string
  merchant: string
  category: string | null
  amount: number
  currency: string
  status: string
  channel: string
}

export type QueueStatus = "pending" | "info_requested" | "closed"

export interface QueueItem {
  status: "pending" | "info_requested" | "approved" | "rejected"
  queued_at: string
  resolved_at?: string
  agent?: string
  agent_note?: string
  can_approve: boolean
  info_request?: { question: string; asked_at: string }
  customer_replies?: { at: string; question: string; text: string }[]
  result?: { action: string; case_id?: string; existing_case_id?: string; verified: boolean; evidence_ids: string[] }
  package: HandoffPackage
}

export interface AgentQuestion {
  handoff_id: string
  question: string
  asked_at: string
}

export type Scorecard = Record<string, unknown>

export interface Combined {
  rate: number | null
  num: number | null
  den: number | null
  per_run_den?: number
  range: [number, number] | null
  runs?: number
}

export interface RunSummary {
  runs: number
  n_cases: number
  metrics: Record<string, Combined>
  latency: Record<string, number>
  by_language?: Record<string, Record<string, Combined | number>>
  by_country?: Record<string, Record<string, Combined | number>>
  by_segment?: Record<string, Record<string, Combined | number>>
}

export interface EvalData {
  v3?: { baseline?: RunSummary; proposed?: RunSummary }
  v1?: { baseline?: Scorecard; proposed?: Scorecard }
  v2?: { baseline?: Scorecard; proposed?: Scorecard }
  classifier?: {
    arms: Record<string, { macro_f1: number; macro_f1_es: number; macro_f1_pt: number }>
    acceptance: { deployed: string; gain_macro_f1: number; accepted: boolean }
    alpha_sweep?: { alpha: number; coverage: number; decides: number; clarifies: number; routing_errors: number }[]
  }
}

async function call<T>(path: string, init?: RequestInit, token?: string): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? `HTTP ${res.status}`)
  }
  return res.json()
}

export const api = {
  health: () => call<{ ok: boolean; llm_provider: string; policy_version: string }>("/api/health"),
  scenarios: () => call<Scenario[]>("/api/scenarios"),
  start: (scenario_id: string) =>
    call<{ conversation_id: string; scenario: Scenario; session: SessionInfo }>("/api/conversations", {
      method: "POST",
      body: JSON.stringify({ scenario_id }),
    }),
  send: (cid: string, message: string) =>
    call<{ turn: TurnResult; session: SessionInfo }>(`/api/conversations/${cid}/messages`, {
      method: "POST",
      body: JSON.stringify({ message }),
    }),
  stepUp: (cid: string, otp: string) =>
    call<{ session: SessionInfo }>(`/api/conversations/${cid}/step-up`, {
      method: "POST",
      body: JSON.stringify({ otp }),
    }),
  transactions: (cid: string) => call<Txn[]>(`/api/conversations/${cid}/transactions`),
  questions: (cid: string) =>
    call<AgentQuestion[]>(`/api/conversations/${cid}/questions`),
  replyToAgent: (cid: string, handoffId: string, message: string) =>
    call<{ ok: boolean; status: string }>(`/api/conversations/${cid}/handoffs/${handoffId}/reply`, {
      method: "POST",
      body: JSON.stringify({ message }),
    }),
  agentLogin: (access_code: string) =>
    call<{ token: string }>("/api/agent/login", { method: "POST", body: JSON.stringify({ access_code }) }),
  handoffs: (token: string, status: QueueStatus) =>
    call<QueueItem[]>(`/api/handoffs?status=${status}`, undefined, token),
  resolve: (token: string, id: string, decision: string, note: string) =>
    call<{ ok: boolean; item: QueueItem }>(
      `/api/handoffs/${id}/resolve`,
      { method: "POST", body: JSON.stringify({ decision, note }) },
      token,
    ),
  evaluation: () => call<EvalData>("/api/eval"),
}
