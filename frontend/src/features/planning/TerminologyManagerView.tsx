import { useCallback, useEffect, useState } from 'react'
import {
  type TermEntry,
  confirmTerm,
  correctTerm,
  deprecateTerm,
  getTermHistory,
  listTerms,
  proposeTerm,
  rejectTerm,
  rollbackTerm,
} from '../../api/planningApi'

interface Props {
  factoryId: string
  onNotice?: (msg: string) => void
}

export function TerminologyManagerView({ factoryId, onNotice }: Props) {
  const [terms, setTerms] = useState<TermEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState<'ALL' | 'CONFIRMED' | 'PROPOSED' | 'DEPRECATED'>('ALL')
  const [selectedTerm, setSelectedTerm] = useState<TermEntry | null>(null)
  const [history, setHistory] = useState<TermEntry[]>([])
  const [showProposeModal, setShowProposeModal] = useState(false)

  // Propose form state
  const [newExpr, setNewExpr] = useState('')
  const [newConcept, setNewConcept] = useState('')
  const [newCanonicalId, setNewCanonicalId] = useState('')
  const [newEntityType, setNewEntityType] = useState('MACHINE')
  const [newMeaning, setNewMeaning] = useState('')

  // Correction / Action modal
  const [actionNote, setActionNote] = useState('')
  const [correctMeaning, setCorrectMeaning] = useState('')
  const [activeAction, setActiveAction] = useState<'CONFIRM' | 'REJECT' | 'CORRECT' | 'DEPRECATE' | 'ROLLBACK' | null>(null)
  const [rollbackVersion, setRollbackVersion] = useState<number>(1)

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const data = await listTerms(factoryId)
      setTerms(data)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      onNotice?.(`Failed to load terms: ${msg}`)
    } finally {
      setLoading(false)
    }
  }, [factoryId, onNotice])

  useEffect(() => {
    loadData()
  }, [loadData])

  const loadHistory = async (term: TermEntry) => {
    setSelectedTerm(term)
    try {
      const hist = await getTermHistory(factoryId, term.term_id)
      setHistory(hist)
    } catch {
      setHistory([term])
    }
  }

  const handlePropose = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newExpr.trim() || !newConcept.trim()) return
    try {
      await proposeTerm(factoryId, {
        expression: newExpr.trim(),
        concept: newConcept.trim(),
        canonical_id: newCanonicalId.trim() || undefined,
        entity_type: newEntityType || undefined,
        meaning: newMeaning.trim(),
      })
      onNotice?.(`Proposed term "${newExpr}" successfully.`)
      setShowProposeModal(false)
      setNewExpr('')
      setNewConcept('')
      setNewCanonicalId('')
      setNewMeaning('')
      await loadData()
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      onNotice?.(`Failed to propose term: ${msg}`)
    }
  }

  const handleExecuteAction = async () => {
    if (!selectedTerm || !activeAction) return
    try {
      if (activeAction === 'CONFIRM') {
        await confirmTerm(factoryId, selectedTerm.term_id, actionNote)
        onNotice?.(`Term ${selectedTerm.expression} confirmed.`)
      } else if (activeAction === 'REJECT') {
        await rejectTerm(factoryId, selectedTerm.term_id, actionNote)
        onNotice?.(`Term ${selectedTerm.expression} rejected.`)
      } else if (activeAction === 'CORRECT') {
        await correctTerm(factoryId, selectedTerm.term_id, actionNote || 'Corrected meaning', {
          meaning: correctMeaning || selectedTerm.meaning,
        })
        onNotice?.(`Term ${selectedTerm.expression} corrected.`)
      } else if (activeAction === 'DEPRECATE') {
        await deprecateTerm(factoryId, selectedTerm.term_id, actionNote)
        onNotice?.(`Term ${selectedTerm.expression} deprecated.`)
      } else if (activeAction === 'ROLLBACK') {
        await rollbackTerm(factoryId, selectedTerm.term_id, rollbackVersion, actionNote || 'Rollback')
        onNotice?.(`Term ${selectedTerm.expression} rolled back to v${rollbackVersion}.`)
      }
      setActiveAction(null)
      setActionNote('')
      await loadData()
      const updated = await getTermHistory(factoryId, selectedTerm.term_id)
      setHistory(updated)
      if (updated.length > 0) setSelectedTerm(updated[updated.length - 1] ?? null)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      onNotice?.(`Action failed: ${msg}`)
    }
  }

  const filteredTerms = terms.filter((t) => {
    if (filter === 'ALL') return true
    return t.status === filter
  })

  return (
    <div className="space-y-6">
      {/* Header and Controls */}
      <div className="rounded-lg border border-border bg-panel p-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h3 className="text-base font-semibold text-ink">Factory Terminology Book</h3>
            <p className="text-xs text-muted">
              Tenant-isolated dictionary for plant <span className="font-mono text-accent">{factoryId}</span>. Requires human <span className="font-semibold text-ink">TERMINOLOGY_REVIEWER</span> to confirm, reject, or correct.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setShowProposeModal(true)}
              className="rounded bg-accent px-3 py-1.5 text-xs font-semibold text-surface hover:opacity-90"
            >
              + Propose New Term
            </button>
            <button
              type="button"
              onClick={loadData}
              className="rounded border border-border px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface"
            >
              Refresh
            </button>
          </div>
        </div>

        {/* Filter Badges */}
        <div className="mt-4 flex gap-2 border-t border-border pt-4 text-xs">
          {(['ALL', 'CONFIRMED', 'PROPOSED', 'DEPRECATED'] as const).map((st) => (
            <button
              key={st}
              type="button"
              onClick={() => setFilter(st)}
              className={`rounded px-3 py-1 font-medium transition-colors ${
                filter === st
                  ? 'bg-accent/20 text-accent border border-accent/30'
                  : 'bg-surface text-muted hover:text-ink border border-border'
              }`}
            >
              {st} ({st === 'ALL' ? terms.length : terms.filter((t) => t.status === st).length})
            </button>
          ))}
        </div>
      </div>

      {/* Main Term Table and History View */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Term List */}
        <div className="rounded-lg border border-border bg-panel p-5 lg:col-span-2">
          <h4 className="text-sm font-semibold text-ink">Terms ({filteredTerms.length})</h4>
          {loading ? (
            <div className="py-8 text-center text-xs text-muted">Loading terminology...</div>
          ) : filteredTerms.length === 0 ? (
            <div className="py-8 text-center text-xs text-muted">No terms matching filter in {factoryId}.</div>
          ) : (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-border text-muted">
                    <th className="pb-2 font-medium">Status</th>
                    <th className="pb-2 font-medium">Expression</th>
                    <th className="pb-2 font-medium">Entity & Canonical ID</th>
                    <th className="pb-2 font-medium">Concept</th>
                    <th className="pb-2 font-medium">Ver</th>
                    <th className="pb-2 font-medium">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {filteredTerms.map((term) => (
                    <tr
                      key={term.term_id}
                      onClick={() => loadHistory(term)}
                      className={`cursor-pointer hover:bg-surface/50 ${
                        selectedTerm?.term_id === term.term_id ? 'bg-surface/80' : ''
                      }`}
                    >
                      <td className="py-2.5 pr-2">
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                            term.status === 'CONFIRMED'
                              ? 'bg-emerald-500/10 text-emerald-400'
                              : term.status === 'PROPOSED'
                              ? 'bg-amber-500/10 text-amber-400'
                              : term.status === 'DEPRECATED'
                              ? 'bg-muted/20 text-muted'
                              : 'bg-rose-500/10 text-rose-400'
                          }`}
                        >
                          {term.status}
                        </span>
                      </td>
                      <td className="py-2.5 font-medium text-ink">{term.expression}</td>
                      <td className="py-2.5 text-muted">
                        {term.entity_type ? (
                          <span>
                            {term.entity_type}: <strong className="text-accent">{term.canonical_id || '—'}</strong>
                          </span>
                        ) : (
                          '—'
                        )}
                      </td>
                      <td className="py-2.5 font-mono text-muted">{term.concept}</td>
                      <td className="py-2.5 text-muted font-mono">v{term.version}</td>
                      <td className="py-2.5 text-accent font-medium">Inspect →</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Selected Term Inspector & Actions */}
        <div className="rounded-lg border border-border bg-panel p-5">
          <h4 className="text-sm font-semibold text-ink">Term Inspector</h4>
          {selectedTerm ? (
            <div className="mt-3 space-y-4 text-xs">
              <div className="rounded border border-border bg-surface p-3 space-y-1.5">
                <div className="flex justify-between items-center">
                  <span className="font-mono text-muted">{selectedTerm.term_id}</span>
                  <span className="font-mono text-accent">v{selectedTerm.version}</span>
                </div>
                <div className="text-base font-bold text-ink">{selectedTerm.expression}</div>
                <div className="text-muted">Concept: <strong className="text-ink">{selectedTerm.concept}</strong></div>
                {selectedTerm.meaning && (
                  <div className="text-muted">Meaning: {selectedTerm.meaning}</div>
                )}
                {selectedTerm.reviewer && (
                  <div className="text-[11px] text-muted">Reviewed by: {selectedTerm.reviewer}</div>
                )}
              </div>

              {/* Reviewer Actions */}
              <div className="space-y-2 border-t border-border pt-3">
                <span className="text-[11px] font-semibold text-muted uppercase tracking-wider">Reviewer Actions</span>
                <div className="flex flex-wrap gap-2">
                  {selectedTerm.status === 'PROPOSED' && (
                    <>
                      <button
                        type="button"
                        onClick={() => setActiveAction('CONFIRM')}
                        className="rounded bg-emerald-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-emerald-500"
                      >
                        Confirm
                      </button>
                      <button
                        type="button"
                        onClick={() => setActiveAction('REJECT')}
                        className="rounded bg-rose-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-rose-500"
                      >
                        Reject
                      </button>
                    </>
                  )}
                  {selectedTerm.status === 'CONFIRMED' && (
                    <>
                      <button
                        type="button"
                        onClick={() => {
                          setCorrectMeaning(selectedTerm.meaning)
                          setActiveAction('CORRECT')
                        }}
                        className="rounded bg-accent px-2.5 py-1 text-xs font-semibold text-surface hover:opacity-90"
                      >
                        Correct
                      </button>
                      <button
                        type="button"
                        onClick={() => setActiveAction('DEPRECATE')}
                        className="rounded bg-border px-2.5 py-1 text-xs font-semibold text-ink hover:bg-surface"
                      >
                        Deprecate
                      </button>
                    </>
                  )}
                  {history.length > 1 && (
                    <button
                      type="button"
                      onClick={() => setActiveAction('ROLLBACK')}
                      className="rounded border border-border px-2.5 py-1 text-xs font-medium text-ink hover:bg-surface"
                    >
                      Rollback
                    </button>
                  )}
                </div>
              </div>

              {/* History Timeline */}
              <div className="border-t border-border pt-3">
                <span className="text-[11px] font-semibold text-muted uppercase tracking-wider">Version History</span>
                <div className="mt-2 space-y-2">
                  {history.map((h) => (
                    <div key={h.version} className="rounded border border-border bg-surface/50 p-2 text-[11px]">
                      <div className="flex justify-between font-mono">
                        <strong className="text-ink">v{h.version} ({h.status})</strong>
                        <span className="text-muted">{h.reviewer || 'system'}</span>
                      </div>
                      {h.meaning && <p className="mt-1 text-muted">{h.meaning}</p>}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="py-8 text-center text-xs text-muted">Select a term to inspect history and review.</div>
          )}
        </div>
      </div>

      {/* Propose Term Modal */}
      {showProposeModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-md rounded-lg border border-border bg-panel p-6 shadow-xl">
            <h4 className="text-base font-semibold text-ink">Propose Factory Term</h4>
            <p className="mt-1 text-xs text-muted">
              Proposals enter the book as untrusted proposed terms until confirmed by a reviewer.
            </p>
            <form onSubmit={handlePropose} className="mt-4 space-y-3 text-xs">
              <div>
                <label htmlFor="new-expr" className="block text-muted">Factory Expression / Jargon</label>
                <input
                  id="new-expr"
                  type="text"
                  required
                  value={newExpr}
                  onChange={(e) => setNewExpr(e.target.value)}
                  placeholder="e.g. الفيلر or Mixer 1"
                  className="mt-1 w-full rounded border border-border bg-surface p-2 text-ink"
                />
              </div>
              <div>
                <label htmlFor="new-concept" className="block text-muted">Ontology Concept Reference</label>
                <input
                  id="new-concept"
                  type="text"
                  required
                  value={newConcept}
                  onChange={(e) => setNewConcept(e.target.value)}
                  placeholder="e.g. MACH_FILLER or PROD_JUICE"
                  className="mt-1 w-full rounded border border-border bg-surface p-2 text-ink"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label htmlFor="new-entity-type" className="block text-muted">Entity Type</label>
                  <select
                    id="new-entity-type"
                    value={newEntityType}
                    onChange={(e) => setNewEntityType(e.target.value)}
                    className="mt-1 w-full rounded border border-border bg-surface p-2 text-ink"
                  >
                    <option value="MACHINE">MACHINE</option>
                    <option value="LINE">LINE</option>
                    <option value="PRODUCT">PRODUCT</option>
                    <option value="MATERIAL">MATERIAL</option>
                    <option value="RESOURCE">RESOURCE</option>
                    <option value="SHIFT">SHIFT</option>
                  </select>
                </div>
                <div>
                  <label htmlFor="new-canonical-id" className="block text-muted">Canonical Model ID</label>
                  <input
                    id="new-canonical-id"
                    type="text"
                    value={newCanonicalId}
                    onChange={(e) => setNewCanonicalId(e.target.value)}
                    placeholder="e.g. M1 or P_A"
                    className="mt-1 w-full rounded border border-border bg-surface p-2 text-ink"
                  />
                </div>
              </div>
              <div>
                <label htmlFor="new-meaning" className="block text-muted">Meaning / Notes</label>
                <textarea
                  id="new-meaning"
                  rows={2}
                  value={newMeaning}
                  onChange={(e) => setNewMeaning(e.target.value)}
                  placeholder="Describe process, machine location, or context..."
                  className="mt-1 w-full rounded border border-border bg-surface p-2 text-ink"
                />
              </div>
              <div className="mt-4 flex justify-end gap-3 pt-3 border-t border-border">
                <button
                  type="button"
                  onClick={() => setShowProposeModal(false)}
                  className="rounded border border-border px-3 py-1.5 text-xs text-muted hover:text-ink"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="rounded bg-accent px-4 py-1.5 text-xs font-semibold text-surface hover:opacity-90"
                >
                  Submit Proposal
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Reviewer Action Dialog */}
      {activeAction && selectedTerm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-sm rounded-lg border border-border bg-panel p-6 shadow-xl">
            <h4 className="text-sm font-semibold text-ink">
              Action: {activeAction} {selectedTerm.expression}
            </h4>
            <div className="mt-3 space-y-3 text-xs">
              {activeAction === 'CORRECT' && (
                <div>
                  <label htmlFor="correct-meaning" className="block text-muted">Updated Meaning</label>
                  <textarea
                    id="correct-meaning"
                    rows={3}
                    value={correctMeaning}
                    onChange={(e) => setCorrectMeaning(e.target.value)}
                    className="mt-1 w-full rounded border border-border bg-surface p-2 text-ink"
                  />
                </div>
              )}
              {activeAction === 'ROLLBACK' && (
                <div>
                  <label htmlFor="rollback-version" className="block text-muted">Target Version to Restore</label>
                  <select
                    id="rollback-version"
                    value={rollbackVersion}
                    onChange={(e) => setRollbackVersion(Number(e.target.value))}
                    className="mt-1 w-full rounded border border-border bg-surface p-2 text-ink"
                  >
                    {history.map((h) => (
                      <option key={h.version} value={h.version}>
                        v{h.version} - {h.status} ({h.meaning || h.concept})
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <div>
                <label htmlFor="action-note" className="block text-muted">Audit Note / Rationale</label>
                <input
                  id="action-note"
                  type="text"
                  required
                  value={actionNote}
                  onChange={(e) => setActionNote(e.target.value)}
                  placeholder="Reason for change..."
                  className="mt-1 w-full rounded border border-border bg-surface p-2 text-ink"
                />
              </div>
              <div className="mt-4 flex justify-end gap-3 pt-3 border-t border-border">
                <button
                  type="button"
                  onClick={() => setActiveAction(null)}
                  className="rounded border border-border px-3 py-1.5 text-xs text-muted hover:text-ink"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleExecuteAction}
                  className="rounded bg-accent px-4 py-1.5 text-xs font-semibold text-surface hover:opacity-90"
                >
                  Confirm Action
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
