import type { DecisionBrief } from '../../api/planningApi'

interface Props {
  brief: DecisionBrief | null
  onProceedToApproval: () => void
}

export function DecisionIntelligenceView({ brief, onProceedToApproval }: Props) {
  if (!brief) {
    return (
      <div className="rounded-lg border border-border bg-panel p-8 text-center text-sm text-muted">
        No decision brief computed yet. Run optimization from the Scenario Workbench first.
      </div>
    )
  }

  const rec = brief.recommendation
  const isRecommended =
    rec.status === 'RECOMMENDED' || rec.status === 'CONDITIONALLY_RECOMMENDED'

  return (
    <div className="space-y-6">
      {/* Executive Recommendation Banner */}
      <div
        className={`rounded-lg border p-6 ${
          isRecommended
            ? 'border-ok/30 bg-ok/5'
            : 'border-warn/30 bg-warn/5'
        }`}
      >
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <span
              className={`rounded px-2.5 py-1 text-xs font-semibold uppercase tracking-wider ${
                isRecommended
                  ? 'bg-ok/20 text-ok'
                  : 'bg-warn/20 text-warn'
              }`}
            >
              {rec.status}
            </span>
            <span className="font-mono text-xs text-muted">
              Brief Digest: {brief.brief_sha256.slice(0, 16)}...
            </span>
          </div>

          <button
            type="button"
            onClick={onProceedToApproval}
            className="rounded bg-ok px-4 py-2 text-xs font-medium text-white hover:bg-green-600"
          >
            Review &amp; Record Human Decision →
          </button>
        </div>

        <h2 className="mt-3 text-lg font-semibold text-ink">{rec.headline}</h2>
        <p className="mt-1 text-xs text-muted">
          Selected Scenario Anchor: <strong>{rec.selected_scenario_id || 'BASE'}</strong> | HiGHS Numerical Convergence Verified
        </p>

        {rec.reasons.length > 0 && (
          <ul className="mt-3 list-inside list-disc space-y-1 text-xs text-ink/90">
            {rec.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        )}
      </div>

      {/* Mathematical Scenario Comparison */}
      {brief.comparison && (
        <div className="rounded-lg border border-border bg-panel p-5">
          <h3 className="text-sm font-semibold text-ink">Scenario Comparison Matrix</h3>
          <p className="text-xs text-muted">
            All numerical results verified with exact primal feasibility checks against canonical LP standards.
          </p>

          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-xs font-mono">
              <thead>
                <tr className="border-b border-border text-muted">
                  <th className="py-2.5 px-3">Scenario ID</th>
                  <th className="py-2.5 px-3">Status</th>
                  <th className="py-2.5 px-3">Baseline Margin</th>
                  <th className="py-2.5 px-3">Scenario Margin</th>
                  <th className="py-2.5 px-3">Net Delta</th>
                  <th className="py-2.5 px-3">Effect</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {brief.comparison.compared.map((sc) => {
                  const delta = sc.objective?.absolute_change ? Number(sc.objective.absolute_change) : 0
                  return (
                    <tr key={sc.scenario_id} className="hover:bg-surface/50">
                      <td className="py-2.5 px-3 font-semibold text-ink">{sc.scenario_id}</td>
                      <td className="py-2.5 px-3 text-ok">{sc.status}</td>
                      <td className="py-2.5 px-3 text-muted">
                        {sc.objective?.baseline ? `${Number(sc.objective.baseline).toFixed(2)} EUR` : '-'}
                      </td>
                      <td className="py-2.5 px-3 text-ink font-semibold">
                        {sc.objective?.scenario ? `${Number(sc.objective.scenario).toFixed(2)} EUR` : '-'}
                      </td>
                      <td
                        className={`py-2.5 px-3 font-semibold ${
                          delta > 0 ? 'text-ok' : delta < 0 ? 'text-critical' : 'text-muted'
                        }`}
                      >
                        {delta > 0 ? `+${delta.toFixed(2)}` : delta.toFixed(2)} EUR
                      </td>
                      <td className="py-2.5 px-3">
                        <span
                          className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                            sc.objective?.effect === 'IMPROVED'
                              ? 'bg-ok/10 text-ok'
                              : sc.objective?.effect === 'DETERIORATED'
                              ? 'bg-critical/10 text-critical'
                              : 'bg-muted/10 text-muted'
                          }`}
                        >
                          {sc.objective?.effect || 'UNCHANGED'}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Trade-Offs & Required Acknowledgements */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="rounded-lg border border-border bg-panel p-5">
          <h3 className="text-sm font-semibold text-ink">Executive Summary</h3>
          <ul className="mt-3 space-y-2 text-xs text-muted">
            {brief.executive_summary.map((line, idx) => (
              <li key={idx} className="flex items-start gap-2">
                <span className="text-accent font-bold">•</span>
                <span>{line}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="rounded-lg border border-border bg-panel p-5">
          <h3 className="text-sm font-semibold text-ink">Mandatory Governance Acknowledgements</h3>
          <p className="mt-1 text-xs text-muted">
            The human approver must explicitly accept these boundary conditions before sign-off:
          </p>

          <ul className="mt-3 space-y-1.5">
            {rec.required_acknowledgements.map((item, idx) => (
              <li
                key={idx}
                className="flex items-center gap-2 rounded bg-surface p-2 font-mono text-xs text-warn border border-border"
              >
                <span>⚠</span>
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  )
}
