import { useState } from 'react'
import type { ExecutionState, FormulationState, IntakeState, ResultsState } from './types'

export interface ResultsAlternativesStepProps {
  intake: IntakeState
  formulation: FormulationState
  execution: ExecutionState
  results: ResultsState
  onPrev: () => void
  onNext: () => void
}

export function ResultsAlternativesStep({
  intake,
  formulation: _formulation,
  execution,
  results,
  onPrev,
  onNext,
}: ResultsAlternativesStepProps) {
  const [selectedOptionKey, setSelectedOptionKey] = useState<string>(
    results.recommendedOption ?? 'repair_now',
  )

  const options = results.optimizationResult?.options ?? []
  const selectedOpt = options.find((o) => o.option === selectedOptionKey) ?? options[0]
  const twin = results.twinResult

  const isInfeasible = execution.status === 'INFEASIBLE'
  const isUnknown = execution.status === 'UNKNOWN'
  const isFailed = execution.status === 'FAILED'

  return (
    <div className="flex flex-col gap-6">
      <header>
        <span className="text-xs font-mono uppercase tracking-wider text-accent">
          Workflow Stage 5 of 6
        </span>
        <h2 className="text-xl font-semibold text-ink">
          Comparative Solution Space &amp; Candidate Alternatives
        </h2>
        <p className="mt-1 text-sm text-muted">
          Multi-criteria Pareto trade-off assessment comparing immediate intervention vs deferred
          turnaround vs monitored operation for{' '}
          <strong className="font-mono text-ink">{intake.assetId}</strong>.
        </p>
      </header>

      {/* Special State Banners: Infeasible, Unknown, Failed */}
      {isInfeasible && (
        <div className="rounded-lg border border-critical bg-critical/10 p-4">
          <div className="flex items-center gap-2">
            <span className="rounded bg-critical px-2 py-0.5 text-xs font-bold text-white uppercase">
              INFEASIBLE
            </span>
            <h3 className="text-sm font-semibold text-critical">
              Hard Operational Constraints Violated by Candidate Options
            </h3>
          </div>
          <p className="mt-1.5 text-xs text-ink/90">
            The optimization engine identified that one or more candidate maintenance options exceed the
            stipulated safety, thermal, or budget boundaries. Review the constraint violations below.
          </p>
          <ul className="mt-2 list-disc pl-5 text-xs text-critical space-y-0.5">
            {execution.infeasibilityViolations?.map((v, idx) => (
              <li key={idx}>{v}</li>
            )) ?? (
              <li>Required zero LPS violations was breached during simulation.</li>
            )}
          </ul>
        </div>
      )}

      {isUnknown && (
        <div className="rounded-lg border border-warn bg-warn/10 p-4">
          <div className="flex items-center gap-2">
            <span className="rounded bg-warn px-2 py-0.5 text-xs font-bold text-surface uppercase">
              UNKNOWN / ABSTAINED
            </span>
            <h3 className="text-sm font-semibold text-warn">
              Diagnostic Engine Epistemic Abstention
            </h3>
          </div>
          <p className="mt-1.5 text-xs text-ink/90">
            {execution.unknownReason ??
              'Insufficient telemetry evidence to establish causal attribution between mechanical air leak and sensor drift. Advisory ranking reflects maximum uncertainty.'}
          </p>
        </div>
      )}

      {isFailed && (
        <div className="rounded-lg border border-critical bg-critical/10 p-4">
          <div className="flex items-center gap-2">
            <span className="rounded bg-critical px-2 py-0.5 text-xs font-bold text-white uppercase">
              FAILED
            </span>
            <h3 className="text-sm font-semibold text-critical">Solver Execution Divergence</h3>
          </div>
          <p className="mt-1 text-xs text-critical">
            {execution.errorDetail ?? 'The backend solver reported an unrecoverable failure.'}
          </p>
        </div>
      )}

      {/* Top Advisory Recommendation Banner */}
      {results.optimizationResult && (
        <section
          aria-labelledby="recommendation-heading"
          className="rounded-lg border border-border bg-panel p-4"
        >
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border pb-3">
            <div>
              <span className="text-xs font-mono uppercase tracking-wider text-muted">
                Advisory Outcome
              </span>
              <h3 id="recommendation-heading" className="text-base font-bold text-ink flex items-center gap-2">
                Recommended Alternative:{' '}
                <span className="text-accent underline font-mono">
                  {results.optimizationResult.recommended_option}
                </span>
              </h3>
            </div>
            <span className="rounded border border-ok/60 bg-ok/10 px-2 py-0.5 text-xs font-mono text-ok">
              Utility Score: {selectedOpt?.bounded_utility_score.toFixed(2) ?? '0.00'}
            </span>
          </div>
          <p className="mt-2 text-xs text-ink/90">
            {results.optimizationResult.recommendation_note}
          </p>
          {results.optimizationResult.uncertainty_warnings.length > 0 && (
            <div className="mt-2 text-[11px] text-muted space-y-0.5">
              {results.optimizationResult.uncertainty_warnings.map((w, i) => (
                <p key={i}>⚠️ {w}</p>
              ))}
            </div>
          )}
        </section>
      )}

      {/* Alternatives Comparison Matrix Table */}
      <section
        aria-labelledby="alternatives-heading"
        className="rounded-lg border border-border bg-panel p-4 overflow-hidden"
      >
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <h3 id="alternatives-heading" className="text-xs font-semibold uppercase tracking-wider text-muted">
            Candidate Maintenance Alternatives Trade-Off Matrix
          </h3>
          <span className="text-[11px] text-muted">
            Pareto Frontier: <strong className="text-ink font-mono">{results.paretoFrontier.join(', ') || 'None'}</strong>
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-border bg-surface/60 text-[11px] font-mono uppercase text-muted">
              <tr>
                <th className="py-2.5 px-3">Option</th>
                <th className="py-2.5 px-3">Feasibility</th>
                <th className="py-2.5 px-3">Pareto Status</th>
                <th className="py-2.5 px-3">Utility (0–1)</th>
                <th className="py-2.5 px-3">Exp. Cost (€)</th>
                <th className="py-2.5 px-3">LPS Risk (min)</th>
                <th className="py-2.5 px-3">Excess Energy</th>
                <th className="py-2.5 px-3">Disruption (h)</th>
                <th className="py-2.5 px-3 text-right">Inspect</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {options.map((opt) => {
                const isSelected = opt.option === selectedOptionKey
                return (
                  <tr
                    key={opt.option}
                    className={`transition-colors ${
                      isSelected ? 'bg-accent/10' : 'hover:bg-surface/40'
                    }`}
                  >
                    <td className="py-2.5 px-3 font-semibold text-ink">
                      <div>{opt.title}</div>
                      <span className="font-mono text-[10px] text-muted">{opt.option}</span>
                    </td>
                    <td className="py-2.5 px-3">
                      {opt.feasible ? (
                        <span className="rounded border border-ok/60 bg-ok/10 px-1.5 py-0.5 font-mono text-[10px] text-ok">
                          FEASIBLE
                        </span>
                      ) : (
                        <span className="rounded border border-critical/60 bg-critical/10 px-1.5 py-0.5 font-mono text-[10px] text-critical">
                          INFEASIBLE
                        </span>
                      )}
                    </td>
                    <td className="py-2.5 px-3">
                      {!opt.pareto_dominated ? (
                        <span className="rounded border border-accent/60 bg-accent/10 px-1.5 py-0.5 font-mono text-[10px] text-accent">
                          Optimal
                        </span>
                      ) : (
                        <span className="rounded border border-border px-1.5 py-0.5 font-mono text-[10px] text-muted">
                          Dominated
                        </span>
                      )}
                    </td>
                    <td className="py-2.5 px-3 font-mono font-bold text-ink">
                      {opt.bounded_utility_score.toFixed(2)}
                    </td>
                    <td className="py-2.5 px-3 font-mono text-ink">
                      €{opt.expected_total_cost_eur.toFixed(2)}
                    </td>
                    <td className="py-2.5 px-3 font-mono text-ink">
                      {opt.projected_lps_risk_minutes.toFixed(1)}m
                    </td>
                    <td className="py-2.5 px-3 font-mono text-ink">
                      {opt.projected_excess_energy_kwh.toFixed(1)} kWh
                    </td>
                    <td className="py-2.5 px-3 font-mono text-ink">
                      {opt.projected_unplanned_disruption_hours.toFixed(2)}h
                    </td>
                    <td className="py-2.5 px-3 text-right">
                      <button
                        type="button"
                        onClick={() => setSelectedOptionKey(opt.option)}
                        className={`rounded px-2 py-1 text-[11px] font-medium ${
                          isSelected
                            ? 'bg-accent text-white'
                            : 'border border-border text-muted hover:text-ink'
                        }`}
                      >
                        {isSelected ? 'Selected' : 'Select'}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {/* Selected Option Deep-Dive */}
        {selectedOpt && (
          <div className="mt-4 rounded border border-border bg-surface/50 p-3 text-xs">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/60 pb-2">
              <span className="font-semibold text-ink">
                Selected Option Evaluation: {selectedOpt.title}
              </span>
              <span className="font-mono text-muted">
                Status: {selectedOpt.feasible ? 'FEASIBLE' : 'INFEASIBLE'}
              </span>
            </div>
            <p className="mt-2 text-ink/90 leading-relaxed">{selectedOpt.rationale}</p>
            {selectedOpt.constraint_violations.length > 0 && (
              <div className="mt-2 rounded border border-critical/40 bg-critical/10 p-2 text-critical text-[11px]">
                <strong className="block">Constraint Violations:</strong>
                <ul className="list-disc pl-4 mt-0.5 space-y-0.5">
                  {selectedOpt.constraint_violations.map((cv, i) => (
                    <li key={i}>{cv}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </section>

      {/* Physics Digital Twin Trajectory Rollout */}
      {twin && (
        <section
          aria-labelledby="twin-trajectory-heading"
          className="rounded-lg border border-border bg-panel p-4"
        >
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border pb-3">
            <div>
              <h3 id="twin-trajectory-heading" className="text-xs font-semibold uppercase tracking-wider text-muted">
                Physics-Informed Digital Twin Rollout Telemetry
              </h3>
              <p className="text-xs text-ink font-medium mt-0.5">
                Scenario: <span className="font-mono text-accent">{twin.scenario}</span> | Asset:{' '}
                <span className="font-mono text-ink">{twin.asset_id}</span>
              </p>
            </div>
            <div className="flex items-center gap-2">
              <span
                className={`rounded border px-2 py-0.5 text-[11px] font-mono ${
                  twin.horizon_supported
                    ? 'border-ok/60 bg-ok/10 text-ok'
                    : 'border-warn/60 bg-warn/10 text-warn'
                }`}
              >
                {twin.horizon_support_status}
              </span>
            </div>
          </div>

          <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4 text-xs font-mono">
            <div className="rounded border border-border bg-surface p-2">
              <span className="text-muted block text-[10px]">PEAK TP3 PRESSURE</span>
              <span className="text-sm font-bold text-ink">
                {twin.summary.max_tp3_bar.toFixed(2)} bar
              </span>
            </div>
            <div className="rounded border border-border bg-surface p-2">
              <span className="text-muted block text-[10px]">FINAL OIL TEMP</span>
              <span className="text-sm font-bold text-ink">
                {twin.summary.final_oil_temp_c.toFixed(1)} °C
              </span>
            </div>
            <div className="rounded border border-border bg-surface p-2">
              <span className="text-muted block text-[10px]">MEAN LOAD DUTY</span>
              <span className="text-sm font-bold text-ink">
                {(twin.summary.mean_load_duty * 100).toFixed(0)}%
              </span>
            </div>
            <div className="rounded border border-border bg-surface p-2">
              <span className="text-muted block text-[10px]">LPS TIME &lt;7.0 BAR</span>
              <span className="text-sm font-bold text-ink">
                {twin.summary.lps_excursion_minutes.toFixed(1)} min
              </span>
            </div>
          </div>

          {/* Trajectory sample table */}
          {twin.trajectory.length > 0 && (
            <div className="mt-4 border-t border-border pt-3">
              <span className="text-[11px] font-medium text-muted">
                Simulated Trajectory Snapshot ({twin.trajectory.length} timesteps sampled)
              </span>
              <div className="mt-2 max-h-40 overflow-y-auto rounded border border-border font-mono text-[11px]">
                <table className="w-full text-left">
                  <thead className="bg-surface sticky top-0 text-[10px] text-muted border-b border-border">
                    <tr>
                      <th className="py-1 px-2.5">Time (min)</th>
                      <th className="py-1 px-2.5">TP3 (bar)</th>
                      <th className="py-1 px-2.5">Reservoirs (bar)</th>
                      <th className="py-1 px-2.5">Oil Temp (°C)</th>
                      <th className="py-1 px-2.5">Motor State</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/40">
                    {twin.trajectory.slice(0, 10).map((pt, idx) => (
                      <tr key={idx} className="hover:bg-surface/30">
                        <td className="py-1 px-2.5 text-muted">{pt.minute.toFixed(1)}</td>
                        <td className="py-1 px-2.5 text-accent font-semibold">
                          {pt.tp3_bar.toFixed(3)}
                        </td>
                        <td className="py-1 px-2.5 text-muted">
                          {pt.reservoirs_bar.toFixed(2)}
                        </td>
                        <td className="py-1 px-2.5 text-ink">{pt.oil_temp_c.toFixed(1)}</td>
                        <td className="py-1 px-2.5 text-muted">
                          {pt.motor_loaded ? 'LOADED' : 'UNLOADED'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {twin.horizon_warning && (
            <p className="mt-3 text-[11px] text-warn bg-warn/10 border border-warn/40 rounded p-2">
              ⚠️ {twin.horizon_warning}
            </p>
          )}
        </section>
      )}

      {/* Navigation actions */}
      <div className="flex items-center justify-between border-t border-border pt-4">
        <button
          type="button"
          onClick={onPrev}
          className="rounded border border-border px-3 py-1.5 text-xs text-muted hover:text-ink"
        >
          ← Back to Execution Progress
        </button>
        <button
          type="button"
          onClick={onNext}
          className="rounded bg-accent px-5 py-2.5 text-xs font-semibold text-white transition-opacity hover:opacity-90 shadow-sm"
        >
          Advance to Verification &amp; Explanation →
        </button>
      </div>
    </div>
  )
}
