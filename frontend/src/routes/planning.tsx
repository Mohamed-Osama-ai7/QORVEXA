import { createFileRoute } from '@tanstack/react-router'
import { PlanningStudio } from '../features/planning/PlanningStudio'

export const Route = createFileRoute('/planning')({
  component: PlanningPage,
})

function PlanningPage() {
  return <PlanningStudio />
}
