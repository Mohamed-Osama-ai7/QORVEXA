import { useState } from 'react'
import type { ScenarioSpec } from '../../api/planningApi'

interface Props {
  scenarios: ScenarioSpec[]
  onUpdateScenarios: (scenarios: ScenarioSpec[]) => void
  onRunDecision: () => Promise<void>
  isPending: boolean
}

const PRESET_SCENARIOS: ScenarioSpec[] = [
  {
    scenario_id: 'MACH120',
    description: 'Overtime expansion: Machine shop capacity expanded to 120 hours (+20 hrs)',
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
    description: 'Planned maintenance: Machine shop capacity reduced to 80 hours (-20 hrs)',
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
    description: 'Priority order: Product B minimum production raised to 90 units',
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
  scenarios,
  onUpdateScenarios,
  onRunDecision,
  isPending,
}: Props) {
  const [newId, setNewId] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [resId, setResId] = useState('MACHINE')
  const [resCap, setResCap] = useState('110')

  const addPreset = (preset: ScenarioSpec) => {
    if (scenarios.some((s) => s.scenario_id === preset.scenario_id)) return
    onUpdateScenarios([...scenarios, preset])
  }

  const removeScenario = (id: string) => {
    onUpdateScenarios(scenarios.filter((s) => s.scenario_id !== id))
  }

  const addCustomScenario = () => {
    if (!newId.trim()) return
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
      <div className="rounded-lg border border-border bg-panel p-4">
        <span className="text-xs font-semibold uppercase tracking-wider text-muted">
          Quick Load What-If Scenarios
        </span>
        <div className="mt-2 flex flex-wrap gap-2">
          {PRESET_SCENARIOS.map((preset) => {
            const isAdded = scenarios.some((s) => s.scenario_id === preset.scenario_id)
            return (
              <button
                key={preset.scenario_id}
                type="button"
                disabled={isAdded}
                onClick={() => addPreset(preset)}
                className={`rounded border px-3 py-1.5 text-xs font-medium transition-colors ${
                  isAdded
                    ? 'border-border bg-surface text-muted cursor-default'
                    : 'border-border bg-panel text-ink hover:border-accent hover:text-accent'
                }`}
              >
                + {preset.scenario_id} ({preset.changes[0]?.resource_id || preset.changes[0]?.product_id || 'PARAM'})
              </button>
            )
          })}
        </div>
      </div>

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
            Unmodified production plan: 100 hrs Machine, 100 hrs Labor, 40 max P_A, 50 max P_B.
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
                onClick={() => removeScenario(sc.scenario_id)}
                className="text-xs text-critical hover:underline"
              >
                Remove
              </button>
            </div>
            <div className="mt-2 flex flex-wrap gap-2 text-xs font-mono text-muted">
              {sc.changes.map((ch, idx) => (
                <span key={idx} className="rounded bg-surface px-2 py-0.5 border border-border">
                  {ch.kind === 'capacity'
                    ? `${ch.resource_id}.capacity = ${ch.capacity}`
                    : `${ch.product_id}.min_quantity = ${ch.min_quantity}`}
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
            type="text"
            value={newId}
            onChange={(e) => setNewId(e.target.value)}
            placeholder="Scenario ID (e.g. MACH110)"
            className="rounded border border-border bg-surface px-3 py-1.5 font-mono text-xs text-ink focus:border-accent focus:outline-none"
          />
          <select
            value={resId}
            onChange={(e) => setResId(e.target.value)}
            className="rounded border border-border bg-surface px-3 py-1.5 text-xs text-ink focus:border-accent focus:outline-none"
          >
            <option value="MACHINE">Resource: Machine Shop (MACHINE)</option>
            <option value="LABOR">Resource: Labor Force (LABOR)</option>
          </select>
          <input
            type="number"
            value={resCap}
            onChange={(e) => setResCap(e.target.value)}
            placeholder="New capacity"
            className="rounded border border-border bg-surface px-3 py-1.5 font-mono text-xs text-ink focus:border-accent focus:outline-none"
          />
          <button
            type="button"
            disabled={!newId.trim()}
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
