import { useState } from 'react'
import type { DecideOnRequest, DecisionBrief, DecisionHistory } from '../../api/planningApi'

interface Props {
  brief: DecisionBrief | null
  history: DecisionHistory | null
  canDecide: boolean
  onDecideOn: (payload: DecideOnRequest) => Promise<void>
  isPending: boolean
}

export function HumanApprovalView(props: Props) {
  return <ApprovalPanel key={props.brief?.brief_sha256} {...props} />
}

function ApprovalPanel({ brief, history, canDecide, onDecideOn, isPending }: Props) {
  const [verdict, setVerdict] = useState<DecideOnRequest['verdict'] | null>(null)
  const [reason, setReason] = useState('')
  const [acknowledged, setAcknowledged] = useState<string[]>([])

  const recommendation = brief?.recommendation
  const selectedScenario = recommendation?.recommended_scenario_id
  const approvable = Boolean(selectedScenario) &&
    (recommendation?.status === 'RECOMMENDED' || recommendation?.status === 'CONDITIONALLY_RECOMMENDED')
  const requiredAcks = recommendation?.unresolved_assumptions ?? []
  const allAcksChecked = requiredAcks.every((item) => acknowledged.includes(item))
  const canSubmit = canDecide && reason.trim().length >= 10 &&
    (verdict === 'REJECTED' || (verdict === 'APPROVED' && approvable && allAcksChecked))

  const toggleAck = (item: string) => {
    setAcknowledged((prev) =>
      prev.includes(item) ? prev.filter((i) => i !== item) : [...prev, item],
    )
  }

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!brief || !verdict || !canSubmit || isPending) return

    await onDecideOn({
      brief_sha256: brief.brief_sha256,
      verdict,
      scenario_id: verdict === 'APPROVED' ? selectedScenario : null,
      acknowledged: verdict === 'APPROVED' ? acknowledged : [],
      reason: reason.trim(),
    })
  }

  return (
    <div className="space-y-6">
      {/* Sign-off Form Card */}
      <div className="rounded-lg border border-border bg-panel p-6">
        <h2 className="text-lg font-semibold text-ink">Human Approval &amp; Decision Sign-Off</h2>
        <p className="mt-1 text-xs text-muted">
          QORVEXA is advisory and read-only. Industrial changes must be authorized by an accountable human engineer.
          The server records the verdict and rationale in the audit history.
        </p>

        {!brief ? (
          <div className="mt-4 rounded bg-surface p-4 text-xs text-muted">
            A decision brief is required before recording human sign-off.
          </div>
        ) : !canDecide ? (
          <p className="mt-4 text-xs text-muted">Decision sign-off requires an approver account.</p>
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
                    disabled={!approvable || isPending}
                    onChange={() => setVerdict('APPROVED')}
                    className="text-ok focus:ring-ok"
                  />
                  <span className="text-ok font-semibold">APPROVE</span> (Accept the recommendation)
                </label>
                <label className="flex items-center gap-2 text-xs font-medium text-ink cursor-pointer">
                  <input
                    type="radio"
                    name="verdict"
                    value="REJECTED"
                    checked={verdict === 'REJECTED'}
                    disabled={isPending}
                    onChange={() => setVerdict('REJECTED')}
                    className="text-critical focus:ring-critical"
                  />
                  <span className="text-critical font-semibold">REJECT</span> (Decline proposed schedule)
                </label>
              </div>
            </div>

            {verdict === 'APPROVED' && (
              <p className="text-xs text-muted">
                Scenario to approve: <strong>{selectedScenario}</strong>
              </p>
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
                        disabled={isPending}
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
                disabled={isPending}
                maxLength={2000}
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
        <h3 className="border-b border-border pb-3 text-base font-semibold text-ink">
          Persistent Audit History
        </h3>
        <div className="mt-4 overflow-x-auto">
          {history === null ? (
            <p className="py-6 text-center text-xs text-muted">
              {isPending ? 'Loading audit history…' : 'Audit history is unavailable.'}
            </p>
          ) : !history.records?.length ? (
            <p className="py-6 text-center text-xs text-muted">No decisions are recorded for this session.</p>
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
                    <td className="py-2.5 px-3 text-ink font-semibold">{rec.scenario_id ?? '—'}</td>
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
