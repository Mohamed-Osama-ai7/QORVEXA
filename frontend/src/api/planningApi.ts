/**
 * Typed client for QORVEXA Industrial Decision Studio API.
 * Uses `apiFetch` with fallback to in-memory governed session state when offline.
 */

import { apiFetch } from './http'

export interface ExtractedClaim {
  key: string
  value: string | number
  quote: string
}

export interface SessionSummary {
  id: string
  title: string
  created_by: string
  created_at: string
}

export interface ResolvedFact {
  key: string
  value: string
  state: 'CONFIRMED' | 'ASSUMED'
  claim_seq: number
  basis: string
}

export interface Issue {
  code: string
  key: string
  message: string
  question?: string
  can_assume?: boolean
  candidates?: string[]
  claim_seqs?: number[]
}

export interface FormulationState {
  status: 'READY' | 'NEEDS_CLARIFICATION' | 'CONTRADICTED' | 'UNSUPPORTED' | 'INVALID_MODEL'
  facts: ResolvedFact[]
  issues: Issue[]
  ledger_head: string
  state_sha256: string
  units_status: string
  unit_report_sha256: string
}

export interface IngestResponse {
  source_sha256: string
  accepted_count: number
  rejected_count: number
  accepted: ExtractedClaim[]
  rejected: [ExtractedClaim, string][]
  state: FormulationState
}

export interface ChangeSpec {
  kind: 'capacity' | 'demand_bounds' | 'value'
  resource_id?: string
  product_id?: string
  capacity?: string
  min_quantity?: string
  max_quantity?: string
  clear_max?: boolean
  value_per_unit?: string
}

export interface ScenarioSpec {
  scenario_id: string
  description?: string
  changes: ChangeSpec[]
  parent_scenario_id?: string | null
}

export interface ScenarioComparison {
  scenario_id: string
  status: string
  baseline_status: string
  objective?: {
    mode: string
    currency: string
    baseline: string | number
    scenario: string | number
    absolute_change: string | number
    effect: 'IMPROVED' | 'DETERIORATED' | 'UNCHANGED'
  }
}

export interface DecisionBrief {
  schema_version: string
  brief_sha256: string
  executive_summary: string[]
  baseline_id: string
  baseline_status: string
  objective_mode: string | null
  currency: string | null
  comparison?: {
    baseline_id: string
    mode: string
    currency: string
    compared: ScenarioComparison[]
  }
  recommendation: {
    status: 'RECOMMENDED' | 'CONDITIONALLY_RECOMMENDED' | 'NOT_RECOMMENDED' | 'INFEASIBLE'
    selected_scenario_id?: string | null
    headline: string
    reasons: string[]
    required_acknowledgements: string[]
  }
}

export interface DecisionRecord {
  seq: number
  session_id: string
  brief_sha256: string
  verdict: 'APPROVED' | 'REJECTED'
  scenario_id: string | null
  approver_id: string
  acknowledged_assumptions: string[]
  reason: string
  recorded_at: string
  record_sha256: string
}

export interface DecisionHistory {
  records: DecisionRecord[]
}

export async function listSessions(): Promise<SessionSummary[]> {
  try {
    const res = await apiFetch<{ data: SessionSummary[] }>('/api/v1/industrial/sessions')
    return res.data
  } catch {
    return [
      {
        id: 'DEMO-MIX-2026',
        title: 'Q4 Precision Mix Optimization',
        created_by: 'lead_engineer',
        created_at: new Date().toISOString(),
      },
    ]
  }
}

export async function createSession(sessionId: string, title: string): Promise<{ session_id: string; title: string }> {
  const res = await apiFetch<{ data: { session_id: string; title: string } }>('/api/v1/industrial/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, title }),
  })
  return res.data
}

export async function getSessionState(sessionId: string): Promise<FormulationState> {
  const res = await apiFetch<{ data: FormulationState }>(`/api/v1/industrial/sessions/${sessionId}/state`)
  return res.data
}

export async function ingestText(
  sessionId: string,
  text: string,
  claims?: ExtractedClaim[]
): Promise<IngestResponse> {
  const res = await apiFetch<{ data: IngestResponse }>(`/api/v1/industrial/sessions/${sessionId}/ingest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, claims }),
  })
  return res.data
}

export async function confirmClaims(sessionId: string, keys: string[]): Promise<FormulationState> {
  const res = await apiFetch<{ data: FormulationState }>(`/api/v1/industrial/sessions/${sessionId}/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keys }),
  })
  return res.data
}

export async function assertFact(sessionId: string, key: string, value: string | number): Promise<FormulationState> {
  const res = await apiFetch<{ data: FormulationState }>(`/api/v1/industrial/sessions/${sessionId}/assert`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key, value }),
  })
  return res.data
}

export async function assumeDefaults(sessionId: string, rationale: string): Promise<FormulationState> {
  const res = await apiFetch<{ data: FormulationState }>(`/api/v1/industrial/sessions/${sessionId}/assume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rationale }),
  })
  return res.data
}

export async function buildFormulation(sessionId: string): Promise<unknown> {
  const res = await apiFetch<{ data: unknown }>(`/api/v1/industrial/sessions/${sessionId}/build`, {
    method: 'POST',
  })
  return res.data
}

export async function runDecision(
  sessionId: string,
  scenarios: ScenarioSpec[] = [],
  paretoMetrics: string[] = ['objective', 'total_volume']
): Promise<DecisionBrief> {
  const res = await apiFetch<{ data: DecisionBrief }>(`/api/v1/industrial/sessions/${sessionId}/decide`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scenarios, pareto_metrics: paretoMetrics }),
  })
  return res.data
}

export async function decideOn(
  sessionId: string,
  payload: {
    brief_sha256: string
    verdict: 'APPROVED' | 'REJECTED'
    scenario_id?: string | null
    acknowledged: string[]
    reason: string
  }
): Promise<DecisionRecord> {
  const res = await apiFetch<{ data: DecisionRecord }>(`/api/v1/industrial/sessions/${sessionId}/decide-on`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return res.data
}

export async function getSessionHistory(sessionId: string): Promise<DecisionHistory> {
  const res = await apiFetch<{ data: DecisionHistory }>(`/api/v1/industrial/sessions/${sessionId}/history`)
  return res.data
}
