import { useState } from 'react'
import type { FormulationState } from '../../api/planningApi'

interface Props {
  state: FormulationState | null
  onConfirm: (keys: string[]) => Promise<void>
  onAssume: (rationale: string) => Promise<void>
  onAssert: (key: string, value: string | number) => Promise<void>
  isPending: boolean
}

export function FactBoardView({ state, onConfirm, onAssume, onAssert, isPending }: Props) {
  const [selectedKeys, setSelectedKeys] = useState<string[]>([])
  const [rationale, setRationale] = useState('')
  const [manualKey, setManualKey] = useState('')
  const [manualValue, setManualValue] = useState('')

  if (!state) {
    return (
      <div className="rounded-lg border border-border bg-panel p-8 text-center text-sm text-muted">
        No active session state. Please ingest factory text or select a session.
      </div>
    )
  }

  const unconfirmedIssues = state.issues.filter(
    (i) => i.code === 'UNCONFIRMED_PROPOSAL' || i.code === 'MISSING_VALUE'
  )

  const toggleKey = (key: string) => {
    setSelectedKeys((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    )
  }

  const selectAll = () => {
    setSelectedKeys(unconfirmedIssues.map((i) => i.key))
  }

  return (
    <div className="space-y-6">
      {/* State banner */}
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-border bg-panel p-4">
        <div>
          <span className="text-xs uppercase tracking-wider text-muted">Formulation Status</span>
          <div className="flex items-center gap-2 mt-1">
            <span
              className={`inline-block size-3 rounded-full ${
                state.status === 'READY'
                  ? 'bg-ok'
                  : state.status === 'NEEDS_CLARIFICATION'
                  ? 'bg-warn'
                  : 'bg-critical'
              }`}
            />
            <span className="font-semibold text-ink">{state.status}</span>
          </div>
        </div>

        <div className="flex items-center gap-4 text-xs">
          <div>
            <span className="text-muted">Confirmed Facts: </span>
            <span className="font-mono font-medium text-ink">{state.facts.length}</span>
          </div>
          <div>
            <span className="text-muted">Pending Issues: </span>
            <span className="font-mono font-medium text-warn">{state.issues.length}</span>
          </div>
          <div>
            <span className="text-muted">Units: </span>
            <span className="font-medium text-ok">{state.units_status}</span>
          </div>
        </div>
      </div>

      {/* Unconfirmed Proposals Table */}
      {unconfirmedIssues.length > 0 && (
        <div className="rounded-lg border border-border bg-panel p-5">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
            <div>
              <h3 className="text-base font-semibold text-ink">Proposed Claims (Pending Human Confirmation)</h3>
              <p className="text-xs text-muted">
                These claims were extracted from raw text. A human planner must confirm them before mathematical admission.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={selectAll}
                className="rounded border border-border px-2.5 py-1 text-xs text-ink hover:bg-border"
              >
                Select All ({unconfirmedIssues.length})
              </button>
              <button
                type="button"
                disabled={isPending || selectedKeys.length === 0}
                onClick={() => {
                  onConfirm(selectedKeys)
                  setSelectedKeys([])
                }}
                className="rounded bg-ok px-3 py-1 text-xs font-medium text-white hover:bg-green-600 disabled:opacity-50"
              >
                Confirm Selected ({selectedKeys.length})
              </button>
            </div>
          </div>

          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-border text-muted">
                  <th className="py-2 px-3 w-8"></th>
                  <th className="py-2 px-3">Slot Key</th>
                  <th className="py-2 px-3">Proposed Value</th>
                  <th className="py-2 px-3">Issue Reason</th>
                  <th className="py-2 px-3">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {unconfirmedIssues.map((issue) => (
                  <tr key={issue.key} className="hover:bg-surface/50">
                    <td className="py-2 px-3">
                      <input
                        type="checkbox"
                        checked={selectedKeys.includes(issue.key)}
                        onChange={() => toggleKey(issue.key)}
                        className="rounded border-border bg-surface text-accent"
                      />
                    </td>
                    <td className="py-2 px-3 font-mono text-ink">{issue.key}</td>
                    <td className="py-2 px-3 font-mono text-ok">
                      {issue.candidates?.join(', ') || '-'}
                    </td>
                    <td className="py-2 px-3 text-muted">{issue.message}</td>
                    <td className="py-2 px-3">
                      <button
                        type="button"
                        disabled={isPending}
                        onClick={() => onConfirm([issue.key])}
                        className="rounded border border-border px-2 py-0.5 text-xs text-ok hover:bg-ok/10"
                      >
                        Confirm
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Assumptions / Closed-World Defaults */}
      <div className="rounded-lg border border-border bg-panel p-5">
        <h3 className="text-base font-semibold text-ink">Closed-World Default Assumptions</h3>
        <p className="mt-1 text-xs text-muted">
          If non-critical parameters are missing, apply allow-listed defaults with an explicit written rationale.
        </p>

        <div className="mt-3 flex flex-wrap items-center gap-3">
          <input
            type="text"
            value={rationale}
            onChange={(e) => setRationale(e.target.value)}
            className="flex-1 rounded border border-border bg-surface px-3 py-1.5 text-xs text-ink focus:border-accent focus:outline-none"
            placeholder="Enter rationale for applying default parameters..."
          />
          <button
            type="button"
            disabled={isPending || !rationale.trim()}
            onClick={() => onAssume(rationale)}
            className="rounded border border-accent bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/20 disabled:opacity-50"
          >
            Apply Standard Defaults
          </button>
        </div>
      </div>

      {/* Confirmed Facts Table */}
      <div className="rounded-lg border border-border bg-panel p-5">
        <h3 className="text-base font-semibold text-ink">Confirmed Facts Ledger ({state.facts.length})</h3>
        <p className="mt-1 text-xs text-muted">
          Immutable statements backed by human confirmation or explicitly stated by authorized planners.
        </p>

        <div className="mt-3 max-h-64 overflow-y-auto">
          {state.facts.length === 0 ? (
            <p className="py-4 text-center text-xs text-muted">No confirmed facts yet.</p>
          ) : (
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-border text-muted">
                  <th className="py-2 px-3">Seq</th>
                  <th className="py-2 px-3">Key</th>
                  <th className="py-2 px-3">Value</th>
                  <th className="py-2 px-3">State</th>
                  <th className="py-2 px-3">Basis</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {state.facts.map((fact) => (
                  <tr key={`${fact.claim_seq}-${fact.key}`} className="hover:bg-surface/50">
                    <td className="py-1.5 px-3 font-mono text-muted">{fact.claim_seq}</td>
                    <td className="py-1.5 px-3 font-mono text-ink">{fact.key}</td>
                    <td className="py-1.5 px-3 font-mono font-medium text-ink">{fact.value}</td>
                    <td className="py-1.5 px-3">
                      <span className="rounded bg-ok/10 px-1.5 py-0.5 text-xs font-medium text-ok">
                        {fact.state}
                      </span>
                    </td>
                    <td className="py-1.5 px-3 text-muted">{fact.basis}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Manual Fact Assertion */}
      <div className="rounded-lg border border-border bg-panel p-5">
        <h3 className="text-base font-semibold text-ink">Assert Fact (Direct Planner Statement)</h3>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <input
            type="text"
            value={manualKey}
            onChange={(e) => setManualKey(e.target.value)}
            placeholder="e.g. resource:MACHINE:capacity"
            className="flex-1 rounded border border-border bg-surface px-3 py-1.5 font-mono text-xs text-ink focus:border-accent focus:outline-none"
          />
          <input
            type="text"
            value={manualValue}
            onChange={(e) => setManualValue(e.target.value)}
            placeholder="e.g. 120"
            className="w-36 rounded border border-border bg-surface px-3 py-1.5 font-mono text-xs text-ink focus:border-accent focus:outline-none"
          />
          <button
            type="button"
            disabled={isPending || !manualKey.trim() || !manualValue.trim()}
            onClick={() => {
              onAssert(manualKey, manualValue)
              setManualKey('')
              setManualValue('')
            }}
            className="rounded border border-border px-3 py-1.5 text-xs font-medium text-ink hover:bg-border disabled:opacity-50"
          >
            Assert Fact
          </button>
        </div>
      </div>
    </div>
  )
}
