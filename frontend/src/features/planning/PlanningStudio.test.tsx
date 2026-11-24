import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PlanningStudio } from './PlanningStudio'

const SESSIONS_URL = '/api/v1/industrial/sessions'
const BRIEF_SHA = 'a'.repeat(64)
const SESSION = { id: 'FIRST', title: 'First session', created_by: 'planner', created_at: '2026-11-23' }
const READY_STATE = { status: 'READY', facts: [], issues: [], ledger_head: 'head', state_sha256: 'state' }
const FORMULATION = {
  state: READY_STATE,
  compiled: { manifest: { spec_sha256: 'model-digest' } },
  problem: {
    mode: 'MAXIMIZE_PROFIT',
    currency: 'USD',
    products: [],
    resources: [{ id: 'PRESS', name: 'Press', unit: 'h', capacity: '30', kind: 'MACHINE' }],
  },
  record: { record_sha256: 'record-digest' },
}
const BRIEF = {
  baseline_id: 'BASE',
  baseline_status: 'VERIFIED_NUMERICAL_SOLUTION',
  brief_sha256: BRIEF_SHA,
  executive_summary: ['Review the press capacity.'],
  comparison: null,
  recommendation: {
    status: 'CONDITIONALLY_RECOMMENDED',
    recommended_scenario_id: 'PRESS-40',
    unresolved_assumptions: ['CAPACITY_COST_UNMODELED'],
    reasons: ['Capacity is available.'],
  },
}
const RECORD = {
  seq: 1,
  session_id: 'FIRST',
  brief_sha256: BRIEF_SHA,
  verdict: 'APPROVED',
  scenario_id: 'PRESS-40',
  approver_id: 'server-approver',
  recorded_at: '2026-11-23',
  record_sha256: 'server-record-digest',
}

function json(body: unknown, status = 200) {
  return Response.json(body, { status })
}

function renderStudio(overrides: Record<string, (request?: RequestInit) => Response | Promise<Response>> = {}) {
  const fetchMock = vi.fn((url: string, request?: RequestInit) => {
    const respond = overrides[url]
    if (respond) return Promise.resolve(respond(request))
    if (url === '/api/v1/me') {
      return Promise.resolve(json({ tenant_id: 'factory-1', roles: ['approver'] }))
    }
    if (url === SESSIONS_URL) return Promise.resolve(json([SESSION]))
    if (url.endsWith('/state')) return Promise.resolve(json(READY_STATE))
    if (url.endsWith('/build')) return Promise.resolve(json(FORMULATION))
    if (url.endsWith('/history')) return Promise.resolve(json({ records: [] }))
    return Promise.resolve(json({ detail: 'Not found' }, 404))
  })
  vi.stubGlobal('fetch', fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <PlanningStudio />
    </QueryClientProvider>,
  )
  return { fetchMock, user: userEvent.setup() }
}

async function compileFormulation(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole('option', { name: /First session/ })
  await user.click(screen.getByRole('button', { name: '3. Formulation & Units' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Compile Formulation' })).toBeEnabled())
  await user.click(screen.getByRole('button', { name: 'Compile Formulation' }))
}

async function openDecisionForm(user: ReturnType<typeof userEvent.setup>) {
  await compileFormulation(user)
  await user.click(await screen.findByRole('button', { name: 'Run Real Optimization & Intelligence' }))
  await user.click(await screen.findByRole('button', { name: /Review & Record Human Decision/ }))
}

afterEach(() => vi.unstubAllGlobals())

describe('PlanningStudio', () => {
  it('renders advisory intake and configured step tabs', async () => {
    renderStudio()

    expect(screen.getByText('Production Planning & Decision Studio')).toBeInTheDocument()
    expect(screen.getByText('ADVISORY ONLY · READ-ONLY')).toBeInTheDocument()
    expect(screen.getByText(/All extracted statements enter the system strictly as/i)).toBeInTheDocument()
    expect(screen.getByText('2. Fact Board')).toBeInTheDocument()
    expect(screen.getByText('6. Human Approval & Audit')).toBeInTheDocument()
    expect(await screen.findByRole('option', { name: /First session/ })).toBeInTheDocument()
  })

  it('shows a session list failure without exposing a fake session', async () => {
    renderStudio({ [SESSIONS_URL]: () => json({ detail: 'Forbidden' }, 403) })

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load planning sessions.')
    expect(screen.getByRole('combobox', { name: 'Planning session' })).toBeDisabled()
    expect(screen.queryByRole('option', { name: /Precision Mix Optimization/ })).not.toBeInTheDocument()
  })

  it('requires a compiled formulation before running a decision', async () => {
    const { user } = renderStudio({
      [`${SESSIONS_URL}/FIRST/build`]: () => json({ state: READY_STATE, compiled: null }),
    })

    await compileFormulation(user)
    expect(await screen.findByRole('alert')).toHaveTextContent('The formulation did not compile.')

    await user.click(screen.getByRole('button', { name: '4. Scenario Workbench' }))
    expect(screen.getByText('Compile the formulation before running a decision.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Run Real Optimization/ })).not.toBeInTheDocument()
  })

  it('displays solver and audit-loading failures without verified results or sign-off', async () => {
    const { user } = renderStudio({
      [`${SESSIONS_URL}/FIRST/decide`]: () => json({ detail: 'Solver unavailable' }, 503),
      [`${SESSIONS_URL}/FIRST/history`]: () => json({ detail: 'Audit unavailable' }, 503),
    })

    await compileFormulation(user)
    await user.click(await screen.findByRole('button', { name: /Run Real Optimization/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not generate a decision brief.')

    await user.click(screen.getByRole('button', { name: '5. Solver & Intelligence' }))
    expect(screen.getByText(/No decision brief computed/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '6. Human Approval & Audit' }))
    expect(screen.getByText('Audit history is unavailable.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Commit Immutable Decision/ })).not.toBeInTheDocument()
  })

  it('requires a human verdict, rationale and acknowledgements, then displays only server history', async () => {
    let committed = false
    const postDecision = vi.fn(() => json({ detail: 'Forbidden' }, 403))
    const { user } = renderStudio({
      [`${SESSIONS_URL}/FIRST/decide`]: () => json(BRIEF),
      [`${SESSIONS_URL}/FIRST/decide-on`]: () => postDecision(),
      [`${SESSIONS_URL}/FIRST/history`]: () => json({ records: committed ? [RECORD] : [] }),
    })

    await openDecisionForm(user)
    const submit = screen.getByRole('button', { name: /Commit Immutable Decision/ })
    const reason = screen.getByRole('textbox', { name: /Engineering Justification/ })
    expect(reason).toHaveValue('')
    expect(submit).toBeDisabled()
    await user.click(screen.getByRole('radio', { name: /APPROVE/ }))
    expect(screen.getByText('PRESS-40')).toBeInTheDocument()
    const ack = screen.getByRole('checkbox', { name: 'CAPACITY_COST_UNMODELED' })
    expect(ack).not.toBeChecked()
    await user.type(reason, 'Margin supports additional hours.')
    expect(submit).toBeDisabled()
    await user.click(ack)
    await user.click(submit)
    expect(await screen.findByRole('alert')).toHaveTextContent('Your account does not have permission.')
    expect(screen.getByText('No decisions are recorded for this session.')).toBeInTheDocument()
    expect(screen.queryByText('server-approver')).not.toBeInTheDocument()

    postDecision.mockImplementation(() => {
      committed = true
      return json(RECORD)
    })
    await user.click(submit)
    expect(await screen.findByText('server-approver')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Decision recorded by the server as APPROVED.')
  })

  it('disables approval for a nonverified brief', async () => {
    const { user } = renderStudio({
      [`${SESSIONS_URL}/FIRST/decide`]: () => json({
        ...BRIEF,
        baseline_status: 'INFEASIBLE',
        recommendation: {
          ...BRIEF.recommendation,
          status: 'NO_VERIFIED_RECOMMENDATION',
          recommended_scenario_id: null,
        },
      }),
    })

    await openDecisionForm(user)
    expect(screen.getByRole('radio', { name: /APPROVE/ })).toBeDisabled()
    expect(screen.getByRole('radio', { name: /REJECT/ })).toBeEnabled()
  })

  it('reloads authoritative history after an uncertain write and closes the sign-off form', async () => {
    let committed = false
    const { user } = renderStudio({
      [`${SESSIONS_URL}/FIRST/decide`]: () => json(BRIEF),
      [`${SESSIONS_URL}/FIRST/decide-on`]: () => {
        committed = true
        return json({ detail: 'Gateway timed out' }, 504)
      },
      [`${SESSIONS_URL}/FIRST/history`]: () => json({ records: committed ? [{ ...RECORD, verdict: 'REJECTED' }] : [] }),
    })

    await openDecisionForm(user)
    await user.click(screen.getByRole('radio', { name: /REJECT/ }))
    await user.type(screen.getByRole('textbox', { name: /Engineering Justification/ }), 'Further tests are required.')
    await user.click(screen.getByRole('button', { name: /Commit Immutable Decision/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not confirm that the decision was recorded.')
    expect(screen.getByText('server-approver')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Commit Immutable Decision/ })).not.toBeInTheDocument()
  })

  it('discards a late response when switching planning sessions', async () => {
    let finishDecision: (response: Response) => void = () => {}
    const { fetchMock, user } = renderStudio({
      [SESSIONS_URL]: () => json([SESSION, { ...SESSION, id: 'SECOND', title: 'Second session' }]),
      [`${SESSIONS_URL}/FIRST/decide`]: () => new Promise((resolve) => { finishDecision = resolve }),
    })

    await compileFormulation(user)
    await user.click(await screen.findByRole('button', { name: /Run Real Optimization/ }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(`${SESSIONS_URL}/FIRST/decide`, expect.anything()))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Planning session' }), 'SECOND')
    await act(async () => finishDecision(json(BRIEF)))

    await user.click(screen.getByRole('button', { name: '5. Solver & Intelligence' }))
    expect(screen.getByText(/No decision brief computed/)).toBeInTheDocument()
    expect(screen.queryByText('PRESS-40')).not.toBeInTheDocument()
  })
})
