import type { Question, UnderstandingReport } from '../../api/planningApi'

interface Props {
  report: UnderstandingReport | null
  sourceText?: string
  onAskClarification?: (question: Question) => void
}

export function UnderstandingReportView({ report, sourceText }: Props) {
  if (!report) {
    return (
      <div className="rounded-lg border border-border bg-panel p-8 text-center text-muted">
        <p className="text-sm">No industrial understanding report available yet.</p>
        <p className="mt-1 text-xs">
          Submit factory text in the intake view to generate an epistemic understanding report.
        </p>
      </div>
    )
  }

  const getReadinessBadge = (readiness: string) => {
    switch (readiness) {
      case 'CANDIDATES_FOR_HUMAN_REVIEW':
        return (
          <span className="inline-flex items-center gap-1 rounded bg-emerald-500/10 px-2.5 py-1 text-xs font-semibold text-emerald-400 border border-emerald-500/20">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            CANDIDATES FOR HUMAN REVIEW
          </span>
        )
      case 'NEEDS_CLARIFICATION':
        return (
          <span className="inline-flex items-center gap-1 rounded bg-amber-500/10 px-2.5 py-1 text-xs font-semibold text-amber-400 border border-amber-500/20">
            <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
            NEEDS CLARIFICATION
          </span>
        )
      case 'BLOCKED':
      default:
        return (
          <span className="inline-flex items-center gap-1 rounded bg-rose-500/10 px-2.5 py-1 text-xs font-semibold text-rose-400 border border-rose-500/20">
            <span className="h-1.5 w-1.5 rounded-full bg-rose-400" />
            BLOCKED
          </span>
        )
    }
  }

  const getGroundingBadge = (outcome: string) => {
    switch (outcome) {
      case 'GROUNDED':
        return <span className="rounded bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-400">GROUNDED</span>
      case 'PARTIALLY_GROUNDED':
        return <span className="rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-400">PARTIAL</span>
      default:
        return <span className="rounded bg-rose-500/10 px-1.5 py-0.5 text-[10px] font-medium text-rose-400">{outcome}</span>
    }
  }

  return (
    <div className="space-y-6">
      {/* Disclaimer Banner */}
      <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4 text-xs text-amber-300">
        <div className="flex items-start gap-2">
          <span className="font-bold">EPISTEMIC BOUNDARY:</span>
          <span>{report.disclaimer}</span>
        </div>
      </div>

      {/* Overview Card */}
      <div className="rounded-lg border border-border bg-panel p-5">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-border pb-4">
          <div>
            <h3 className="text-base font-semibold text-ink">Industrial Understanding Report</h3>
            <p className="text-xs text-muted">
              Pipeline: {report.pipeline_version} · Factory: <span className="font-mono text-ink">{report.factory_id}</span> · Engine: {report.llm_status}
            </p>
          </div>
          <div>{getReadinessBadge(report.readiness)}</div>
        </div>

        {/* Language Detection & Register */}
        <div className="mt-4">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-muted">Language & Register Detection</h4>
          <div className="mt-2 flex flex-wrap gap-2">
            {report.languages.map((lang, idx) => (
              <div
                key={idx}
                className="flex items-center gap-2 rounded border border-border bg-surface px-3 py-1.5 text-xs text-ink"
              >
                <span className="font-semibold text-accent">{lang.primary}</span>
                {lang.register_hint && lang.register_hint !== 'UNDETERMINED' && (
                  <span className="rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">
                    {lang.register_hint}
                  </span>
                )}
                {lang.arabizi && (
                  <span className="rounded bg-purple-500/10 px-1.5 py-0.5 text-[10px] text-purple-400">
                    Arabizi
                  </span>
                )}
                {lang.code_switching && (
                  <span className="rounded bg-cyan-500/10 px-1.5 py-0.5 text-[10px] text-cyan-400">
                    Code-Switching
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Contradictions Alert */}
      {report.contradictions.length > 0 && (
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-5">
          <h4 className="text-sm font-semibold text-rose-400">Contradictions Detected ({report.contradictions.length})</h4>
          <p className="mt-1 text-xs text-rose-300">
            Conflicting statements found in factory evidence. Conflicting values block mathematical formulation until resolved.
          </p>
          <div className="mt-3 space-y-2">
            {report.contradictions.map((c, i) => (
              <div key={i} className="rounded border border-rose-500/20 bg-surface/50 p-3 text-xs">
                <span className="font-mono font-bold text-rose-400">{c.code}</span>: {c.description}
                <div className="mt-1 text-[11px] text-muted">Affected claims: {c.claim_ids.join(', ')}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Clarification Questions */}
      {report.questions.length > 0 && (
        <div className="rounded-lg border border-amber-500/30 bg-panel p-5">
          <h4 className="text-sm font-semibold text-ink">Operator Clarification Plan ({report.questions.length})</h4>
          <p className="text-xs text-muted">
            Questions framed in the speaker's language (Egyptian Arabic, formal Arabic, or English) to resolve open gaps.
          </p>
          <div className="mt-3 space-y-3">
            {report.questions.map((q, i) => (
              <div key={i} className="rounded border border-border bg-surface p-3.5">
                <div className="flex items-center justify-between text-xs">
                  <span className="rounded bg-border px-1.5 py-0.5 font-mono text-[10px] text-muted">
                    {q.language}
                  </span>
                  <span className={`text-[10px] font-semibold ${q.blocking ? 'text-rose-400' : 'text-muted'}`}>
                    {q.blocking ? 'BLOCKING FORMULATION' : 'ADVISORY GAP'}
                  </span>
                </div>
                <p className="mt-2 text-sm font-medium text-ink" dir={q.language.startsWith('ar') ? 'rtl' : 'ltr'}>
                  {q.text}
                </p>
                <div className="mt-2 text-xs text-muted">
                  <span className="font-medium text-ink">Why it matters:</span> {q.why}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Untrusted Candidates emitted to Ledger */}
      <div className="rounded-lg border border-border bg-panel p-5">
        <h4 className="text-sm font-semibold text-ink">Emitted Untrusted Proposals ({report.candidates.length})</h4>
        <p className="text-xs text-muted">
          Only grounded claims about confirmed entities become supported proposals in the formulation ledger.
        </p>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-border text-muted">
                <th className="pb-2 font-medium">Status</th>
                <th className="pb-2 font-medium">Ledger Key</th>
                <th className="pb-2 font-medium">Value</th>
                <th className="pb-2 font-medium">Quote Evidence</th>
                <th className="pb-2 font-medium">Notes</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {report.candidates.map((c, i) => (
                <tr key={i} className="hover:bg-surface/50">
                  <td className="py-2 pr-2">
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                        c.status === 'SUPPORTED'
                          ? 'bg-emerald-500/10 text-emerald-400'
                          : c.status === 'BLOCKING_FEATURE'
                          ? 'bg-rose-500/10 text-rose-400'
                          : 'bg-amber-500/10 text-amber-400'
                      }`}
                    >
                      {c.status}
                    </span>
                  </td>
                  <td className="py-2 font-mono text-ink">{c.key}</td>
                  <td className="py-2 font-mono text-accent">{c.value}</td>
                  <td className="py-2 italic text-muted max-w-xs truncate">&ldquo;{c.quote}&rdquo;</td>
                  <td className="py-2 text-muted">{c.note || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Typed Claims & Grounding */}
      <div className="rounded-lg border border-border bg-panel p-5">
        <h4 className="text-sm font-semibold text-ink">Extracted & Grounded Claims ({report.claims.length})</h4>
        <div className="mt-3 space-y-2">
          {report.claims.map((claim) => {
            const gr = report.grounding.find((g) => g.claim_id === claim.claim_id)
            return (
              <div key={claim.claim_id} className="rounded border border-border bg-surface p-3 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-ink font-semibold">{claim.predicate}</span>
                    <span className="rounded bg-border px-1.5 py-0.5 text-[10px] text-muted">
                      {claim.semantic_type}
                    </span>
                    {claim.negated && (
                      <span className="rounded bg-rose-500/20 px-1.5 py-0.5 text-[10px] text-rose-300">
                        NEGATED
                      </span>
                    )}
                  </div>
                  <div>{gr && getGroundingBadge(gr.outcome)}</div>
                </div>
                <p className="mt-2 italic text-ink">&ldquo;{claim.quote}&rdquo;</p>
                <div className="mt-2 flex flex-wrap gap-4 text-[11px] text-muted">
                  <span>Subject: <strong className="text-ink">{claim.subject_text || '—'}</strong></span>
                  {claim.value && (
                    <span>
                      Value: <strong className="text-accent">{claim.value.low} {claim.value.unit || claim.value.unit_text}</strong>
                    </span>
                  )}
                  <span>Offsets: [{claim.orig_start}..{claim.orig_end}]</span>
                  <span>Provenance: {claim.provenance || 'rules/1'}</span>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Source Text with verification offsets */}
      {sourceText && (
        <div className="rounded-lg border border-border bg-panel p-5">
          <h4 className="text-sm font-semibold text-ink">Raw Evidence Source Text</h4>
          <pre className="mt-2 whitespace-pre-wrap rounded border border-border bg-surface p-3 font-mono text-xs text-ink">
            {sourceText}
          </pre>
        </div>
      )}
    </div>
  )
}
