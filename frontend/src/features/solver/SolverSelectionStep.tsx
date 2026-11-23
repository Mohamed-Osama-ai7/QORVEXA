import type { FormulationState, IntakeState, SolverConfig, SolverMode } from './types'

export interface SolverSelectionStepProps {
  intake: IntakeState
  formulation: FormulationState
  config: SolverConfig
  onChange: (updater: (prev: SolverConfig) => SolverConfig) => void
  onExecute: () => void
  onPrev: () => void
}

interface SolverOption {
  mode: SolverMode
  title: string
  subtitle: string
  description: string
  endpoint: string
  statusBadge: string
  statusColor: string
  limitations: string
}

const SOLVER_OPTIONS: SolverOption[] = [
  {
    mode: 'composite',
    title: 'Composite Problem-Solving Pipeline (Recommended)',
    subtitle: 'End-to-End Orchestrated Industrial Reasoning Chain',
    description:
      'Chains diagnostic hypothesis ranking, physics ODE counterfactual simulation, and mathematical maintenance optimization into a unified evidence-backed decision package.',
    endpoint: 'Composite (/rca + /twin/simulate + /optimization/compare)',
    statusBadge: 'Fully Verified & Grounded',
    statusColor: 'border-ok/60 bg-ok/10 text-ok',
    limitations:
      'Deterministic execution. All outputs advisory; requires human field review.',
  },
  {
    mode: 'optimization',
    title: 'Mathematical Maintenance Optimizer',
    subtitle: 'Bounded Multi-Criteria Impact & Trade-Off Solver',
    description:
      'Compares repair_now, schedule_soon, and monitor_closely across cost, LPS reliability risk, energy draw, and downtime hours. Evaluates Pareto dominance and hard constraint feasibility.',
    endpoint: 'POST /api/v1/optimization/compare',
    statusBadge: 'Operational & Feasible',
    statusColor: 'border-accent/60 bg-accent/10 text-accent',
    limitations:
      'Evaluates bounded candidates. Gated by hard operational limits (oil temp, duty, budget).',
  },
  {
    mode: 'digital_twin',
    title: 'Physics-Informed Digital Twin Simulator',
    subtitle: 'Mass-Balance & Thermal ODE State Simulator',
    description:
      'Simulates reservoir pressure trajectories and compressor oil temperature rollouts under baseline, air leak, continued operation, or repair counterfactuals.',
    endpoint: 'POST /api/v1/twin/simulate',
    statusBadge: 'Calibrated Physical ODE',
    statusColor: 'border-accent/60 bg-accent/10 text-accent',
    limitations:
      'Closed-loop supported up to 30 min. 60 min unsupported CL. ≥120 min unsupported.',
  },
  {
    mode: 'rca',
    title: 'Diagnostic Root-Cause Analysis Engine',
    subtitle: 'Deterministic 5-Phase Evidence Scorer',
    description:
      'Evaluates pneumatic air leak, compressor failure, sensor malfunction, and abnormal high demand hypotheses against 5-phase evidence packages. Discloses contradictions and missing checks.',
    endpoint: 'POST /api/v1/anomaly/rca & /incidents/{id}/rca',
    statusBadge: 'Rule-Scored Index (Not %)',
    statusColor: 'border-ok/60 bg-ok/10 text-ok',
    limitations:
      'Support score is an uncalibrated rank, NOT a probability percentage.',
  },
  {
    mode: 'copilot',
    title: 'Controlled Industrial Copilot Orchestrator',
    subtitle: 'Citation-Grounded Read-Only Tool Synthesizer',
    description:
      'Deterministic reasoning agent orchestrating read-only tools across RAG documentation, Knowledge Graph, Twin, RCA, and Optimizer with strict safety firewall.',
    endpoint: 'POST /api/v1/copilot/query',
    statusBadge: 'Read-Only Advisory',
    statusColor: 'border-ok/60 bg-ok/10 text-ok',
    limitations:
      'Abstains out-of-domain. Actuation and injection commands strictly blocked.',
  },
]

export function SolverSelectionStep({
  intake,
  formulation: _formulation,
  config,
  onChange,
  onExecute,
  onPrev,
}: SolverSelectionStepProps) {
  const handleSelectMode = (mode: SolverMode) => {
    onChange((prev) => ({ ...prev, solverMode: mode }))
  }

  return (
    <div className="flex flex-col gap-6">
      <header>
        <span className="text-xs font-mono uppercase tracking-wider text-accent">
          Workflow Stage 3 of 6
        </span>
        <h2 className="text-xl font-semibold text-ink">Solver Engine Selection &amp; Boundary Audit</h2>
        <p className="mt-1 text-sm text-muted">
          Select the computational solver or composite pipeline to resolve the formulated problem for{' '}
          <strong className="font-mono text-ink">{intake.assetId}</strong>. Each solver displays its
          canonical API contract and known validity limits.
        </p>
      </header>

      {/* Solver Cards */}
      <section aria-label="Available Solvers" className="flex flex-col gap-3">
        {SOLVER_OPTIONS.map((opt) => {
          const isSelected = config.solverMode === opt.mode
          return (
            <div
              key={opt.mode}
              className={`rounded-lg border p-4 transition-all ${
                isSelected
                  ? 'border-accent bg-accent/10 shadow-sm'
                  : 'border-border bg-panel hover:border-border/80'
              }`}
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="flex items-center gap-3">
                  <input
                    type="radio"
                    name="solver-mode"
                    id={`solver-${opt.mode}`}
                    checked={isSelected}
                    onChange={() => handleSelectMode(opt.mode)}
                    className="h-4 w-4 accent-accent cursor-pointer"
                  />
                  <div>
                    <label
                      htmlFor={`solver-${opt.mode}`}
                      className="cursor-pointer font-semibold text-sm text-ink"
                    >
                      {opt.title}
                    </label>
                    <p className="text-xs text-muted">{opt.subtitle}</p>
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`rounded border px-2 py-0.5 text-[10px] font-mono ${opt.statusColor}`}>
                    {opt.statusBadge}
                  </span>
                  <span className="rounded border border-border bg-surface px-2 py-0.5 font-mono text-[10px] text-muted">
                    {opt.endpoint}
                  </span>
                </div>
              </div>

              <p className="mt-2 text-xs leading-relaxed text-ink/90 pl-7">{opt.description}</p>

              <div className="mt-2.5 flex items-center gap-2 border-t border-border/60 pt-2 text-[11px] text-muted pl-7">
                <span className="font-semibold text-warn">Boundary Constraint:</span>
                <span>{opt.limitations}</span>
              </div>
            </div>
          )
        })}
      </section>

      {/* Mode Specific Parameters */}
      <section
        aria-labelledby="parameters-heading"
        className="rounded-lg border border-border bg-panel p-4"
      >
        <h3 id="parameters-heading" className="text-xs font-semibold uppercase tracking-wider text-muted">
          Solver Run Parameters &amp; Counterfactual Configuration
        </h3>

        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 text-xs">
          <div>
            <label htmlFor="twin-scenario" className="block text-muted">
              Digital Twin Scenario Rollout
            </label>
            <select
              id="twin-scenario"
              value={config.twinScenario}
              onChange={(e) => {
                const val = e.target.value as any
                onChange((prev) => ({ ...prev, twinScenario: val }))
              }}
              className="mt-1 w-full rounded border border-border bg-surface px-2.5 py-1.5 text-xs text-ink"
            >
              <option value="leak">Pneumatic Air Leak (Injected Loss)</option>
              <option value="maintenance_repair">Counterfactual Maintenance Repair</option>
              <option value="continued_operation">Continued Unrepaired Operation</option>
              <option value="baseline">Baseline Nominal Operation</option>
            </select>
          </div>

          <div>
            <label htmlFor="twin-horizon" className="block text-muted">
              Twin Simulation Horizon
            </label>
            <select
              id="twin-horizon"
              value={config.twinHorizonMinutes}
              onChange={(e) => {
                const val = Number.parseInt(e.target.value, 10)
                onChange((prev) => ({ ...prev, twinHorizonMinutes: val }))
              }}
              className="mt-1 w-full rounded border border-border bg-surface px-2.5 py-1.5 font-mono text-xs text-ink"
            >
              <option value="10">10 min (Supported Closed-Loop)</option>
              <option value="30">30 min (Supported Closed-Loop)</option>
              <option value="60">60 min (Teacher-Forced Only / CL Unsupported)</option>
              <option value="120">120 min (Unsupported / Long Horizon)</option>
              <option value="240">240 min (Unsupported / Max Window)</option>
            </select>
            {config.twinHorizonMinutes > 30 && (
              <span className="mt-1 block text-[10px] text-warn">
                ⚠️ Horizons &gt;30m closed-loop are labelled unsupported in canonical API.
              </span>
            )}
          </div>

          <div>
            <label htmlFor="repair-minute" className="block text-muted">
              Intervention Timing (Minutes into Window)
            </label>
            <input
              id="repair-minute"
              type="number"
              step="1"
              min="1"
              max="120"
              value={config.twinRepairAtMinute}
              onChange={(e) => {
                const val = Number.parseFloat(e.target.value) || 2
                onChange((prev) => ({ ...prev, twinRepairAtMinute: val }))
              }}
              className="mt-1 w-full rounded border border-border bg-surface px-2.5 py-1.5 font-mono text-xs text-ink"
            />
            <span className="text-[10px] text-muted">Immediate: 2 min | Turnaround: 30 min</span>
          </div>
        </div>

        {/* Evaluation & Edge Case Simulation Controls */}
        <div className="mt-4 border-t border-border pt-3">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-muted">
            Evaluation Diagnostic Controls (Simulate Edge Behaviors)
          </span>
          <div className="mt-2 flex flex-wrap gap-4 text-xs">
            <label className="flex items-center gap-1.5 cursor-pointer text-muted hover:text-ink">
              <input
                type="checkbox"
                checked={config.simulateInfeasibility}
                onChange={(e) =>
                  onChange((prev) => ({
                    ...prev,
                    simulateInfeasibility: e.target.checked,
                    simulateUnknown: false,
                    simulateFailure: false,
                  }))
                }
                className="rounded border-border bg-surface text-critical"
              />
              <span>Force Infeasible Evaluation (Violate Constraints)</span>
            </label>

            <label className="flex items-center gap-1.5 cursor-pointer text-muted hover:text-ink">
              <input
                type="checkbox"
                checked={config.simulateUnknown}
                onChange={(e) =>
                  onChange((prev) => ({
                    ...prev,
                    simulateUnknown: e.target.checked,
                    simulateInfeasibility: false,
                    simulateFailure: false,
                  }))
                }
                className="rounded border-border bg-surface text-warn"
              />
              <span>Force Epistemic Abstention (UNKNOWN / Insufficient Evidence)</span>
            </label>

            <label className="flex items-center gap-1.5 cursor-pointer text-muted hover:text-ink">
              <input
                type="checkbox"
                checked={config.simulateFailure}
                onChange={(e) =>
                  onChange((prev) => ({
                    ...prev,
                    simulateFailure: e.target.checked,
                    simulateInfeasibility: false,
                    simulateUnknown: false,
                  }))
                }
                className="rounded border-border bg-surface text-critical"
              />
              <span>Force Numerical Divergence (FAILED State)</span>
            </label>
          </div>
        </div>
      </section>

      {/* Safety Invariant Reminder */}
      <div className="rounded border border-ok/40 bg-ok/5 p-3 text-xs text-ink flex items-center justify-between">
        <div>
          <strong className="text-ok">Safety Boundary Active:</strong> FactoryMind solvers operate in
          read-only advisory mode. They produce human-evaluated recommendations and never write to PLCs
          or actuate physical hardware.
        </div>
      </div>

      {/* Navigation actions */}
      <div className="flex items-center justify-between border-t border-border pt-4">
        <button
          type="button"
          onClick={onPrev}
          className="rounded border border-border px-3 py-1.5 text-xs text-muted hover:text-ink"
        >
          ← Back to Problem Formulation
        </button>
        <button
          type="button"
          onClick={onExecute}
          className="rounded bg-accent px-5 py-2.5 text-xs font-semibold text-white transition-opacity hover:opacity-90 shadow-sm"
        >
          Initialize Solver &amp; Run Pipeline →
        </button>
      </div>
    </div>
  )
}
