import { useState } from 'react'
import type { PlanningProblem } from '../../api/generated/model/planningProblem'
import type { ScenarioSpec } from '../../api/planningApi'

interface Props {
  problem: PlanningProblem | null
  scenarios: ScenarioSpec[]
  onUpdateScenarios: (scenarios: ScenarioSpec[]) => void
  onRunDecision: () => Promise<void>
  isPending: boolean
}

const PRESET_SCENARIOS: ScenarioSpec[] = [
  {
    scenario_id: 'MACH120',
    description: 'Example: Set machine shop capacity to 120 resource units',
    changes: [
      {
        kind: 'capacity',
        resource_id: 'MACHINE',
        capacity: '120',
      },
    ],
  },
  {
    scenario_id: 'MACH80',
    description: 'Example: Set machine shop capacity to 80 resource units',
    changes: [
      {
        kind: 'capacity',
        resource_id: 'MACHINE',
        capacity: '80',
      },
    ],
  },
  {
    scenario_id: 'B90',
    description: 'Example: Set Product B minimum production to 90 units',
    changes: [
      {
        kind: 'demand_bounds',
        product_id: 'P_B',
        min_quantity: '90',
      },
    ],
  },
]

export function ScenarioWorkbenchView({
  problem,
  scenarios,
  onUpdateScenarios,
  onRunDecision,
  isPending,
}: Props) {
  const [newId, setNewId] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [resId, setResId] = useState('')
  const [resCap, setResCap] = useState('')

  if (!problem) {
    return (
      <div className="rounded-lg border border-border bg-panel p-5">
        <h2 className="text-lg font-semibold text-ink">Scenario Workbench</h2>
        <p className="text-xs text-muted">Compile the formulation before running a decision.</p>
      </div>
    )
  }

  const activeResource = problem.resources.find((resource) => resource.id === resId)
  const canAddScenario = !isPending && activeResource && resCap.trim() !== '' &&
    Number.isFinite(Number(resCap)) && Number(resCap) >= 0 && newId.trim() !== '' &&
    !scenarios.some((scenario) => scenario.scenario_id === newId.trim().toUpperCase())
  const presets = PRESET_SCENARIOS.filter((preset) => preset.changes.every((change) =>
    'resource_id' in change
      ? problem.resources.some((resource) => resource.id === change.resource_id)
      : problem.products.some((product) => product.id === change.product_id),
  ))

  const addPreset = (preset: ScenarioSpec) => {
    if (scenarios.some((s) => s.scenario_id === preset.scenario_id)) return
    onUpdateScenarios([...scenarios, preset])
  }

  const removeScenario = (id: string) => {
    onUpdateScenarios(scenarios.filter((s) => s.scenario_id !== id))
  }

  const addCustomScenario = () => {
    if (!canAddScenario) return
    const custom: ScenarioSpec = {
      scenario_id: newId.trim().toUpperCase(),
      description: newDesc.trim() || `Capacity adjustment on ${resId} to ${resCap}`,
      changes: [
        {
          kind: 'capacity',
          resource_id: resId,
          capacity: resCap,
        },
      ],
    }
    onUpdateScenarios([...scenarios, custom])
    setNewId('')
    setNewDesc('')
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-border bg-panel p-5">
        <div>
          <h2 className="text-lg font-semibold text-ink">Scenario Workbench</h2>
          <p className="text-xs text-muted">
            Define counterfactual What-If scenarios with preserved mathematical lineage for Pareto evaluation.
          </p>
        </div>

        <button
          type="button"
          disabled={isPending}
          onClick={onRunDecision}
          className="rounded bg-accent px-4 py-2 text-xs font-medium text-white hover:bg-blue-600 disabled:opacity-50"
        >
          {isPending ? 'Solving with HiGHS...' : 'Run Real Optimization & Intelligence'}
        </button>
      </div>

      {/* Preset Scenarios Buttons */}
      {presets.length > 0 && (
        <div className="rounded-lg border border-border bg-panel p-4">
          <span className="text-xs font-semibold uppercase tracking-wider text-muted">
            Example What-If Scenarios
          </span>
          <div className="mt-2 flex flex-wrap gap-2">
            {presets.map((preset) => {
              const isAdded = scenarios.some((s) => s.scenario_id === preset.scenario_id)
              return (
                <button
                  key={preset.scenario_id}
                  type="button"
                  disabled={isPending || isAdded}
                  onClick={() => addPreset(preset)}
                  className={`rounded border px-3 py-1.5 text-xs font-medium transition-colors ${
                    isAdded
                      ? 'border-border bg-surface text-muted cursor-default'
                      : 'border-border bg-panel text-ink hover:border-accent hover:text-accent'
                  }`}
                >
                  + {preset.scenario_id}
                </button>
              )
            })}
          </div>
        </div>
      )}

      {/* Active Scenarios List */}
      <div className="space-y-3">
        {/* Baseline Card */}
        <div className="rounded-lg border border-border bg-panel p-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="rounded bg-accent/20 px-2 py-0.5 font-mono text-xs font-semibold text-accent">
                BASE
              </span>
              <span className="text-sm font-medium text-ink">Baseline Mathematical Production Mix</span>
            </div>
            <span className="text-xs font-medium text-muted">Primary Anchor (Reference)</span>
          </div>
          <p className="mt-1 text-xs text-muted">
            {problem.resources.map((resource) => `${resource.name}: ${resource.capacity} ${resource.unit}`).join(' · ')}
          </p>
        </div>

        {/* User Added Scenarios */}
        {scenarios.map((sc) => (
          <div key={sc.scenario_id} className="rounded-lg border border-border bg-panel p-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="rounded bg-ok/20 px-2 py-0.5 font-mono text-xs font-semibold text-ok">
                  {sc.scenario_id}
                </span>
                <span className="text-sm font-medium text-ink">{sc.description}</span>
              </div>
              <button
                type="button"
                disabled={isPending}
                onClick={() => removeScenario(sc.scenario_id)}
                className="text-xs text-critical hover:underline"
              >
                Remove
              </button>
            </div>
            <div className="mt-2 flex flex-wrap gap-2 text-xs font-mono text-muted">
              {sc.changes.map((ch, idx) => (
                <span key={idx} className="rounded bg-surface px-2 py-0.5 border border-border">
                  {'resource_id' in ch
                    ? `${ch.resource_id}.capacity = ${ch.capacity}`
                    : 'value_per_unit' in ch
                      ? `${ch.product_id}.value_per_unit = ${ch.value_per_unit}`
                      : `${ch.product_id} demand bounds`}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* Add Custom Scenario Form */}
      <div className="rounded-lg border border-border bg-panel p-5">
        <h3 className="text-sm font-semibold text-ink">Add Custom Parameter Variation</h3>
        <div className="mt-3 grid grid-cols-1 md:grid-cols-4 gap-3">
          <input
            disabled={isPending}
            type="text"
            value={newId}
            onChange={(e) => setNewId(e.target.value)}
            placeholder="Scenario ID (e.g. MACH110)"
            className="rounded border border-border bg-surface px-3 py-1.5 font-mono text-xs text-ink focus:border-accent focus:outline-none"
          />
          <select
            aria-label="Resource"
            disabled={isPending}
            value={activeResource?.id ?? ''}
            onChange={(e) => setResId(e.target.value)}
            className="rounded border border-border bg-surface px-3 py-1.5 text-xs text-ink focus:border-accent focus:outline-none"
          >
            <option value="">Choose a resource</option>
            {problem.resources.map((resource) => (
              <option key={resource.id} value={resource.id}>{resource.name} ({resource.id}, {resource.unit})</option>
            ))}
          </select>
          <input
            disabled={isPending}
            type="number"
            value={resCap}
            onChange={(e) => setResCap(e.target.value)}
            placeholder="New capacity"
            className="rounded border border-border bg-surface px-3 py-1.5 font-mono text-xs text-ink focus:border-accent focus:outline-none"
          />
          <button
            type="button"
            disabled={!canAddScenario}
            onClick={addCustomScenario}
            className="rounded border border-border px-3 py-1.5 text-xs font-medium text-ink hover:bg-border disabled:opacity-50"
          >
            + Add Scenario
          </button>
        </div>
      </div>
    </div>
  )
}
