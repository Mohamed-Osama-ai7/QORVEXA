import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { PlanningStudio } from './PlanningStudio'

function renderStudio() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <PlanningStudio />
    </QueryClientProvider>
  )
}

describe('PlanningStudio', () => {
  it('renders studio header, advisory badge, and step tabs', async () => {
    renderStudio()

    expect(screen.getByText('Production Planning & Decision Studio')).toBeInTheDocument()
    expect(screen.getByText('ADVISORY ONLY · READ-ONLY')).toBeInTheDocument()
    expect(screen.getByText('1. Intake & Claims')).toBeInTheDocument()
    expect(screen.getByText('2. Fact Board')).toBeInTheDocument()
    expect(screen.getByText('3. Formulation & Units')).toBeInTheDocument()
    expect(screen.getByText('4. Scenario Workbench')).toBeInTheDocument()
    expect(screen.getByText('5. Solver & Intelligence')).toBeInTheDocument()
    expect(screen.getByText('6. Human Approval & Audit')).toBeInTheDocument()
  })

  it('renders epistemic boundary notice in intake step', () => {
    renderStudio()

    expect(screen.getByText('Epistemic Boundary Notice')).toBeInTheDocument()
    expect(
      screen.getByText(/All extracted statements enter the system strictly as/i)
    ).toBeInTheDocument()
  })

  it('navigates across tabs correctly', async () => {
    const user = userEvent.setup()
    renderStudio()

    // Click Fact Board tab
    await user.click(screen.getByText('2. Fact Board'))
    expect(
      screen.getByText(/No active session state. Please ingest factory text or select a session./i)
    ).toBeInTheDocument()

    // Click Scenario Workbench tab
    await user.click(screen.getByText('4. Scenario Workbench'))
    expect(screen.getByText('Baseline Mathematical Production Mix')).toBeInTheDocument()
    expect(screen.getByText('+ MACH120 (MACHINE)')).toBeInTheDocument()

    // Click Human Approval & Audit tab
    await user.click(screen.getByText('6. Human Approval & Audit'))
    expect(screen.getByText('Human Approval & Decision Sign-Off')).toBeInTheDocument()
    expect(screen.getByText('Persistent Audit History')).toBeInTheDocument()
  })
})
