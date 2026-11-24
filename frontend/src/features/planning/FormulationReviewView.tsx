import type { FormulationResult, FormulationState } from '../../api/planningApi'

interface Props {
  state: FormulationState | null
  formulation: FormulationResult | null
  onBuild: () => Promise<void>
  isPending: boolean
}

export function FormulationReviewView({ state, formulation, onBuild, isPending }: Props) {
  if (!state) {
    return (
      <div className="rounded-lg border border-border bg-panel p-8 text-center text-sm text-muted">
        Select a planning session to review its formulation state.
      </div>
    )
  }

  const problem = formulation?.problem

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-border bg-panel p-5">
        <div>
          <h2 className="text-lg font-semibold text-ink">Formulation &amp; Unit Gate</h2>
          <p className="text-xs text-muted">Session status: {state.status}</p>
          <p className="text-xs text-muted">Unit status: {state.units_status || 'Unavailable'}</p>
        </div>
        <button
          type="button"
          disabled={isPending || state.status !== 'READY'}
          onClick={onBuild}
          className="rounded bg-accent px-4 py-2 text-xs font-medium text-white hover:bg-blue-600 disabled:opacity-50"
        >
          {isPending ? 'Validating...' : 'Compile Formulation'}
        </button>
      </div>

      {problem && (
        <div className="rounded-lg border border-border bg-panel p-5">
          <h3 className="text-sm font-semibold text-ink">Compiled Formulation</h3>
          <p className="text-xs text-muted">{problem.mode} · {problem.currency}</p>
          <p className="break-all font-mono text-xs text-muted">
            Model digest: {formulation.compiled?.manifest.spec_sha256}
          </p>
          <dl className="mt-4 grid gap-4 text-xs md:grid-cols-3">
            <div>
              <dt className="font-semibold">Products</dt>
              {problem.products.map((product) => (
                <dd key={product.id}>
                  {product.name} ({product.id}) · {product.value_per_unit} {product.value_currency} per {product.unit}
                  {' · '}Bounds {product.min_quantity ?? '0'}–{product.max_quantity ?? 'unbounded'}
                </dd>
              ))}
            </div>
            <div>
              <dt className="font-semibold">Resource Capacities</dt>
              {problem.resources.map((resource) => (
                <dd key={resource.id}>
                  {resource.name} ({resource.id}) · {resource.capacity} {resource.unit}
                </dd>
              ))}
            </div>
            <div>
              <dt className="font-semibold">Consumption Rates</dt>
              {problem.consumptions?.map((rate) => (
                <dd key={`${rate.product_id}:${rate.resource_id}`}>
                  {rate.product_id} · {rate.resource_id} · {rate.amount_per_unit} {rate.resource_unit} per {rate.product_unit}
                </dd>
              ))}
            </div>
          </dl>
        </div>
      )}
    </div>
  )
}
