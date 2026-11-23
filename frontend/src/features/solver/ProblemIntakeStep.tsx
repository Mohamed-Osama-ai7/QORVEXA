import { type ChangeEvent, useState } from 'react'
import { describeApiError } from '../../api/errors'
import { useListIncidents } from '../../api/generated/incidents/incidents'
import type { IntakeState, ProblemClass } from './types'

export interface ProblemIntakeStepProps {
  state: IntakeState
  onChange: (updater: (prev: IntakeState) => IntakeState) => void
  onNext: () => void
}

const PROBLEM_CLASS_LABELS: Record<ProblemClass, string> = {
  air_leak: 'Pneumatic Air Leakage',
  excess_duty: 'Compressor Duty Exceedance',
  thermal_runaway: 'Oil Thermal Elevation',
  sensor_drift: 'Sensor Inconsistency / Unobservability',
  pressure_decay: 'Unloaded Pressure Decay',
}

interface PresetOption {
  id: string
  name: string
  description: string
  patch: Partial<IntakeState>
}

const PRESETS: PresetOption[] = [
  {
    id: 'metropt3-f1-leak',
    name: 'MetroPT-3 Real Leak Episode (F1 Baseline)',
    description: 'High duty cycle (85%) with rapid pressure drop during unloaded regime.',
    patch: {
      problemClass: 'air_leak',
      problemTitle: 'APU Pneumatic Line Air Leakage (MetroPT-3 Episode F1)',
      measuredLoadDuty: 0.85,
      measuredMotorCurrentA: 7.2,
      measuredOilTempC: 76.5,
      estimatedLeakRateBarPerMin: 0.35,
      planningHorizonHours: 8.0,
      operatorNotes:
        'Audible hiss reported near car 1 APU reservoir. Duty cycle increased from nominal 15% to 85%.',
    },
  },
  {
    id: 'thermal-duty-runaway',
    name: 'Critical Duty Runaway & Thermal Stress',
    description: 'Severe duty load (94%) approaching continuous operation with elevated oil temperature.',
    patch: {
      problemClass: 'excess_duty',
      problemTitle: 'Compressor Severe Duty Cycling & Thermal Elevation',
      measuredLoadDuty: 0.94,
      measuredMotorCurrentA: 8.4,
      measuredOilTempC: 98.2,
      estimatedLeakRateBarPerMin: 0.55,
      planningHorizonHours: 12.0,
      operatorNotes:
        'Compressor running almost continuously. Oil temperature exceeding 95°C advisory warning threshold.',
    },
  },
  {
    id: 'sensor-drift-gate',
    name: 'Sensor Inconsistency & Epistemic Gate',
    description: 'Discrepancy between TP3 decay slope and electrical valve command telemetry.',
    patch: {
      problemClass: 'sensor_drift',
      problemTitle: 'Pneumatic Pressure Sensor Calibration Drift',
      measuredLoadDuty: 0.42,
      measuredMotorCurrentA: 5.1,
      measuredOilTempC: 64.0,
      estimatedLeakRateBarPerMin: 0.12,
      planningHorizonHours: 24.0,
      operatorNotes:
        'TP3 pressure sensor telemetry shows intermittent steps without corresponding compressor cycling.',
    },
  },
]

export function ProblemIntakeStep({ state, onChange, onNext }: ProblemIntakeStepProps) {
  const [selectedPreset, setSelectedPreset] = useState<string>('metropt3-f1-leak')
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({})

  const incidentsQuery = useListIncidents()
  const incidents = incidentsQuery.data?.status === 200 ? incidentsQuery.data.data.items : []

  const handlePresetSelect = (presetId: string) => {
    setSelectedPreset(presetId)
    const preset = PRESETS.find((p) => p.id === presetId)
    if (preset) {
      onChange((prev) => ({
        ...prev,
        ...preset.patch,
      }))
      setValidationErrors({})
    }
  }

  const handleIncidentSelect = (e: ChangeEvent<HTMLSelectElement>) => {
    const incId = e.target.value
    if (!incId) {
      onChange((prev) => ({ ...prev, incidentId: '' }))
      return
    }
    const found = incidents.find((i) => i.id === incId)
    if (found) {
      onChange((prev) => ({
        ...prev,
        incidentId: found.id,
        assetId: found.asset_id,
        problemTitle: `Incident ${found.id.slice(0, 8)} (${found.detector})`,
        measuredLoadDuty: Math.min(1.0, Math.max(0.0, found.peak_score * 0.8)),
        observationWindowStart: found.episode_start,
        observationWindowEnd: found.episode_end,
      }))
    }
  }

  const validate = (): boolean => {
    const errors: Record<string, string> = {}

    if (!state.assetId.trim()) {
      errors.assetId = 'Asset ID is required.'
    } else if (!/^[a-z0-9_-]{1,64}$/.test(state.assetId.trim())) {
      errors.assetId = 'Asset ID must be a lowercase alphanumeric identifier (max 64 chars).'
    }

    if (state.incidentId.trim() && !/^[a-f0-9]{32}$/.test(state.incidentId.trim())) {
      errors.incidentId = 'Incident ID must be a 32-character lowercase hex string or blank.'
    }

    if (!state.problemTitle.trim()) {
      errors.problemTitle = 'Problem title is required.'
    }

    if (
      !Number.isFinite(state.measuredLoadDuty) ||
      state.measuredLoadDuty < 0 ||
      state.measuredLoadDuty > 1
    ) {
      errors.measuredLoadDuty = 'Load duty fraction must be between 0.00 and 1.00.'
    }

    if (
      !Number.isFinite(state.measuredMotorCurrentA) ||
      state.measuredMotorCurrentA < 0 ||
      state.measuredMotorCurrentA > 20
    ) {
      errors.measuredMotorCurrentA = 'Motor current must be between 0.0 and 20.0 A.'
    }

    if (
      !Number.isFinite(state.measuredOilTempC) ||
      state.measuredOilTempC < -20 ||
      state.measuredOilTempC > 150
    ) {
      errors.measuredOilTempC = 'Oil temperature must be between -20.0 and 150.0 °C.'
    }

    if (
      !Number.isFinite(state.estimatedLeakRateBarPerMin) ||
      state.estimatedLeakRateBarPerMin < 0 ||
      state.estimatedLeakRateBarPerMin > 1.5
    ) {
      errors.estimatedLeakRateBarPerMin = 'Leak rate must be between 0.00 and 1.50 bar/min.'
    }

    if (
      !Number.isFinite(state.planningHorizonHours) ||
      state.planningHorizonHours < 1 ||
      state.planningHorizonHours > 72
    ) {
      errors.planningHorizonHours = 'Planning horizon must be between 1.0 and 72.0 hours.'
    }

    setValidationErrors(errors)
    return Object.keys(errors).length === 0
  }

  const handleProceed = () => {
    if (validate()) {
      onNext()
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <header>
        <span className="text-xs font-mono uppercase tracking-wider text-accent">
          Workflow Stage 1 of 6
        </span>
        <h2 className="text-xl font-semibold text-ink">Problem Intake &amp; Symptom Ingestion</h2>
        <p className="mt-1 text-sm text-muted">
          Define the operational problem instance for the asset. Import telemetry from an existing
          incident episode or specify custom physical symptoms.
        </p>
      </header>

      {/* Preset selection */}
      <section
        aria-labelledby="intake-presets"
        className="rounded-lg border border-border bg-panel p-4"
      >
        <h3 id="intake-presets" className="text-xs font-semibold uppercase tracking-wider text-muted">
          Industrial Case Presets
        </h3>
        <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-3">
          {PRESETS.map((p) => {
            const isSelected = selectedPreset === p.id
            return (
              <button
                key={p.id}
                type="button"
                onClick={() => handlePresetSelect(p.id)}
                className={`flex flex-col rounded border p-3 text-left transition-colors ${
                  isSelected
                    ? 'border-accent bg-accent/10 text-ink shadow-sm'
                    : 'border-border bg-surface/50 text-muted hover:border-border hover:text-ink'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-ink">{p.name}</span>
                  {isSelected && (
                    <span className="rounded bg-accent/20 px-1.5 py-0.5 text-[10px] font-mono text-accent">
                      Active
                    </span>
                  )}
                </div>
                <p className="mt-1 text-[11px] leading-relaxed text-muted">{p.description}</p>
              </button>
            )
          })}
        </div>
      </section>

      {/* Database Incident Link */}
      <section
        aria-labelledby="incident-link-heading"
        className="rounded-lg border border-border bg-panel p-4"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3
            id="incident-link-heading"
            className="text-xs font-semibold uppercase tracking-wider text-muted"
          >
            Optional: Link to Stored Incident Record
          </h3>
          {incidentsQuery.isPending && (
            <span className="text-xs text-muted">Loading recorded incidents…</span>
          )}
          {incidentsQuery.isError && (
            <span className="text-xs text-critical">
              Could not load incidents: {describeApiError(incidentsQuery.error)}
            </span>
          )}
        </div>

        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="incident-select" className="block text-xs font-medium text-muted">
              Select Incident from Database
            </label>
            <select
              id="incident-select"
              value={state.incidentId}
              onChange={handleIncidentSelect}
              className="mt-1 w-full rounded border border-border bg-surface px-2.5 py-1.5 font-mono text-xs text-ink"
            >
              <option value="">-- No Incident Selected (Manual / Preset Case) --</option>
              {incidents.map((inc) => (
                <option key={inc.id} value={inc.id}>
                  {inc.id.slice(0, 8)}… | {inc.asset_id} | {inc.detector} (peak:{' '}
                  {inc.peak_score.toFixed(2)})
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="incident-hex-input" className="block text-xs font-medium text-muted">
              Incident ID (Hex Hash)
            </label>
            <input
              id="incident-hex-input"
              type="text"
              value={state.incidentId}
              placeholder="e.g. 550e8400e29b41d4a716446655440000"
              onChange={(e) => {
                const val = e.target.value
                onChange((prev) => ({ ...prev, incidentId: val }))
              }}
              className="mt-1 w-full rounded border border-border bg-surface px-2.5 py-1.5 font-mono text-xs text-ink"
            />
            {validationErrors.incidentId && (
              <p className="mt-1 text-[11px] text-critical">{validationErrors.incidentId}</p>
            )}
          </div>
        </div>
      </section>

      {/* Main Intake Form */}
      <section
        aria-labelledby="intake-details-heading"
        className="rounded-lg border border-border bg-panel p-4"
      >
        <h3
          id="intake-details-heading"
          className="text-xs font-semibold uppercase tracking-wider text-muted"
        >
          Problem Definition &amp; Observed Telemetry
        </h3>

        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <label htmlFor="intake-asset-id" className="block text-xs font-medium text-muted">
              Target Asset Identifier
            </label>
            <input
              id="intake-asset-id"
              type="text"
              value={state.assetId}
              onChange={(e) => {
                const val = e.target.value
                onChange((prev) => ({ ...prev, assetId: val }))
              }}
              className="mt-1 w-full rounded border border-border bg-surface px-2.5 py-1.5 font-mono text-xs text-ink"
            />
            {validationErrors.assetId && (
              <p className="mt-1 text-[11px] text-critical">{validationErrors.assetId}</p>
            )}
          </div>

          <div>
            <label htmlFor="intake-category" className="block text-xs font-medium text-muted">
              Problem Classification
            </label>
            <select
              id="intake-category"
              value={state.problemClass}
              onChange={(e) => {
                const val = e.target.value as ProblemClass
                onChange((prev) => ({ ...prev, problemClass: val }))
              }}
              className="mt-1 w-full rounded border border-border bg-surface px-2.5 py-1.5 text-xs text-ink"
            >
              {(Object.keys(PROBLEM_CLASS_LABELS) as ProblemClass[]).map((k) => (
                <option key={k} value={k}>
                  {PROBLEM_CLASS_LABELS[k]}
                </option>
              ))}
            </select>
          </div>

          <div className="sm:col-span-2 lg:col-span-1">
            <label htmlFor="intake-title" className="block text-xs font-medium text-muted">
              Problem Title / Incident Descriptor
            </label>
            <input
              id="intake-title"
              type="text"
              value={state.problemTitle}
              onChange={(e) => {
                const val = e.target.value
                onChange((prev) => ({ ...prev, problemTitle: val }))
              }}
              className="mt-1 w-full rounded border border-border bg-surface px-2.5 py-1.5 text-xs text-ink"
            />
            {validationErrors.problemTitle && (
              <p className="mt-1 text-[11px] text-critical">{validationErrors.problemTitle}</p>
            )}
          </div>
        </div>

        {/* Telemetry Measurements */}
        <div className="mt-5 border-t border-border pt-4">
          <h4 className="text-xs font-medium text-ink">
            Observed Physical Metrics &amp; Operational Triggers
          </h4>
          <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <label htmlFor="intake-duty" className="block text-xs text-muted">
                Observed Duty Cycle Fraction <span className="font-mono text-ink">(0–1)</span>
              </label>
              <input
                id="intake-duty"
                type="number"
                step="0.01"
                min="0"
                max="1"
                value={state.measuredLoadDuty}
                onChange={(e) => {
                  const val = Number.parseFloat(e.target.value) || 0
                  onChange((prev) => ({ ...prev, measuredLoadDuty: val }))
                }}
                className="mt-1 w-full rounded border border-border bg-surface px-2.5 py-1.5 font-mono text-xs text-ink"
              />
              <span className="text-[10px] text-muted">
                Nominal: ~0.15 | Current: {(state.measuredLoadDuty * 100).toFixed(0)}%
              </span>
              {validationErrors.measuredLoadDuty && (
                <p className="mt-1 text-[11px] text-critical">{validationErrors.measuredLoadDuty}</p>
              )}
            </div>

            <div>
              <label htmlFor="intake-leak-rate" className="block text-xs text-muted">
                Estimated Air Leak Rate <span className="font-mono text-ink">(bar/min)</span>
              </label>
              <input
                id="intake-leak-rate"
                type="number"
                step="0.05"
                min="0"
                max="1.5"
                value={state.estimatedLeakRateBarPerMin}
                onChange={(e) => {
                  const val = Number.parseFloat(e.target.value) || 0
                  onChange((prev) => ({ ...prev, estimatedLeakRateBarPerMin: val }))
                }}
                className="mt-1 w-full rounded border border-border bg-surface px-2.5 py-1.5 font-mono text-xs text-ink"
              />
              <span className="text-[10px] text-muted">Normal: &lt;0.05 bar/min</span>
              {validationErrors.estimatedLeakRateBarPerMin && (
                <p className="mt-1 text-[11px] text-critical">
                  {validationErrors.estimatedLeakRateBarPerMin}
                </p>
              )}
            </div>

            <div>
              <label htmlFor="intake-oil-temp" className="block text-xs text-muted">
                Compressor Oil Temp <span className="font-mono text-ink">(°C)</span>
              </label>
              <input
                id="intake-oil-temp"
                type="number"
                step="0.5"
                min="-20"
                max="150"
                value={state.measuredOilTempC}
                onChange={(e) => {
                  const val = Number.parseFloat(e.target.value) || 0
                  onChange((prev) => ({ ...prev, measuredOilTempC: val }))
                }}
                className="mt-1 w-full rounded border border-border bg-surface px-2.5 py-1.5 font-mono text-xs text-ink"
              />
              <span className="text-[10px] text-muted">Warning: ≥85°C | Trip: ≥100°C</span>
              {validationErrors.measuredOilTempC && (
                <p className="mt-1 text-[11px] text-critical">{validationErrors.measuredOilTempC}</p>
              )}
            </div>

            <div>
              <label htmlFor="intake-horizon" className="block text-xs text-muted">
                Planning Horizon <span className="font-mono text-ink">(hours)</span>
              </label>
              <input
                id="intake-horizon"
                type="number"
                step="1"
                min="1"
                max="72"
                value={state.planningHorizonHours}
                onChange={(e) => {
                  const val = Number.parseFloat(e.target.value) || 8
                  onChange((prev) => ({ ...prev, planningHorizonHours: val }))
                }}
                className="mt-1 w-full rounded border border-border bg-surface px-2.5 py-1.5 font-mono text-xs text-ink"
              />
              <span className="text-[10px] text-muted">Range: 1h to 72h</span>
              {validationErrors.planningHorizonHours && (
                <p className="mt-1 text-[11px] text-critical">
                  {validationErrors.planningHorizonHours}
                </p>
              )}
            </div>
          </div>
        </div>

        {/* Operator Notes */}
        <div className="mt-4 border-t border-border pt-4">
          <label htmlFor="intake-notes" className="block text-xs font-medium text-muted">
            Field Inspection Notes &amp; Operator Observations
          </label>
          <textarea
            id="intake-notes"
            rows={2}
            value={state.operatorNotes}
            onChange={(e) => {
              const val = e.target.value
              onChange((prev) => ({ ...prev, operatorNotes: val }))
            }}
            placeholder="Add relevant engineering field observations, dispatch records, or maintenance logs…"
            className="mt-1 w-full rounded border border-border bg-surface p-2 text-xs text-ink placeholder:text-muted/50"
          />
        </div>
      </section>

      {/* Navigation action */}
      <div className="flex items-center justify-between border-t border-border pt-4">
        <span className="text-xs text-muted">
          All values validated against physical operating ranges.
        </span>
        <button
          type="button"
          onClick={handleProceed}
          className="rounded bg-accent px-4 py-2 text-xs font-semibold text-white transition-opacity hover:opacity-90"
        >
          Lock Intake &amp; Proceed to Problem Formulation →
        </button>
      </div>
    </div>
  )
}
