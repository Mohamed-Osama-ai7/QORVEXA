import type { ExecutionState, SolverConfig } from './types'

export interface ExecutionProgressStepProps {
  state: ExecutionState
  config: SolverConfig
  onRetry: () => void
  onCancel: () => void
  onNext: () => void
  onPrev: () => void
}

const STAGES = [
  'Problem Intake & Asset Security Context',
  'Physical Laws & Operational Constraints Binding',
  'Mathematical Solver & Engine Initialization',
  'Numerical ODE Rollout & Multi-Criteria Evaluation',
  'Feasibility Verification & Pareto Frontier Analysis',
  'Evidence Package Assembly & Decision Grounding',
]

export function ExecutionProgressStep({
  state,
  config: _config,
  onRetry,
  onCancel,
  onNext,
  onPrev,
}: ExecutionProgressStepProps) {
  const isRunning = state.status === 'RUNNING'
  const isCompleted = state.status === 'COMPLETED'
  const isInfeasible = state.status === 'INFEASIBLE'
  const isFailed = state.status === 'FAILED'
  const isUnknown = state.status === 'UNKNOWN'
  const isFinished = isCompleted || isInfeasible || isFailed || isUnknown

  const progressPercent = isCompleted
    ? 100
    : isInfeasible || isFailed || isUnknown
      ? 100
      : Math.min(95, Math.round((state.currentStageIndex / STAGES.length) * 100))

  return (
    <div className="flex flex-col gap-6">
      <header>
        <span className="text-xs font-mono uppercase tracking-wider text-accent">
          Workflow Stage 4 of 6
        </span>
        <h2 className="text-xl font-semibold text-ink">Solver Execution &amp; Pipeline Telemetry</h2>
        <p className="mt-1 text-sm text-muted">
          Real-time tracking of numerical state rollout, multi-criteria trade-off evaluations, and
          feasibility checks.
        </p>
      </header>

      {/* Main Execution Status Banner */}
      <section
        aria-live="polite"
        className={`rounded-lg border p-4 transition-colors ${
          isRunning
            ? 'border-accent/60 bg-accent/5'
            : isCompleted
              ? 'border-ok/60 bg-ok/5'
              : isInfeasible
                ? 'border-critical/60 bg-critical/10'
                : isFailed
                  ? 'border-critical/60 bg-critical/10'
                  : 'border-warn/60 bg-warn/10'
        }`}
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            {isRunning && (
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent" />
            )}
            <div>
              <span className="text-xs font-mono uppercase tracking-wider text-muted">
                Pipeline Status
              </span>
              <h3 className="text-lg font-bold text-ink">
                {isRunning && 'Solver Running — Processing Reasoning Chain…'}
                {isCompleted && 'Solver Completed Successfully'}
                {isInfeasible && 'Solver Output: INFEASIBLE (Hard Constraints Violated)'}
                {isFailed && 'Solver Execution: FAILED (Numerical or Network Fault)'}
                {isUnknown && 'Solver Output: UNKNOWN / ABSTAINED (Insufficient Evidence)'}
              </h3>
            </div>
          </div>
          <div className="flex items-center gap-3 font-mono text-xs">
            <span className="text-muted">Elapsed Time:</span>
            <span className="rounded border border-border bg-surface px-2 py-1 font-semibold text-ink">
              {(state.elapsedMs / 1000).toFixed(2)}s
            </span>
          </div>
        </div>

        {/* Progress Bar */}
        <div className="mt-4">
          <div className="flex justify-between text-xs text-muted font-mono mb-1">
            <span>
              Stage {Math.min(STAGES.length, state.currentStageIndex + 1)} of {STAGES.length}:{' '}
              {STAGES[Math.min(STAGES.length - 1, state.currentStageIndex)]}
            </span>
            <span>{progressPercent}%</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-surface">
            <div
              className={`h-full transition-all duration-300 ${
                isCompleted
                  ? 'bg-ok'
                  : isInfeasible
                    ? 'bg-critical'
                    : isFailed
                      ? 'bg-critical'
                      : isUnknown
                        ? 'bg-warn'
                        : 'bg-accent'
              }`}
              style={{ width: `${progressPercent}%` }}
            />
          </div>
        </div>

        {/* Infeasible alert detail */}
        {isInfeasible && (
          <div className="mt-4 rounded border border-critical/50 bg-critical/10 p-3 text-xs text-ink">
            <p className="font-semibold text-critical">Constraint Infeasibility Explanations:</p>
            <ul className="mt-1.5 list-disc space-y-1 pl-4 text-critical/90">
              {state.infeasibilityViolations?.map((v, i) => (
                <li key={i}>{v}</li>
              )) ?? (
                <li>All candidate options violate the specified operational limits or budget ceilings.</li>
              )}
            </ul>
            <p className="mt-2 text-muted">
              You can still review candidate rankings and trade-offs in Step 5, or return to Step 2 to relax
              bounds.
            </p>
          </div>
        )}

        {/* Failed alert detail */}
        {isFailed && (
          <div className="mt-4 rounded border border-critical/50 bg-critical/10 p-3 text-xs text-ink">
            <p className="font-semibold text-critical">Execution Error Trace:</p>
            <p className="mt-1 font-mono text-xs text-critical/90">
              {state.errorDetail ?? 'Numerical divergence encountered during ODE simulation.'}
            </p>
            <div className="mt-3">
              <button
                type="button"
                onClick={onRetry}
                className="rounded bg-critical/20 border border-critical px-3 py-1 font-semibold text-critical hover:bg-critical/30"
              >
                Retry Execution
              </button>
            </div>
          </div>
        )}

        {/* Unknown / Abstained detail */}
        {isUnknown && (
          <div className="mt-4 rounded border border-warn/50 bg-warn/10 p-3 text-xs text-ink">
            <p className="font-semibold text-warn">Epistemic Abstention Rationale:</p>
            <p className="mt-1 text-ink/90">
              {state.unknownReason ??
                'The RCA diagnostic engine abstained from ranking hypotheses because sensor unobservability (e.g. missing DV_pressure telemetry) prevents distinguishing between a mechanical valve failure and sensor drift.'}
            </p>
          </div>
        )}
      </section>

      {/* Sequential Pipeline Stages Grid */}
      <section
        aria-labelledby="pipeline-stages-heading"
        className="rounded-lg border border-border bg-panel p-4"
      >
        <h3
          id="pipeline-stages-heading"
          className="text-xs font-semibold uppercase tracking-wider text-muted mb-3"
        >
          Pipeline Stage Progression
        </h3>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 text-xs">
          {STAGES.map((title, idx) => {
            const isStageDone = idx < state.currentStageIndex || isCompleted
            const isStageActive = idx === state.currentStageIndex && isRunning

            return (
              <div
                key={idx}
                className={`flex items-start gap-2.5 rounded border p-2.5 transition-colors ${
                  isStageDone
                    ? 'border-ok/40 bg-ok/5 text-ink'
                    : isStageActive
                      ? 'border-accent bg-accent/10 text-ink shadow-sm'
                      : 'border-border/60 bg-surface/40 text-muted opacity-70'
                }`}
              >
                <div
                  className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-mono ${
                    isStageDone
                      ? 'bg-ok text-surface font-bold'
                      : isStageActive
                        ? 'bg-accent text-white animate-pulse'
                        : 'border border-border text-muted'
                  }`}
                >
                  {isStageDone ? '✓' : idx + 1}
                </div>
                <div className="min-w-0">
                  <p className="font-medium truncate">{title}</p>
                  <span className="text-[10px] text-muted">
                    {isStageDone ? 'Completed' : isStageActive ? 'Running…' : 'Queued'}
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      </section>

      {/* Terminal Monospace Execution Log Stream */}
      <section
        aria-labelledby="logs-heading"
        className="rounded-lg border border-border bg-panel p-4"
      >
        <div className="flex items-center justify-between pb-2 border-b border-border">
          <h3 id="logs-heading" className="text-xs font-semibold uppercase tracking-wider text-muted">
            Engine Telemetry &amp; Verification Logs
          </h3>
          <span className="font-mono text-[11px] text-muted">
            {state.stageLogs.length} events logged
          </span>
        </div>
        <div
          aria-label="Execution telemetry log"
          className="mt-3 h-48 overflow-y-auto rounded border border-border bg-surface p-3 font-mono text-[11px] leading-relaxed text-ink space-y-1"
        >
          {state.stageLogs.length === 0 ? (
            <p className="text-muted/60">Waiting for solver telemetry…</p>
          ) : (
            state.stageLogs.map((log) => (
              <div key={log.id} className="flex gap-2">
                <span className="text-muted/70 shrink-0">[{log.timestamp}]</span>
                <span
                  className={`shrink-0 font-semibold ${
                    log.level === 'error'
                      ? 'text-critical'
                      : log.level === 'warn'
                        ? 'text-warn'
                        : log.level === 'success'
                          ? 'text-ok'
                          : 'text-accent'
                  }`}
                >
                  [{log.level.toUpperCase()}]
                </span>
                <span className="text-muted shrink-0">({log.stageName}):</span>
                <span className="text-ink break-all">{log.message}</span>
              </div>
            ))
          )}
        </div>
      </section>

      {/* Navigation actions */}
      <div className="flex items-center justify-between border-t border-border pt-4">
        <button
          type="button"
          onClick={onPrev}
          disabled={isRunning}
          className="rounded border border-border px-3 py-1.5 text-xs text-muted hover:text-ink disabled:opacity-40"
        >
          ← Adjust Solver Mode
        </button>

        <div className="flex items-center gap-3">
          {isRunning && (
            <button
              type="button"
              onClick={onCancel}
              className="rounded border border-critical/60 px-3 py-1.5 text-xs text-critical hover:bg-critical/10"
            >
              Abort Execution
            </button>
          )}

          {isFinished && (
            <button
              type="button"
              onClick={onNext}
              className="rounded bg-accent px-5 py-2.5 text-xs font-semibold text-white transition-opacity hover:opacity-90 shadow-sm"
            >
              View Results &amp; Alternatives →
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
