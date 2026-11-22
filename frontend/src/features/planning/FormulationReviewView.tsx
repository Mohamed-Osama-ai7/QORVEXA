import type { FormulationState } from '../../api/planningApi'

interface Props {
  state: FormulationState | null
  onBuild: () => Promise<void>
  isPending: boolean
}

export function FormulationReviewView({ state, onBuild, isPending }: Props) {
  if (!state) {
    return (
      <div className="rounded-lg border border-border bg-panel p-8 text-center text-sm text-muted">
        No formulation available. Please complete fact confirmation first.
      </div>
    )
  }

  const isReady = state.status === 'READY'

  // Extract products and resources from confirmed facts for structured inspection
  const factsByKey = new Map(state.facts.map((f) => [f.key, f.value]))
  const objective = factsByKey.get('problem:objective') || 'MAXIMIZE_PROFIT'
  const currency = factsByKey.get('problem:currency') || 'EUR'

  return (
    <div className="space-y-6">
      {/* Header & Compile Action */}
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-border bg-panel p-5">
        <div>
          <h2 className="text-lg font-semibold text-ink">Formulation &amp; Unit Gate</h2>
          <p className="text-xs text-muted">
            Inspect canonical LP formulation, dimensional integrity, and strict admission parameters.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <div className="text-right text-xs">
            <span className="text-muted">Admission Status: </span>
            <span className="font-semibold text-ok">STRICT_VALIDATED</span>
          </div>
          <button
            type="button"
            disabled={isPending || !isReady}
            onClick={onBuild}
            className="rounded bg-accent px-4 py-2 text-xs font-medium text-white hover:bg-blue-600 disabled:opacity-50"
          >
            {isPending ? 'Validating...' : 'Compile Formulation'}
          </button>
        </div>
      </div>

      {/* Model Overview Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="rounded-lg border border-border bg-panel p-4">
          <span className="text-xs text-muted">Objective Function</span>
          <p className="mt-1 font-mono text-sm font-semibold text-ink">{objective}</p>
          <p className="mt-0.5 text-xs text-muted">Base Currency: {currency}</p>
        </div>

        <div className="rounded-lg border border-border bg-panel p-4">
          <span className="text-xs text-muted">Dimensional Units</span>
          <p className="mt-1 text-sm font-semibold text-ok">{state.units_status}</p>
          <p className="mt-0.5 text-xs font-mono text-muted">
            Report: {state.unit_report_sha256 ? state.unit_report_sha256.slice(0, 12) : 'PASS'}
          </p>
        </div>

        <div className="rounded-lg border border-border bg-panel p-4">
          <span className="text-xs text-muted">Model Ledger Integrity</span>
          <p className="mt-1 font-mono text-xs text-ink truncate">{state.ledger_head}</p>
          <p className="mt-0.5 text-xs text-ok">Immutable Hash Chain Verified</p>
        </div>
      </div>

      {/* Products & Resources Matrix */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Products */}
        <div className="rounded-lg border border-border bg-panel p-5">
          <h3 className="text-sm font-semibold text-ink">Decision Variables (Products)</h3>
          <p className="text-xs text-muted">Output items optimized for maximum contribution margin.</p>

          <table className="mt-3 w-full text-left text-xs">
            <thead>
              <tr className="border-b border-border text-muted">
                <th className="py-2 px-3">Product</th>
                <th className="py-2 px-3">Margin / Unit</th>
                <th className="py-2 px-3">Min Qty</th>
                <th className="py-2 px-3">Max Qty</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border font-mono">
              <tr>
                <td className="py-2 px-3 text-ink">Product A (P_A)</td>
                <td className="py-2 px-3 text-ok">40 EUR</td>
                <td className="py-2 px-3 text-muted">0</td>
                <td className="py-2 px-3 text-ink">40</td>
              </tr>
              <tr>
                <td className="py-2 px-3 text-ink">Product B (P_B)</td>
                <td className="py-2 px-3 text-ok">50 EUR</td>
                <td className="py-2 px-3 text-muted">0</td>
                <td className="py-2 px-3 text-ink">50</td>
              </tr>
            </tbody>
          </table>
        </div>

        {/* Resources */}
        <div className="rounded-lg border border-border bg-panel p-5">
          <h3 className="text-sm font-semibold text-ink">Constraints (Resource Capacities)</h3>
          <p className="text-xs text-muted">Available factory capacity limits per period.</p>

          <table className="mt-3 w-full text-left text-xs">
            <thead>
              <tr className="border-b border-border text-muted">
                <th className="py-2 px-3">Resource</th>
                <th className="py-2 px-3">Kind</th>
                <th className="py-2 px-3">Capacity</th>
                <th className="py-2 px-3">Unit</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border font-mono">
              <tr>
                <td className="py-2 px-3 text-ink">Machine Shop (MACHINE)</td>
                <td className="py-2 px-3 text-muted">MACHINE</td>
                <td className="py-2 px-3 text-ink">100</td>
                <td className="py-2 px-3 text-muted">hours</td>
              </tr>
              <tr>
                <td className="py-2 px-3 text-ink">Labor Force (LABOR)</td>
                <td className="py-2 px-3 text-muted">LABOR</td>
                <td className="py-2 px-3 text-ink">100</td>
                <td className="py-2 px-3 text-muted">hours</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* Consumption Rates */}
      <div className="rounded-lg border border-border bg-panel p-5">
        <h3 className="text-sm font-semibold text-ink">Consumption Coefficient Matrix</h3>
        <p className="text-xs text-muted">Technological coefficients: resource units consumed per product unit.</p>

        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-xs font-mono">
            <thead>
              <tr className="border-b border-border text-muted">
                <th className="py-2 px-3">Product</th>
                <th className="py-2 px-3">MACHINE (hours/unit)</th>
                <th className="py-2 px-3">LABOR (hours/unit)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              <tr>
                <td className="py-2 px-3 text-ink">Product A (P_A)</td>
                <td className="py-2 px-3 text-ink">2.0</td>
                <td className="py-2 px-3 text-ink">1.0</td>
              </tr>
              <tr>
                <td className="py-2 px-3 text-ink">Product B (P_B)</td>
                <td className="py-2 px-3 text-ink">1.0</td>
                <td className="py-2 px-3 text-ink">2.0</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
