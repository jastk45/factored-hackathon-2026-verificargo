// Cliente tipado de la API de VerifiCargo (app/api.py).

export type Outcome = "RESOLVED" | "CLARIFY" | "ESCALATED" | "DENIED" | "ABSTAINED" | "BLOCKED"

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
  handoff: HandoffPackage | null
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

export interface QueueItem {
  status: string
  queued_at: string
  resolved_at?: string
  agent_note?: string
  package: HandoffPackage
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
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
  handoffs: (status: "pending" | "resolved") => call<QueueItem[]>(`/api/handoffs?status=${status}`),
  resolve: (id: string, decision: string, note: string) =>
    call<{ ok: boolean }>(`/api/handoffs/${id}/resolve`, {
      method: "POST",
      body: JSON.stringify({ decision, note }),
    }),
  evaluation: () => call<Record<string, any>>("/api/eval"),
}
