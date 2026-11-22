import { useState } from 'react'
import type { ExtractedClaim } from '../../api/planningApi'

interface Props {
  sessionId: string
  onIngest: (text: string, claims?: ExtractedClaim[]) => Promise<void>
  isPending: boolean
}

const SAMPLE_TEXT = `Factory Mix Specification:
Problem ID: product_mix
Objective: MAXIMIZE_PROFIT
Currency: EUR

Products:
- Product A (P_A): profit is 40 EUR per unit. Minimum production is 0 units, maximum is 40 units.
  Requires 2 hours on MACHINE and 1 hour on LABOR per unit.
- Product B (P_B): profit is 50 EUR per unit. Minimum production is 0 units, maximum is 50 units.
  Requires 1 hour on MACHINE and 2 hours on LABOR per unit.

Resources:
- MACHINE: Available machine capacity is 100 hours (kind: MACHINE).
- LABOR: Available labor capacity is 100 hours (kind: LABOR).

Units:
- Product units: item
- Resource units: hour
- Currency: EUR`

const SAMPLE_CLAIMS: ExtractedClaim[] = [
  { key: 'problem:id', value: 'product_mix', quote: 'Problem ID: product_mix' },
  { key: 'problem:objective', value: 'MAXIMIZE_PROFIT', quote: 'Objective: MAXIMIZE_PROFIT' },
  { key: 'problem:currency', value: 'EUR', quote: 'Currency: EUR' },
  { key: 'product:P_A:name', value: 'Product A', quote: 'Product A (P_A)' },
  { key: 'product:P_A:profit', value: 40, quote: 'profit is 40 EUR' },
  { key: 'product:P_A:min_quantity', value: 0, quote: 'Minimum production is 0 units' },
  { key: 'product:P_A:max_quantity', value: 40, quote: 'maximum is 40 units' },
  { key: 'product:P_B:name', value: 'Product B', quote: 'Product B (P_B)' },
  { key: 'product:P_B:profit', value: 50, quote: 'profit is 50 EUR' },
  { key: 'product:P_B:min_quantity', value: 0, quote: 'Minimum production is 0 units' },
  { key: 'product:P_B:max_quantity', value: 50, quote: 'maximum is 50 units' },
  { key: 'resource:MACHINE:name', value: 'Machine Shop', quote: 'MACHINE: Available machine capacity' },
  { key: 'resource:MACHINE:capacity', value: 100, quote: 'capacity is 100 hours' },
  { key: 'resource:MACHINE:kind', value: 'MACHINE', quote: 'kind: MACHINE' },
  { key: 'resource:LABOR:name', value: 'Labor Force', quote: 'LABOR: Available labor capacity' },
  { key: 'resource:LABOR:capacity', value: 100, quote: 'capacity is 100 hours' },
  { key: 'resource:LABOR:kind', value: 'LABOR', quote: 'kind: LABOR' },
  { key: 'consumption:P_A:MACHINE:rate', value: 2, quote: 'Requires 2 hours on MACHINE' },
  { key: 'consumption:P_A:LABOR:rate', value: 1, quote: '1 hour on LABOR' },
  { key: 'consumption:P_B:MACHINE:rate', value: 1, quote: 'Requires 1 hour on MACHINE' },
  { key: 'consumption:P_B:LABOR:rate', value: 2, quote: '2 hours on LABOR' },
]

export function ProblemIntakeView({ onIngest, isPending }: Props) {
  const [text, setText] = useState(SAMPLE_TEXT)
  const [useSampleClaims, setUseSampleClaims] = useState(true)

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-border bg-panel p-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-ink">Factory Language Intake</h2>
            <p className="text-xs text-muted">
              Enter operational plant notes, shift handover reports, or specification text in English or Arabic.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => {
                setText(SAMPLE_TEXT)
                setUseSampleClaims(true)
              }}
              className="rounded border border-border px-3 py-1.5 text-xs font-medium text-ink hover:bg-border"
            >
              Load Standard Factory Mix
            </button>
          </div>
        </div>

        <div className="mt-4">
          <label htmlFor="factory-text" className="block text-xs font-medium text-muted">
            Source Operational Text
          </label>
          <textarea
            id="factory-text"
            rows={10}
            value={text}
            onChange={(e) => setText(e.target.value)}
            className="mt-1 w-full rounded border border-border bg-surface p-3 font-mono text-xs text-ink focus:border-accent focus:outline-none"
            placeholder="Type or paste factory shift notes..."
          />
        </div>

        <div className="mt-4 flex flex-wrap items-center justify-between gap-4 border-t border-border pt-4">
          <label className="flex items-center gap-2 text-xs text-muted">
            <input
              type="checkbox"
              checked={useSampleClaims}
              onChange={(e) => setUseSampleClaims(e.target.checked)}
              className="rounded border-border bg-surface text-accent"
            />
            Attach verified claim quotes for instant deterministic ingestion (offline/demo mode)
          </label>

          <button
            type="button"
            disabled={isPending || !text.trim()}
            onClick={() => onIngest(text, useSampleClaims ? SAMPLE_CLAIMS : undefined)}
            className="rounded bg-accent px-4 py-2 text-xs font-medium text-white hover:bg-blue-600 disabled:opacity-50"
          >
            {isPending ? 'Extracting Claims...' : 'Ingest & Generate Proposals'}
          </button>
        </div>
      </div>

      <div className="rounded-lg border border-border bg-panel p-5">
        <h3 className="text-sm font-semibold text-ink">Epistemic Boundary Notice</h3>
        <p className="mt-1 text-xs text-muted">
          All extracted statements enter the system strictly as <strong>untrusted proposals</strong>.
          Every proposal requires an exact verbatim quote from the text and numeric verification.
          Proposals never reach the mathematical solver without explicit human confirmation on the Fact Board.
        </p>
      </div>
    </div>
  )
}
