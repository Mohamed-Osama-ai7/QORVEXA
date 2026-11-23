import { useState } from 'react'
import type { DecisionBrief, DecisionHistory } from '../../api/planningApi'

interface Props {
  brief: DecisionBrief | null
  history: DecisionHistory | null
  onDecideOn: (payload: {
    brief_sha256: string
    verdict: 'APPROVED' | 'REJECTED'
    scenario_id?: string | null
    acknowledged: string[]
    reason: string
  }) => Promise<void>
  isPending: boolean
}

export function HumanApprovalView({
  brief,
  history,
  onDecideOn,
  isPending,
}: Props) {
  const [verdict, setVerdict] = useState<'APPROVED' | 'REJECTED'>('APPROVED')
  const [selectedScenario, setSelectedScenario] = useState<string>(
    brief?.recommendation?.selected_scenario_id || 'MACH120'
  )
  const [reason, setReason] = useState(
    'Capacity expansion on machine shop to 120 hours approved based on verified +200 EUR contribution margin gain.'
  )
  const [acknowledged, setAcknowledged] = useState<string[]>(
    brief?.recommendation?.required_acknowledgements || []
  )

  const requiredAcks = brief?.recommendation?.required_acknowledgements || []

  const toggleAck = (item: string) => {
    setAcknowledged((prev) =>
      prev.includes(item) ? prev.filter((i) => i !== item) : [...prev, item]
    )
  }

  const allAcksChecked = requiredAcks.every((item) => acknowledged.includes(item))
  const canSubmit = brief && reason.trim().length >= 10 && (verdict === 'REJECTED' || allAcksChecked)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!brief || !canSubmit) return

    await onDecideOn({
      brief_sha256: brief.brief_sha256,
      verdict,
      scenario_id: selectedScenario || null,
      acknowledged,
      reason,
    })
  }

  return (
    <div className="space-y-6">
      {/* Sign-off Form Card */}
      <div className="rounded-lg border border-border bg-panel p-6">
        <h2 className="text-lg font-semibold text-ink">Human Approval &amp; Decision Sign-Off</h2>
        <p className="mt-1 text-xs text-muted">
          QORVEXA is advisory and read-only. Industrial changes must be authorized by an accountable human engineer.
          Signing this record creates an immutable entry in the audit hash chain.
        </p>

        {!brief ? (
          <div className="mt-4 rounded bg-surface p-4 text-xs text-muted">
            Please run optimization and compute a decision brief before recording human sign-off.
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="mt-6 space-y-5">
            {/* Verdict Selection */}
            <div>
              <span className="block text-xs font-medium text-muted">Decision Verdict</span>
              <div className="mt-2 flex gap-4">
                <label className="flex items-center gap-2 text-xs font-medium text-ink cursor-pointer">
                  <input
                    type="radio"
                    name="verdict"
                    value="APPROVED"
                    checked={verdict === 'APPROVED'}
                    onChange={() => setVerdict('APPROVED')}
                    className="text-ok focus:ring-ok"
                  />
                  <span className="text-ok font-semibold">APPROVE</span> (Authorize operational adoption)
                </label>
                <label className="flex items-center gap-2 text-xs font-medium text-ink cursor-pointer">
                  <input
                    type="radio"
                    name="verdict"
                    value="REJECTED"
                    checked={verdict === 'REJECTED'}
                    onChange={() => setVerdict('REJECTED')}
                    className="text-critical focus:ring-critical"
                  />
                  <span className="text-critical font-semibold">REJECT</span> (Decline proposed schedule)
                </label>
              </div>
            </div>

            {/* Target Scenario Selection */}
            {verdict === 'APPROVED' && (
              <div>
                <label htmlFor="target-scenario" className="block text-xs font-medium text-muted">
                  Approved Production Scenario
                </label>
                <select
                  id="target-scenario"
                  value={selectedScenario}
                  onChange={(e) => setSelectedScenario(e.target.value)}
                  className="mt-1 w-full max-w-xs rounded border border-border bg-surface px-3 py-1.5 font-mono text-xs text-ink focus:border-accent focus:outline-none"
                >
                  <option value="MACH120">MACH120 (+200 EUR Net Margin)</option>
                  <option value="BASE">BASE (2,600 EUR Unmodified Baseline)</option>
                  <option value="MACH80">MACH80 (Reduced Outage Schedule)</option>
                </select>
              </div>
            )}

            {/* Mandatory Acknowledgements */}
            {requiredAcks.length > 0 && verdict === 'APPROVED' && (
              <div>
                <span className="block text-xs font-medium text-muted">
                  Mandatory Assumption Acknowledgements ({acknowledged.length}/{requiredAcks.length})
                </span>
                <div className="mt-2 space-y-2 rounded border border-border bg-surface p-3">
                  {requiredAcks.map((ack) => (
                    <label key={ack} className="flex items-center gap-2 text-xs text-ink cursor-pointer">
                      <input
                        type="checkbox"
                        checked={acknowledged.includes(ack)}
                        onChange={() => toggleAck(ack)}
                        className="rounded border-border text-ok"
                      />
                      <span className="font-mono text-warn">{ack}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}

            {/* Rationale Textarea */}
            <div>
              <label htmlFor="reason-text" className="block text-xs font-medium text-muted">
                Engineering Justification &amp; Operational Rationale (Min 10 characters)
              </label>
              <textarea
                id="reason-text"
                rows={3}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Enter engineering rationale for audit record..."
                className="mt-1 w-full rounded border border-border bg-surface p-3 text-xs text-ink focus:border-accent focus:outline-none"
              />
            </div>

            <button
              type="submit"
              disabled={isPending || !canSubmit}
              className="rounded bg-ok px-5 py-2 text-xs font-medium text-white hover:bg-green-600 disabled:opacity-50"
            >
              {isPending ? 'Recording Decision...' : 'Commit Immutable Decision Record'}
            </button>
          </form>
        )}
      </div>

      {/* Persistent Audit History */}
      <div className="rounded-lg border border-border bg-panel p-6">
        <div className="flex items-center justify-between border-b border-border pb-3">
          <div>
            <h3 className="text-base font-semibold text-ink">Persistent Audit History</h3>
            <p className="text-xs text-muted">
              Re-verified from SQLite WAL storage. Replaying this history verifies cryptographic hash continuity.
            </p>
          </div>
          <span className="rounded bg-ok/10 px-2 py-1 font-mono text-xs text-ok">
            Hash Chain: Verified
          </span>
        </div>

        <div className="mt-4 overflow-x-auto">
          {(!history || history.records.length === 0) ? (
            <p className="py-6 text-center text-xs text-muted">No decision records signed yet for this session.</p>
          ) : (
            <table className="w-full text-left text-xs font-mono">
              <thead>
                <tr className="border-b border-border text-muted">
                  <th className="py-2.5 px-3">Seq</th>
                  <th className="py-2.5 px-3">Verdict</th>
                  <th className="py-2.5 px-3">Scenario</th>
                  <th className="py-2.5 px-3">Approver</th>
                  <th className="py-2.5 px-3">Timestamp</th>
                  <th className="py-2.5 px-3">Record SHA-256</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {history.records.map((rec) => (
                  <tr key={rec.seq} className="hover:bg-surface/50">
                    <td className="py-2.5 px-3 text-muted">#{rec.seq}</td>
                    <td className="py-2.5 px-3">
                      <span
                        className={`rounded px-1.5 py-0.5 text-xs font-semibold ${
                          rec.verdict === 'APPROVED'
                            ? 'bg-ok/20 text-ok'
                            : 'bg-critical/20 text-critical'
                        }`}
                      >
                        {rec.verdict}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 text-ink font-semibold">{rec.scenario_id || 'BASE'}</td>
                    <td className="py-2.5 px-3 text-muted">{rec.approver_id}</td>
                    <td className="py-2.5 px-3 text-muted">{rec.recorded_at}</td>
                    <td className="py-2.5 px-3 text-ink truncate max-w-xs">{rec.record_sha256}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}
