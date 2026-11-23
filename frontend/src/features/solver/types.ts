import type {
  CopilotQueryResponse,
  EventEvidencePackage,
  ImpactQuantity,
  MaintenanceDecisionComparisonResponse,
  MaintenanceOptionEvaluation,
  RcaAssessment,
  TwinSimulationResponse,
} from '../../api/generated/model'

export type ProblemClass =
  | 'air_leak'
  | 'excess_duty'
  | 'thermal_runaway'
  | 'sensor_drift'
  | 'pressure_decay'

export type SolverMode = 'optimization' | 'digital_twin' | 'rca' | 'copilot' | 'composite'

export type ExecutionStatus =
  | 'IDLE'
  | 'RUNNING'
  | 'COMPLETED'
  | 'INFEASIBLE'
  | 'FAILED'
  | 'UNKNOWN'

export interface IntakeState {
  assetId: string
  incidentId: string
  problemClass: ProblemClass
  problemTitle: string
  observationWindowStart: string
  observationWindowEnd: string
  planningHorizonHours: number
  measuredLoadDuty: number
  measuredMotorCurrentA: number
  measuredOilTempC: number
  estimatedLeakRateBarPerMin: number
  operatorNotes: string
}

export interface FormulationState {
  operationalRegime: string
  requireZeroLpsViolation: boolean
  maxAllowableDuty: number
  maxAllowableOilTempC: number
  maxAllowableCostEur: number | null
  maxAllowableDowntimeHours: number | null
  ratedApuPowerKw: number
  unloadedPowerFraction: number
  nominalBaselineDuty: number
  electricityEurPerKwh: number
  plannedDepotMaintenanceEur: number
  monitoringCheckEur: number
  unplannedServiceDisruptionEurPerHr: number
  costWeight: number
  reliabilityWeight: number
  downtimeWeight: number
}

export interface SolverConfig {
  solverMode: SolverMode
  twinScenario: 'baseline' | 'leak' | 'continued_operation' | 'maintenance_repair'
  twinHorizonMinutes: number
  twinRepairAtMinute: number
  copilotQuestion: string
  simulateInfeasibility: boolean
  simulateUnknown: boolean
  simulateFailure: boolean
}

export interface StageLogEntry {
  id: string
  timestamp: string
  stageIndex: number
  stageName: string
  message: string
  level: 'info' | 'warn' | 'error' | 'success'
}

export interface ExecutionState {
  status: ExecutionStatus
  currentStageIndex: number
  stageLogs: StageLogEntry[]
  elapsedMs: number
  errorDetail: string | null
  unknownReason: string | null
  infeasibilityViolations: string[] | null
}

export interface ResultsState {
  optimizationResult: MaintenanceDecisionComparisonResponse | null
  twinResult: TwinSimulationResponse | null
  rcaResult: RcaAssessment | null
  copilotResult: CopilotQueryResponse | null
  evidencePackage: EventEvidencePackage | null
  recommendedOption: string | null
  infeasibleOptions: MaintenanceOptionEvaluation[]
  paretoFrontier: string[]
  advisoryNotice: string
  baselineQuantities: ImpactQuantity[]
}

export interface VerificationState {
  decision: 'confirm' | 'defer' | 'dismiss' | null
  selectedOption: 'repair_now' | 'schedule' | 'monitor'
  decisionRationale: string
  humanObservationCategory: 'visual_inspection' | 'auditory_hiss' | 'thermal_measurement' | 'gauge_cross_check'
  humanObservationNote: string
  isObservationLogged: boolean
  isDecisionSubmitted: boolean
  auditVerified: boolean
  auditHash: string | null
}
