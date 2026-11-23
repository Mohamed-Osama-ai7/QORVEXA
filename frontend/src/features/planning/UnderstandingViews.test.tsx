import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { UnderstandingReport } from '../../api/planningApi'
import { UnderstandingReportView } from './UnderstandingReportView'

const MOCK_REPORT: UnderstandingReport = {
  schema_version: 'qorvexa-understanding/1',
  pipeline_version: 'understanding-pipeline/1.0',
  idempotency_key: 'idem-test-1',
  factory_id: 'factory-1',
  source_ids: ['turn-0'],
  source_sha256: ['sha256-test'],
  stages: [
    { stage: 'INPUT', status: 'OK', summary: 'turn-0', errors: [] },
    { stage: 'LANGUAGE_UNDERSTANDING', status: 'OK', summary: 'ARABIC', errors: [] },
  ],
  languages: [
    {
      seg_id: 's0',
      primary: 'ARABIC',
      code_switching: false,
      arabizi: false,
      register_hint: 'EGYPTIAN_COLLOQUIAL_MARKERS',
      markers: ['بتنتج'],
    },
  ],
  entities: [
    {
      mention_id: 'e0',
      text: 'الفيلر',
      orig_start: 0,
      orig_end: 6,
      entity_type: 'MACHINE',
      status: 'CONFIRMED_TERM',
      canonical_id: 'FILLER',
    },
  ],
  claims: [
    {
      claim_id: 'c0',
      source_id: 'turn-0',
      predicate: 'CAPACITY',
      semantic_type: 'ACTUAL_MEASUREMENT',
      negated: false,
      modality: 'ASSERTED',
      quote: 'الفيلر طاقته 100 ساعة.',
      orig_start: 0,
      orig_end: 22,
      value: {
        kind: 'EXACT',
        low: '100',
        unit_text: 'ساعة',
        unit: 'h',
        text: '100 ساعة',
        orig_start: 13,
        orig_end: 21,
      },
      provenance: 'rules/1',
    },
  ],
  grounding: [
    {
      claim_id: 'c0',
      outcome: 'GROUNDED',
      checks: [['quote_in_source', true]],
      reasons: [],
    },
  ],
  contradictions: [],
  gaps: [],
  candidates: [
    {
      key: 'resource:FILLER:capacity',
      value: '100',
      support: ['c0'],
      quote: 'الفيلر طاقته 100 ساعة.',
      status: 'SUPPORTED',
      note: 'Mapped from confirmed entity',
    },
  ],
  questions: [],
  readiness: 'CANDIDATES_FOR_HUMAN_REVIEW',
  disclaimer: 'Untrusted understanding only: not mathematically validated.',
  llm_status: 'AVAILABLE',
}

describe('UnderstandingReportView', () => {
  it('renders readiness badge, language detection, and candidates table', () => {
    render(<UnderstandingReportView report={MOCK_REPORT} />)

    expect(screen.getByText('CANDIDATES FOR HUMAN REVIEW')).toBeInTheDocument()
    expect(screen.getByText('EGYPTIAN_COLLOQUIAL_MARKERS')).toBeInTheDocument()
    expect(screen.getByText('resource:FILLER:capacity')).toBeInTheDocument()
    expect(screen.getByText('GROUNDED')).toBeInTheDocument()
    expect(screen.getByText(/EPISTEMIC BOUNDARY:/i)).toBeInTheDocument()
  })

  it('renders empty fallback when no report is present', () => {
    render(<UnderstandingReportView report={null} />)
    expect(screen.getByText('No industrial understanding report available yet.')).toBeInTheDocument()
  })
})
