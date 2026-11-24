import { useEffect, useRef, useState } from 'react'
import { useMe } from '../../api/generated/incidents/incidents'
import { ApiError } from '../../api/http'
import {
  type DecideOnRequest,
  type DecisionBrief,
  type DecisionHistory,
  type ExtractedClaim,
  type FormulationResult,
  type FormulationState,
  type IngestOptions,
  type ScenarioSpec,
  type SessionSummary,
  type UnderstandingReport,
  assertFact,
  assumeDefaults,
  buildFormulation,
  confirmClaims,
  createSession,
  decideOn,
  getSessionHistory,
  getSessionState,
  getSessionUnderstanding,
  ingestText,
  listSessions,
  runDecision,
} from '../../api/planningApi'
import { DecisionIntelligenceView } from './DecisionIntelligenceView'
import { FactBoardView } from './FactBoardView'
import { FormulationReviewView } from './FormulationReviewView'
import { HumanApprovalView } from './HumanApprovalView'
import { ProblemIntakeView } from './ProblemIntakeView'
import { ScenarioWorkbenchView } from './ScenarioWorkbenchView'
import { TerminologyManagerView } from './TerminologyManagerView'
import { UnderstandingReportView } from './UnderstandingReportView'

type Tab =
  | 'intake'
  | 'understanding'
  | 'terminology'
  | 'facts'
  | 'formulation'
  | 'scenarios'
  | 'decision'
  | 'approval'

function errorMessage(error: unknown, action: string) {
  if (error instanceof ApiError && error.status === 401) return `${action} Sign in again.`
  if (error instanceof ApiError && error.status === 403) {
    return `${action} Your account does not have permission.`
  }
  if (error instanceof ApiError && error.status === 409) {
    return `${action} The session changed. Refresh and review the latest state.`
  }
  return action
}

export function PlanningStudio() {
  const me = useMe()
  const activeFactory = me.data?.data.tenant_id ?? ''
  const canDecide = me.data?.data.roles.includes('approver') ?? false
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [selectedSessionId, setSelectedSessionId] = useState('')
  const [newSessionTitle, setNewSessionTitle] = useState('')
  const [loadingSessions, setLoadingSessions] = useState(true)
  const [sessionError, setSessionError] = useState<string | null>(null)

  useEffect(() => {
    let mounted = true
    listSessions()
      .then((results) => {
        if (!mounted) return
        setSessions(results)
        setSelectedSessionId(results[0]?.id ?? '')
      })
      .catch((error: unknown) => {
        if (mounted) setSessionError(errorMessage(error, 'Could not load planning sessions.'))
      })
      .finally(() => {
        if (mounted) setLoadingSessions(false)
      })
    return () => {
      mounted = false
    }
  }, [])

  const handleCreateSession = async () => {
    const title = newSessionTitle.trim()
    if (!title || loadingSessions) return
    setLoadingSessions(true)
    setSessionError(null)
    try {
      const created = await createSession(`SESS-${crypto.randomUUID()}`, title)
      setNewSessionTitle('')
      try {
        setSessions(await listSessions())
        setSelectedSessionId(created.session_id)
      } catch (error) {
        setSessionError(errorMessage(error, 'Session created, but the session list could not be refreshed.'))
      }
    } catch (error) {
      setSessionError(errorMessage(error, 'Could not create the planning session.'))
    } finally {
      setLoadingSessions(false)
    }
  }

  return (
    <section aria-labelledby="studio-title" className="space-y-6 max-w-6xl">
      {/* Top Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-border pb-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 id="studio-title" className="text-2xl font-bold tracking-tight text-ink">
              Production Planning &amp; Decision Studio
            </h1>
            <span className="rounded bg-ok/10 px-2 py-0.5 text-xs font-semibold text-ok border border-ok/20">
              ADVISORY ONLY · READ-ONLY
            </span>
          </div>
          <p className="mt-1 text-xs text-muted">
            Factory-Floor Language Intake · Verified Fact Ledger · Dimensional Unit Validation · HiGHS Optimization · Human-in-the-Loop Sign-off
          </p>
        </div>

        {/* Session Selector & Factory Scope */}
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1.5 rounded border border-border bg-panel px-2.5 py-1 text-xs">
            <span className="text-muted">Factory:</span>
            <span className="font-mono font-medium text-accent">{activeFactory || 'Unavailable'}</span>
          </div>
          <select
            aria-label="Planning session"
            disabled={loadingSessions || sessions.length === 0}
            value={selectedSessionId}
            onChange={(e) => setSelectedSessionId(e.target.value)}
            className="rounded border border-border bg-panel px-3 py-1.5 font-mono text-xs text-ink focus:border-accent focus:outline-none"
          >
            {sessions.length === 0 && <option value="">No planning sessions</option>}
            {sessions.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title} ({s.id})
              </option>
            ))}
          </select>

          <input
            type="text"
            value={newSessionTitle}
            onChange={(e) => setNewSessionTitle(e.target.value)}
            placeholder="New session title..."
            className="w-40 rounded border border-border bg-panel px-3 py-1.5 text-xs text-ink focus:border-accent focus:outline-none"
          />
          <button
            type="button"
            disabled={loadingSessions || !newSessionTitle.trim()}
            onClick={handleCreateSession}
            className="rounded border border-border px-3 py-1.5 text-xs font-medium text-ink hover:bg-border disabled:opacity-50"
          >
            + Create
          </button>
        </div>
      </div>

      {sessionError && <p role="alert" className="text-sm text-critical">{sessionError}</p>}
      <PlanningSession
        key={selectedSessionId}
        sessionId={selectedSessionId}
        activeFactory={activeFactory}
        canDecide={canDecide}
      />
    </section>
  )
}

interface SessionProps {
  sessionId: string
  activeFactory: string
  canDecide: boolean
}

function PlanningSession({ sessionId, activeFactory, canDecide }: SessionProps) {
  const [activeTab, setActiveTab] = useState<Tab>('intake')
  const [state, setState] = useState<FormulationState | null>(null)
  const [formulation, setFormulation] = useState<FormulationResult | null>(null)
  const [scenarios, setScenarios] = useState<ScenarioSpec[]>([])
  const [brief, setBrief] = useState<DecisionBrief | null>(null)
  const [history, setHistory] = useState<DecisionHistory | null>(null)
  const [isPending, setIsPending] = useState(Boolean(sessionId))
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [rawSourceText, setRawSourceText] = useState('')
  const [understandingReport, setUnderstandingReport] = useState<UnderstandingReport | null>(null)
  const [factoryScope, setFactoryScope] = useState<string | null>(null)
  const mutating = useRef(false)
  const factoryId = factoryScope ?? activeFactory

  useEffect(() => {
    if (!sessionId) return
    let mounted = true
    Promise.allSettled([
      getSessionState(sessionId),
      getSessionHistory(sessionId),
      getSessionUnderstanding(sessionId),
    ]).then(([stateResult, historyResult, understandingResult]) => {
      if (!mounted) return
      if (stateResult.status === 'fulfilled') setState(stateResult.value)
      if (historyResult.status === 'fulfilled') setHistory(historyResult.value)
      if (understandingResult.status === 'fulfilled') {
        setUnderstandingReport(understandingResult.value)
      } else if (!(understandingResult.reason instanceof ApiError && understandingResult.reason.status === 404)) {
        setError(errorMessage(understandingResult.reason, 'Could not load the understanding report.'))
      }
      if (stateResult.status === 'rejected') {
        setError(errorMessage(stateResult.reason, 'Could not load the planning session state.'))
      }
      setIsPending(false)
    })
    return () => {
      mounted = false
    }
  }, [sessionId])

  const runAction = async (action: () => Promise<void>, failure: string) => {
    if (!sessionId || isPending || mutating.current) return
    mutating.current = true
    setIsPending(true)
    setNotice(null)
    setError(null)
    try {
      await action()
    } catch (error) {
      setError(errorMessage(error, failure))
    } finally {
      mutating.current = false
      setIsPending(false)
    }
  }

  const invalidateFormulation = () => {
    setFormulation(null)
    setBrief(null)
  }

  const handleIngest = (text: string, claims?: ExtractedClaim[], options?: IngestOptions) =>
    runAction(async () => {
      invalidateFormulation()
      const result = await ingestText(sessionId, text, claims, options)
      setRawSourceText(text)
      if (options?.factory_id) setFactoryScope(options.factory_id)
      setState(result.state)
      setUnderstandingReport(result.understanding ?? null)
      setNotice(`Ingested ${result.accepted_count} claim proposals.`)
      setActiveTab(result.understanding ? 'understanding' : 'facts')
    }, 'Could not ingest the text.')

  const handleConfirm = (keys: string[]) =>
    runAction(async () => {
      invalidateFormulation()
      const result = await confirmClaims(sessionId, keys)
      setState(result)
      setNotice('Confirmation saved. Review the current fact state.')
      if (result.status === 'READY') setActiveTab('formulation')
    }, 'Could not confirm the selected facts.')

  const handleAssume = (rationale: string) =>
    runAction(async () => {
      invalidateFormulation()
      setState(await assumeDefaults(sessionId, rationale))
      setNotice('Standard defaults applied.')
    }, 'Could not apply the standard defaults.')

  const handleAssert = (key: string, value: string | number) =>
    runAction(async () => {
      invalidateFormulation()
      setState(await assertFact(sessionId, key, value))
      setNotice(`Asserted fact ${key}.`)
    }, 'Could not assert the fact.')

  const handleBuild = () =>
    runAction(async () => {
      invalidateFormulation()
      const result = await buildFormulation(sessionId)
      setState(result.state)
      if (!result.compiled || !result.problem || !result.record) {
        setError('The formulation did not compile. Review the session status and outstanding issues.')
        return
      }
      setFormulation(result)
      setNotice('Formulation compiled.')
      setActiveTab('scenarios')
    }, 'Could not compile the formulation.')

  const handleRunDecision = () =>
    runAction(async () => {
      if (!formulation) return
      setBrief(null)
      const result = await runDecision(sessionId, scenarios)
      setBrief(result)
      setNotice('Decision brief received. Review the solver status and recommendation.')
      setActiveTab('decision')
    }, 'Could not generate a decision brief.')

  const handleDecideOn = (payload: DecideOnRequest) =>
    runAction(async () => {
      if (!canDecide || !brief || payload.brief_sha256 !== brief.brief_sha256) return
      try {
        const record = await decideOn(sessionId, payload)
        setNotice(`Decision recorded by the server as ${record.verdict}.`)
      } catch (error) {
        // A timeout or server failure can happen after a write. Clear the brief
        // so a second decision requires reviewing the server state first.
        if (!(error instanceof ApiError) || error.status === 409 || error.status >= 500) {
          setBrief(null)
          try {
            setHistory(await getSessionHistory(sessionId))
          } catch {
            setHistory(null)
          }
        }
        throw error
      }
      setBrief(null)
      try {
        setHistory(await getSessionHistory(sessionId))
      } catch (error) {
        setHistory(null)
        setError(errorMessage(error, 'Could not refresh the audit history.'))
      }
    }, 'Could not confirm that the decision was recorded.')

  return (
    <>
      {/* Epistemic Alert Toast */}
      {notice && (
        <div role="status" className="rounded-lg border border-ok/30 bg-ok/10 px-4 py-2.5 text-xs text-ok">
          {notice}
        </div>
      )}

      {error && <p role="alert" className="text-sm text-critical">{error}</p>}

      {/* Tabs Navigation */}
      <nav aria-label="Studio Steps" className="flex flex-wrap items-center justify-between gap-1 border-b border-border pb-2 text-xs font-medium">
        <div className="flex flex-wrap gap-1">
          {[
            { id: 'intake', label: '1. Intake & Claims' },
            { id: 'facts', label: '2. Fact Board' },
            { id: 'formulation', label: '3. Formulation & Units' },
            { id: 'scenarios', label: '4. Scenario Workbench' },
            { id: 'decision', label: '5. Solver & Intelligence' },
            { id: 'approval', label: '6. Human Approval & Audit' },
          ].map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id as Tab)}
              className={`rounded px-3 py-1.5 transition-colors ${
                activeTab === tab.id
                  ? 'bg-accent text-white font-semibold'
                  : 'text-muted hover:bg-panel hover:text-ink'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setActiveTab('understanding')}
            className={`rounded px-2.5 py-1.5 transition-colors flex items-center gap-1.5 ${
              activeTab === 'understanding'
                ? 'bg-accent/20 text-accent font-semibold border border-accent/40'
                : 'text-muted hover:bg-panel hover:text-ink border border-border'
            }`}
          >
            <span>🔍 Understanding</span>
            {understandingReport && (
              <span className="rounded-full bg-accent/30 px-1.5 py-0.2 text-[10px] text-accent">
                {understandingReport.claims.length}
              </span>
            )}
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('terminology')}
            className={`rounded px-2.5 py-1.5 transition-colors flex items-center gap-1.5 ${
              activeTab === 'terminology'
                ? 'bg-accent/20 text-accent font-semibold border border-accent/40'
                : 'text-muted hover:bg-panel hover:text-ink border border-border'
            }`}
          >
            <span>📚 Terminology</span>
          </button>
        </div>
      </nav>

      {/* Active Tab View */}
      <div>
        {activeTab === 'intake' && (
          <ProblemIntakeView
            factoryId={factoryId}
            onIngest={handleIngest}
            isPending={isPending}
            hasSession={Boolean(sessionId)}
          />
        )}

        {activeTab === 'understanding' && (
          <UnderstandingReportView
            report={understandingReport}
            sourceText={rawSourceText}
          />
        )}

        {activeTab === 'terminology' && (
          <TerminologyManagerView
            factoryId={factoryId}
            onNotice={setNotice}
          />
        )}

        {activeTab === 'facts' && (
          <FactBoardView
            state={state}
            onConfirm={handleConfirm}
            onAssume={handleAssume}
            onAssert={handleAssert}
            isPending={isPending}
          />
        )}

        {activeTab === 'formulation' && (
          <FormulationReviewView
            state={state}
            formulation={formulation}
            onBuild={handleBuild}
            isPending={isPending}
          />
        )}

        {activeTab === 'scenarios' && (
          <ScenarioWorkbenchView
            scenarios={scenarios}
            problem={formulation?.problem ?? null}
            onUpdateScenarios={(updated) => {
              setScenarios(updated)
              setBrief(null)
            }}
            onRunDecision={handleRunDecision}
            isPending={isPending}
          />
        )}

        {activeTab === 'decision' && (
          <DecisionIntelligenceView
            brief={brief}
            onProceedToApproval={() => setActiveTab('approval')}
          />
        )}

        {activeTab === 'approval' && (
          <HumanApprovalView
            brief={brief}
            history={history}
            canDecide={canDecide}
            onDecideOn={handleDecideOn}
            isPending={isPending}
          />
        )}
      </div>
    </>
  )
}
