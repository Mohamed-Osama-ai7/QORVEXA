import { useState } from 'react'
import type { FormulationState, IntakeState } from './types'

export interface ProblemFormulationStepProps {
  intake: IntakeState
  state: FormulationState
  onChange: (updater: (prev: FormulationState) => FormulationState) => void
  onNext: () => void
  onPrev: () => void
}

export function ProblemFormulationStep({
  intake,
  state,
  onChange,
  onNext,
  onPrev,
}: ProblemFormulationStepProps) {
  const [formulationLocked, setFormulationLocked] = useState(false)

  // Compute normalized weights
  const totalWeight = state.costWeight + state.reliabilityWeight + state.downtimeWeight
  const normCost = totalWeight > 0 ? (state.costWeight / totalWeight).toFixed(2) : '0.33'
  const normRel = totalWeight > 0 ? (state.reliabilityWeight / totalWeight).toFixed(2) : '0.33'
  const normDown = totalWeight > 0 ? (state.downtimeWeight / totalWeight).toFixed(2) : '0.33'

  return (
    <div className="flex flex-col gap-6">
      <header>
        <span className="text-xs font-mono uppercase tracking-wider text-accent">
          Workflow Stage 2 of 6
        </span>
        <h2 className="text-xl font-semibold text-ink">
          Mathematical Problem Formulation &amp; Constraint Review
        </h2>
        <p className="mt-1 text-sm text-muted">
          Formulate governing equations, hard physical laws, operational bounds, and multi-criteria
          objective weights for asset <strong className="font-mono text-ink">{intake.assetId}</strong>.
        </p>
      </header>

      {/* Asset Envelope & Operating Laws */}
      <section
        aria-labelledby="formulation-laws-heading"
        className="rounded-lg border border-border bg-panel p-4"
      >
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border pb-3">
          <h3
            id="formulation-laws-heading"
            className="text-xs font-semibold uppercase tracking-wider text-muted"
          >
            Physical Envelope &amp; Governing Invariants
          </h3>
          <span className="rounded border border-ok/60 bg-ok/10 px-2 py-0.5 text-[11px] font-mono text-ok">
            Laws Verified Active
          </span>
        </div>

        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4 text-xs">
          <div className="rounded border border-border/80 bg-surface/60 p-2.5">
            <span className="text-muted">Governor Hysteresis</span>
            <p className="mt-1 font-mono font-medium text-ink">8.2 bar cut-in / 10.0 bar cut-out</p>
            <span className="text-[10px] text-muted">Pneumatic reservoir control limits</span>
          </div>
          <div className="rounded border border-border/80 bg-surface/60 p-2.5">
            <span className="text-muted">Safety Relief Boundary</span>
            <p className="mt-1 font-mono font-medium text-ink">0.0 bar floor / 15.0 bar relief</p>
            <span className="text-[10px] text-muted">Non-negative physical clamp boundary</span>
          </div>
          <div className="rounded border border-border/80 bg-surface/60 p-2.5">
            <span className="text-muted">Rated Motor Power</span>
            <p className="mt-1 font-mono font-medium text-ink">{state.ratedApuPowerKw.toFixed(1)} kW (Loaded)</p>
            <span className="text-[10px] text-muted">
              Unloaded proxy: {(state.ratedApuPowerKw * state.unloadedPowerFraction).toFixed(2)} kW
            </span>
          </div>
          <div className="rounded border border-border/80 bg-surface/60 p-2.5">
            <span className="text-muted">Thermal Operating Range</span>
            <p className="mt-1 font-mono font-medium text-ink">Normal: &lt;75°C | Alarm: 95°C</p>
            <span className="text-[10px] text-muted">Emergency trip threshold: 105°C</span>
          </div>
        </div>
      </section>

      {/* Operational Constraints */}
      <section
        aria-labelledby="constraints-heading"
        className="rounded-lg border border-border bg-panel p-4"
      >
        <h3 id="constraints-heading" className="text-xs font-semibold uppercase tracking-wider text-muted">
          Operational Safety &amp; Feasibility Constraints
        </h3>
        <p className="mt-1 text-xs text-muted">
          Solutions that violate these bounds will be marked as{' '}
          <strong className="text-critical">INFEASIBLE</strong> by the solver.
        </p>

        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <div className="flex flex-col justify-between rounded border border-border bg-surface p-3">
            <div>
              <span className="text-xs font-medium text-ink">Zero LPS Excursions Policy</span>
              <p className="mt-0.5 text-[11px] text-muted">
                Require zero low-pressure switch (LPS &lt; 7.0 bar) drops during operation.
              </p>
            </div>
            <label className="mt-3 flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={state.requireZeroLpsViolation}
                onChange={(e) =>
                  onChange((prev) => ({ ...prev, requireZeroLpsViolation: e.target.checked }))
                }
                className="h-4 w-4 rounded border-border bg-surface text-accent"
              />
              <span className="text-xs font-medium text-ink">
                {state.requireZeroLpsViolation ? 'Enforced (Strict)' : 'Relaxed (Advisory)'}
              </span>
            </label>
          </div>

          <div className="rounded border border-border bg-surface p-3">
            <label htmlFor="max-duty-input" className="block text-xs font-medium text-ink">
              Maximum Allowable Duty Cycle
            </label>
            <p className="mt-0.5 text-[11px] text-muted">
              Compressor duty above this limit triggers feasibility violations.
            </p>
            <div className="mt-2 flex items-center gap-2">
              <input
                id="max-duty-input"
                type="number"
                step="0.05"
                min="0.5"
                max="1.0"
                value={state.maxAllowableDuty}
                onChange={(e) => {
                  const val = Number.parseFloat(e.target.value) || 0.85
                  onChange((prev) => ({ ...prev, maxAllowableDuty: val }))
                }}
                className="w-24 rounded border border-border bg-panel px-2 py-1 font-mono text-xs text-ink"
              />
              <span className="text-xs text-muted font-mono">
                ({(state.maxAllowableDuty * 100).toFixed(0)}% max)
              </span>
            </div>
          </div>

          <div className="rounded border border-border bg-surface p-3">
            <label htmlFor="max-oil-input" className="block text-xs font-medium text-ink">
              Max Allowable Oil Temperature (°C)
            </label>
            <p className="mt-0.5 text-[11px] text-muted">
              Threshold above which thermal degradation occurs.
            </p>
            <div className="mt-2 flex items-center gap-2">
              <input
                id="max-oil-input"
                type="number"
                step="1"
                min="60"
                max="130"
                value={state.maxAllowableOilTempC}
                onChange={(e) => {
                  const val = Number.parseFloat(e.target.value) || 95
                  onChange((prev) => ({ ...prev, maxAllowableOilTempC: val }))
                }}
                className="w-24 rounded border border-border bg-panel px-2 py-1 font-mono text-xs text-ink"
              />
              <span className="text-xs text-muted font-mono">°C</span>
            </div>
          </div>
        </div>

        {/* Economic / Disruption limits */}
        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="rounded border border-border bg-surface p-3">
            <label htmlFor="budget-input" className="block text-xs font-medium text-ink">
              Max Allowable Maintenance Budget Ceiling (€)
            </label>
            <p className="mt-0.5 text-[11px] text-muted">
              Optional cost ceiling. Interventions exceeding this will be flagged infeasible.
            </p>
            <input
              id="budget-input"
              type="number"
              placeholder="e.g. 5000 (blank = unlimited)"
              value={state.maxAllowableCostEur ?? ''}
              onChange={(e) => {
                const val = e.target.value ? Number.parseFloat(e.target.value) : null
                onChange((prev) => ({ ...prev, maxAllowableCostEur: val }))
              }}
              className="mt-2 w-full rounded border border-border bg-panel px-2.5 py-1.5 font-mono text-xs text-ink"
            />
          </div>

          <div className="rounded border border-border bg-surface p-3">
            <label htmlFor="downtime-input" className="block text-xs font-medium text-ink">
              Max Allowable Service Outage Downtime (Hours)
            </label>
            <p className="mt-0.5 text-[11px] text-muted">
              Optional schedule outage ceiling for depot interventions.
            </p>
            <input
              id="downtime-input"
              type="number"
              step="0.5"
              placeholder="e.g. 6.0 (blank = unlimited)"
              value={state.maxAllowableDowntimeHours ?? ''}
              onChange={(e) => {
                const val = e.target.value ? Number.parseFloat(e.target.value) : null
                onChange((prev) => ({ ...prev, maxAllowableDowntimeHours: val }))
              }}
              className="mt-2 w-full rounded border border-border bg-panel px-2.5 py-1.5 font-mono text-xs text-ink"
            />
          </div>
        </div>
      </section>

      {/* Economic Assumptions */}
      <section
        aria-labelledby="economic-assumptions-heading"
        className="rounded-lg border border-border bg-panel p-4"
      >
        <h3
          id="economic-assumptions-heading"
          className="text-xs font-semibold uppercase tracking-wider text-muted"
        >
          Configurable Economic &amp; Power Rates (Transparency Invariant)
        </h3>
        <p className="mt-1 text-xs text-muted">
          Financial quantities are transparently tagged under tier{' '}
          <span className="font-mono text-ink">configurable_economic_assumption</span>.
        </p>

        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4 text-xs">
          <div>
            <label htmlFor="elec-input" className="text-muted">
              Electricity Tariff (€/kWh)
            </label>
            <input
              id="elec-input"
              type="number"
              step="0.01"
              min="0.01"
              max="2.0"
              value={state.electricityEurPerKwh}
              onChange={(e) => {
                const val = Number.parseFloat(e.target.value) || 0.18
                onChange((prev) => ({ ...prev, electricityEurPerKwh: val }))
              }}
              className="mt-1 w-full rounded border border-border bg-surface px-2 py-1 font-mono text-ink"
            />
          </div>
          <div>
            <label htmlFor="planned-maint-input" className="text-muted">
              Planned Depot Maintenance (€)
            </label>
            <input
              id="planned-maint-input"
              type="number"
              step="50"
              min="50"
              max="50000"
              value={state.plannedDepotMaintenanceEur}
              onChange={(e) => {
                const val = Number.parseFloat(e.target.value) || 650
                onChange((prev) => ({ ...prev, plannedDepotMaintenanceEur: val }))
              }}
              className="mt-1 w-full rounded border border-border bg-surface px-2 py-1 font-mono text-ink"
            />
          </div>
          <div>
            <label htmlFor="disrupt-rate-input" className="text-muted">
              Unplanned Disruption (€/hr)
            </label>
            <input
              id="disrupt-rate-input"
              type="number"
              step="100"
              min="100"
              max="100000"
              value={state.unplannedServiceDisruptionEurPerHr}
              onChange={(e) => {
                const val = Number.parseFloat(e.target.value) || 1800
                onChange((prev) => ({ ...prev, unplannedServiceDisruptionEurPerHr: val }))
              }}
              className="mt-1 w-full rounded border border-border bg-surface px-2 py-1 font-mono text-ink"
            />
          </div>
          <div>
            <label htmlFor="mon-check-input" className="text-muted">
              Enhanced Monitoring Check (€)
            </label>
            <input
              id="mon-check-input"
              type="number"
              step="10"
              min="10"
              max="10000"
              value={state.monitoringCheckEur}
              onChange={(e) => {
                const val = Number.parseFloat(e.target.value) || 120
                onChange((prev) => ({ ...prev, monitoringCheckEur: val }))
              }}
              className="mt-1 w-full rounded border border-border bg-surface px-2 py-1 font-mono text-ink"
            />
          </div>
        </div>
      </section>

      {/* Multi-Objective Optimization Weights */}
      <section
        aria-labelledby="weights-heading"
        className="rounded-lg border border-border bg-panel p-4"
      >
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border pb-3">
          <h3 id="weights-heading" className="text-xs font-semibold uppercase tracking-wider text-muted">
            Multi-Objective Trade-Off Weights
          </h3>
          <span className="text-xs font-mono text-muted">
            Normalized: Cost: <strong className="text-ink">{normCost}</strong> | Reliability:{' '}
            <strong className="text-ink">{normRel}</strong> | Downtime:{' '}
            <strong className="text-ink">{normDown}</strong>
          </span>
        </div>

        <div className="mt-4 grid grid-cols-1 gap-6 sm:grid-cols-3">
          <div>
            <div className="flex justify-between text-xs">
              <label htmlFor="cost-weight-input" className="font-medium text-ink">
                Cost Minimization Weight
              </label>
              <span className="font-mono text-accent">{state.costWeight.toFixed(2)}</span>
            </div>
            <input
              id="cost-weight-input"
              type="range"
              min="0.05"
              max="1.0"
              step="0.05"
              value={state.costWeight}
              onChange={(e) => {
                const val = Number.parseFloat(e.target.value)
                onChange((prev) => ({ ...prev, costWeight: val }))
              }}
              className="mt-2 w-full accent-accent"
            />
            <p className="mt-1 text-[10px] text-muted">Energy consumption and repair invoice costs</p>
          </div>

          <div>
            <div className="flex justify-between text-xs">
              <label htmlFor="rel-weight-input" className="font-medium text-ink">
                Reliability &amp; LPS Margin Weight
              </label>
              <span className="font-mono text-accent">{state.reliabilityWeight.toFixed(2)}</span>
            </div>
            <input
              id="rel-weight-input"
              type="range"
              min="0.05"
              max="1.0"
              step="0.05"
              value={state.reliabilityWeight}
              onChange={(e) => {
                const val = Number.parseFloat(e.target.value)
                onChange((prev) => ({ ...prev, reliabilityWeight: val }))
              }}
              className="mt-2 w-full accent-accent"
            />
            <p className="mt-1 text-[10px] text-muted">
              Pneumatic pressure safety and low-pressure avoidance
            </p>
          </div>

          <div>
            <div className="flex justify-between text-xs">
              <label htmlFor="down-weight-input" className="font-medium text-ink">
                Downtime Minimization Weight
              </label>
              <span className="font-mono text-accent">{state.downtimeWeight.toFixed(2)}</span>
            </div>
            <input
              id="down-weight-input"
              type="range"
              min="0.05"
              max="1.0"
              step="0.05"
              value={state.downtimeWeight}
              onChange={(e) => {
                const val = Number.parseFloat(e.target.value)
                onChange((prev) => ({ ...prev, downtimeWeight: val }))
              }}
              className="mt-2 w-full accent-accent"
            />
            <p className="mt-1 text-[10px] text-muted">
              Service continuity and avoidance of unplanned delays
            </p>
          </div>
        </div>
      </section>

      {/* Epistemic Identifiability Disclosure */}
      <section className="rounded border border-border/80 bg-surface/40 p-3 text-xs">
        <h4 className="font-semibold text-ink">Epistemic Parameter Identifiability Disclosure</h4>
        <div className="mt-1.5 grid grid-cols-1 gap-2 sm:grid-cols-2 text-[11px] text-muted">
          <div>
            <strong className="text-ink">Observable Telemetry:</strong> Reservoir pressure (p),
            compressor motor current (i_motor), oil temperature (T_oil), and dryer discharge valve
            status (u_valve).
          </div>
          <div>
            <strong className="text-ink">Unidentifiable In Situ:</strong> Ambient air temperature
            (T_amb), pneumatic line friction factor (&lambda;), and internal check-valve seat
            microscopic leakage.
          </div>
        </div>
      </section>

      {/* Navigation actions */}
      <div className="flex items-center justify-between border-t border-border pt-4">
        <button
          type="button"
          onClick={onPrev}
          className="rounded border border-border px-3 py-1.5 text-xs text-muted hover:text-ink"
        >
          ← Back to Problem Intake
        </button>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-muted cursor-pointer">
            <input
              type="checkbox"
              checked={formulationLocked}
              onChange={(e) => setFormulationLocked(e.target.checked)}
              className="rounded border-border text-accent"
            />
            <span>Lock formulation review</span>
          </label>
          <button
            type="button"
            onClick={onNext}
            className="rounded bg-accent px-4 py-2 text-xs font-semibold text-white transition-opacity hover:opacity-90"
          >
            Confirm Formulation &amp; Select Solver →
          </button>
        </div>
      </div>
    </div>
  )
}
