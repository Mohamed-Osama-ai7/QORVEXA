/**
 * Typed client for QORVEXA Industrial Decision Studio API.
 */

import {
  approveOrRejectDecision,
  buildFormulation as requestFormulation,
  getSessionHistory as requestSessionHistory,
  listSessions as requestSessions,
  runDecisionIntelligence,
} from './generated/industrial/industrial'
import type { DecideOnRequest } from './generated/model/decideOnRequest'
import type { DecisionBrief } from './generated/model/decisionBrief'
import type { DecisionHistory } from './generated/model/decisionHistory'
import type { DecisionRecord } from './generated/model/decisionRecord'
import type { FormulationResult } from './generated/model/formulationResult'
import type { FormulationState } from './generated/model/formulationState'
import type { ScenarioSpec } from './generated/model/scenarioSpec'
import type { SessionSummary } from './generated/model/sessionSummary'
import { ApiError, apiFetch } from './http'

export type {
  DecideOnRequest,
  DecisionBrief,
  DecisionHistory,
  FormulationResult,
  FormulationState,
  ScenarioSpec,
  SessionSummary,
}

export interface ExtractedClaim {
  key: string
  value: string | number
  quote: string
}

export interface IngestResponse {
  source_sha256: string
  accepted_count: number
  rejected_count: number
  accepted: ExtractedClaim[]
  rejected: [ExtractedClaim, string][]
  state: FormulationState
  understanding?: UnderstandingReport | null
  readiness?: 'BLOCKED' | 'NEEDS_CLARIFICATION' | 'CANDIDATES_FOR_HUMAN_REVIEW' | null
  questions?: Question[]
}

export interface LanguageInfo {
  seg_id: string
  primary: string
  arabic_chars?: number
  latin_chars?: number
  code_switching: boolean
  arabizi: boolean
  register_hint: string
  markers: string[]
}

export interface EntityMention {
  mention_id: string
  text: string
  orig_start: number
  orig_end: number
  entity_type: string
  status: string
  canonical_id?: string | null
  term_id?: string | null
}

export interface QuantityMention {
  kind: string
  low: string
  high?: string | null
  unit_text: string
  unit?: string | null
  currency?: string | null
  per?: string | null
  text: string
  orig_start: number
  orig_end: number
  ambiguous?: boolean
}

export interface Claim {
  claim_id: string
  source_id: string
  turn?: number
  quote: string
  subject_text?: string | null
  subject_entity?: string | null
  related_text?: string | null
  related_entity?: string | null
  predicate: string
  value?: QuantityMention | null
  semantic_type: string
  negated: boolean
  modality: string
  bound?: string | null
  condition?: string | null
  orig_start: number
  orig_end: number
  normalized?: string
  provenance?: string
}

export interface GroundingResult {
  claim_id: string
  outcome: 'GROUNDED' | 'PARTIALLY_GROUNDED' | 'UNSUPPORTED' | 'AMBIGUOUS' | 'CONFLICTED'
  checks: [string, boolean][]
  reasons: string[]
}

export interface Contradiction {
  code: string
  claim_ids: string[]
  description: string
}

export interface Gap {
  gap_id: string
  code: string
  message: string
  affected: string[]
  blocking: boolean
  why: string
  tier: number
}

export interface Question {
  gap_id: string
  language: string
  text: string
  why: string
  affected: string[]
  blocking: boolean
  asked_count: number
}

export interface Candidate {
  key: string
  value: string
  support: string[]
  quote: string
  status: string
  note?: string
}

export interface StageResult {
  stage: string
  status: string
  summary: string
  errors: string[]
}

export interface UnderstandingReport {
  schema_version: string
  pipeline_version: string
  idempotency_key: string
  factory_id: string
  source_ids: string[]
  source_sha256: string[]
  stages: StageResult[]
  languages: LanguageInfo[]
  entities: EntityMention[]
  claims: Claim[]
  grounding: GroundingResult[]
  contradictions: Contradiction[]
  gaps: Gap[]
  candidates: Candidate[]
  questions: Question[]
  readiness: 'BLOCKED' | 'NEEDS_CLARIFICATION' | 'CANDIDATES_FOR_HUMAN_REVIEW'
  disclaimer: string
  llm_status: string
  report_sha256?: string
}

export interface TermEvent {
  version: number
  action: string
  actor: string
  at: string
  note?: string
  from_status?: string
}

export interface TermEntry {
  term_id: string
  factory_id: string
  expression: string
  concept: string
  meaning: string
  entity_type?: string | null
  canonical_id?: string | null
  line_id?: string | null
  department?: string | null
  language?: string
  dialect?: string
  source?: string
  status: 'PROPOSED' | 'CONFIRMED' | 'REJECTED' | 'DEPRECATED'
  version: number
  reviewer?: string | null
  aliases?: string[]
  spelling_variants?: string[]
  history: TermEvent[]
}

export interface IngestOptions {
  factory_id?: string
  speaker_role?: string
  turn?: number
  line_id?: string
  department?: string
  default_currency?: string
}

export interface UnderstandingLinks {
  session_id: string
  factory_id: string
  ontology_head: string
  entity_links: Record<string, string>
  resolved_entities: Array<{
    mention_id: string
    text: string
    entity_id: string
    entity_type: string
    name: string
  }>
  unresolved_mentions: Array<{
    mention_id: string
    text: string
    suggested_type?: string
  }>
}

export interface OntologyContextSnapshot {
  factory_id: string
  head_sha: string
  revision: number
  entities: Record<string, unknown>
  relations: Record<string, unknown>
  roots: string[]
}

export interface OntologyPlanningCandidates {
  factory_id: string
  line_id?: string
  machines: Array<{ id: string; name: string; capacity?: string }>
  materials: Array<{ id: string; name: string; unit?: string }>
  products: Array<{ id: string; name: string; routings: string[] }>
  routings: Array<{ id: string; steps: unknown[] }>
}

export interface OntologyIntegrityReport {
  factory_id: string
  is_valid: boolean
  issues: Array<{
    code: string
    severity: string
    message: string
    entity_id?: string
    relation_id?: string
  }>
}

export async function listSessions(): Promise<SessionSummary[]> {
  const { data } = await requestSessions()
  if (!Array.isArray(data)) throw invalidResponse()
  return data
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
  claims?: ExtractedClaim[],
  options?: IngestOptions
): Promise<IngestResponse> {
  const payload: Record<string, unknown> = { text }
  if (claims) payload.claims = claims
  if (options?.factory_id) payload.factory_id = options.factory_id
  if (options?.speaker_role) payload.speaker_role = options.speaker_role
  if (options?.turn !== undefined) payload.turn = options.turn
  if (options?.line_id) payload.line_id = options.line_id
  if (options?.department) payload.department = options.department
  if (options?.default_currency) payload.default_currency = options.default_currency

  const res = await apiFetch<{ data: IngestResponse }>(`/api/v1/industrial/sessions/${sessionId}/ingest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return res.data
}

export async function getSessionUnderstanding(sessionId: string): Promise<UnderstandingReport> {
  const res = await apiFetch<{ data: UnderstandingReport }>(
    `/api/v1/industrial/sessions/${sessionId}/understanding`
  )
  return res.data
}

export async function getSessionClarifications(
  sessionId: string,
  factoryId: string
): Promise<{ clarification_state: unknown; questions: Question[]; gaps: Gap[] }> {
  const res = await apiFetch<{ data: { clarification_state: unknown; questions: Question[]; gaps: Gap[] } }>(
    `/api/v1/industrial/sessions/${sessionId}/clarifications?factory_id=${encodeURIComponent(factoryId)}`
  )
  return res.data
}

export async function listTerms(factoryId: string): Promise<TermEntry[]> {
  const res = await apiFetch<{ data: TermEntry[] }>(
    `/api/v1/industrial/terminology/${encodeURIComponent(factoryId)}`
  )
  return res.data
}

export async function getTermHistory(factoryId: string, termId: string): Promise<TermEntry[]> {
  const res = await apiFetch<{ data: TermEntry[] }>(
    `/api/v1/industrial/terminology/${encodeURIComponent(factoryId)}/${encodeURIComponent(termId)}`
  )
  return res.data
}

export async function proposeTerm(factoryId: string, entry: Partial<TermEntry>): Promise<TermEntry> {
  const res = await apiFetch<{ data: TermEntry }>(
    `/api/v1/industrial/terminology/${encodeURIComponent(factoryId)}/propose`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(entry),
    }
  )
  return res.data
}

export async function confirmTerm(factoryId: string, termId: string, note: string = ''): Promise<TermEntry> {
  const res = await apiFetch<{ data: TermEntry }>(
    `/api/v1/industrial/terminology/${encodeURIComponent(factoryId)}/${encodeURIComponent(termId)}/confirm`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note }),
    }
  )
  return res.data
}

export async function rejectTerm(factoryId: string, termId: string, note: string = ''): Promise<TermEntry> {
  const res = await apiFetch<{ data: TermEntry }>(
    `/api/v1/industrial/terminology/${encodeURIComponent(factoryId)}/${encodeURIComponent(termId)}/reject`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note }),
    }
  )
  return res.data
}

export async function correctTerm(
  factoryId: string,
  termId: string,
  note: string,
  changes: Partial<TermEntry> = {}
): Promise<TermEntry> {
  const res = await apiFetch<{ data: TermEntry }>(
    `/api/v1/industrial/terminology/${encodeURIComponent(factoryId)}/${encodeURIComponent(termId)}/correct`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note, ...changes }),
    }
  )
  return res.data
}

export async function deprecateTerm(factoryId: string, termId: string, note: string = ''): Promise<TermEntry> {
  const res = await apiFetch<{ data: TermEntry }>(
    `/api/v1/industrial/terminology/${encodeURIComponent(factoryId)}/${encodeURIComponent(termId)}/deprecate`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note }),
    }
  )
  return res.data
}

export async function rollbackTerm(
  factoryId: string,
  termId: string,
  toVersion: number,
  note: string
): Promise<TermEntry> {
  const res = await apiFetch<{ data: TermEntry }>(
    `/api/v1/industrial/terminology/${encodeURIComponent(factoryId)}/${encodeURIComponent(termId)}/rollback`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ to_version: toVersion, note }),
    }
  )
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

export async function buildFormulation(sessionId: string): Promise<FormulationResult> {
  const result = successfulData(await requestFormulation(sessionId))
  if (!result?.state) throw invalidResponse()
  return result
}

export async function runDecision(
  sessionId: string,
  scenarios: ScenarioSpec[] = [],
  paretoMetrics: string[] = ['objective', 'total_volume'],
): Promise<DecisionBrief> {
  const brief = successfulData(await runDecisionIntelligence(sessionId, { scenarios, pareto_metrics: paretoMetrics }))
  if (typeof brief?.brief_sha256 !== 'string' || !brief.recommendation) throw invalidResponse()
  return brief
}

export async function decideOn(sessionId: string, payload: DecideOnRequest): Promise<DecisionRecord> {
  const record = successfulData(await approveOrRejectDecision(sessionId, payload))
  if (record?.session_id !== sessionId || record.brief_sha256 !== payload.brief_sha256 ||
      record.verdict !== payload.verdict || !record.record_sha256) {
    throw invalidResponse()
  }
  return record
}

export async function getSessionHistory(sessionId: string): Promise<DecisionHistory> {
  const history = successfulData(await requestSessionHistory(sessionId))
  if (!Array.isArray(history?.records)) throw invalidResponse()
  return history
}

function successfulData<T>(response: { status: 200; data: T } | { status: 422; data: unknown }): T {
  if (response.status === 200) return response.data
  throw new ApiError(response.status, response.data)
}

function invalidResponse() {
  return new ApiError(502, { detail: 'The planning service returned an unexpected response.' })
}

export async function getSessionOntologyLinks(sessionId: string): Promise<UnderstandingLinks> {
  const res = await apiFetch<{ data: UnderstandingLinks }>(
    `/api/v1/industrial/sessions/${sessionId}/ontology-links`
  )
  return res.data
}

export async function getOntologyContext(
  factoryId: string,
  roots?: string[]
): Promise<OntologyContextSnapshot> {
  const query = roots && roots.length > 0 ? `?roots=${encodeURIComponent(roots.join(','))}` : ''
  const res = await apiFetch<{ data: OntologyContextSnapshot }>(
    `/api/v1/industrial/ontology/${encodeURIComponent(factoryId)}/context${query}`
  )
  return res.data
}

export async function getOntologyPlanningCandidates(
  factoryId: string,
  lineId?: string
): Promise<OntologyPlanningCandidates> {
  const query = lineId ? `?line_id=${encodeURIComponent(lineId)}` : ''
  const res = await apiFetch<{ data: OntologyPlanningCandidates }>(
    `/api/v1/industrial/ontology/${encodeURIComponent(factoryId)}/planning-candidates${query}`
  )
  return res.data
}

export async function verifyFactoryOntology(
  factoryId: string
): Promise<OntologyIntegrityReport> {
  const res = await apiFetch<{ data: OntologyIntegrityReport }>(
    `/api/v1/industrial/ontology/${encodeURIComponent(factoryId)}/verify`
  )
  return res.data
}
