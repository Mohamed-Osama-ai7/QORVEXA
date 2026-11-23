# Industrial Units Engine

Package `backend/src/qorvexa/industrial/units/` · tests `backend/tests/test_industrial_units.py` (53).
Independent: imports nothing from governance, formulation, solver fabric or decision code.

## Model
- **Dimension**: sorted integer-exponent vector over bases L, M, T, N (count) plus one base per currency (`CUR:EUR`).
  Currencies are therefore distinct dimensions and can never be unified by algebra.
- **Unit**: canonical atoms + exact `Fraction` scale to the coherent base + dimension. Scale growth is capped (2048 bits),
  exponents capped (±12), expressions capped (200 chars, 64 tokens, depth 8).
- **Registry**: mass, length, time, area, volume, count, energy, power, 11 currencies. Ambiguous names (`ton`, `oz`, `gal`,
  `t`, `hp`, `btu`, `pound`...) -> `AMBIGUOUS_UNIT`; offset units (`degC`, `degF`, `K`...) -> `OFFSET_UNIT_UNSUPPORTED`;
  anything else unknown -> `UNKNOWN_UNIT`. Custom units only via `registry.extended(symbol, of, exact_factor)` (new registry,
  never mutates the default; currencies cannot be scaled/redefined).
- **Expressions**: `kg/piece`, `m^2`, `kW*h`, `(kg/h)^2`, `1/h`; `/` is left-associative. Hand-written parser, no eval,
  only integer exponents and literal `1`.
- **Quantity**: exact rational value; floats/bools refused; `add/sub` require the *identical* unit (`CONVERSION_REQUIRED` if
  merely convertible); `mul/div` combine units; `compare` is exact across convertible units; `to(unit)` is the only
  conversion path and returns a `ConversionResult` (factor + 3-line trace). Currency conversion is refused (`CURRENCY_MISMATCH`).
- **Compat**: `SAME_UNIT | CONVERTIBLE | CURRENCY_MISMATCH | DIMENSION_MISMATCH` via `classify(a, b)`.

## Validation
`validate_linear_units(variables, constraints, objective, bounds, registry)` checks `coefficient_unit x variable_unit = rhs_unit`
per term, objective terms against the objective unit (currency identity included) and bounds against variable units. Report
status: `CONSISTENT`; `NEEDS_EXPLICIT_CONVERSION` (same dimension, different scale; exact `factor` supplied, never applied);
`INVALID` (dimension/currency/unknown). Issues are sorted; `sha256` is deterministic.
`validate_planning_units(PlanningUnitsInput)` maps products, bounds/demand, consumption, capacities, value coefficients and the
objective currency onto it. `planning_units_from_problem(problem)` is a read-only duck-typed adapter for the existing
Production Planning model (value unit `<currency>/<product unit>`, coefficient `<resource unit>/<product unit>`).

## Limitations
No offset/nonlinear units, no FX, no fractional exponents, no prefix parsing (e.g. `Mg`), no temperature. Unit strings in the
existing Production Planning model carry no separate coefficient unit, so adapter results can only catch unknown/ambiguous
strings there; real dimension checking needs the formulation layer to capture coefficient units (below).

---
## Integration (implemented): unit-aware formulation and solver gate

Tests: `backend/tests/test_unit_aware_formulation.py` (39).

**Ledger keys:** `product:<P>:value_unit`, `product:<P>:bound_unit`, `consumption:<R>:<P>:unit` (coefficient unit),
existing `product:<P>:unit` / `resource:<R>:unit` (capacity unit), `unitdef:<SYMBOL>` = `<exact factor>|<unit expression>`.
LLM actors may only *propose* these; they enter the model only after human assert/confirm.

**Strict vs legacy:** if any value/bound/coefficient unit claim exists the ledger is *strict*: all needed unit claims must be
stated (never assumed or defaulted; bound unit needed only when min/max are non-trivial). Ledgers with no unit metadata stay
legacy: `units_status = LEGACY_UNITS_UNVERIFIED`, never labelled validated.

**Explicit conversion:** same-dimension/different-scale terms yield `CONVERSION_REQUIRED` (formulation blocked) with the
source/target units, the exact factor `n/d`, and candidate conversions `slot|to_unit|n/d|exact|inexact`. Slots: `consumption:R:P`,
`resource:R` (capacity), `value:P`, `bound:P`. A human approves one via `convert_units(ledger, slot, to_unit, actor, rationale, at)`
(or `GovernedSessions.convert_units`), recorded as an immutable `CONVERT` ledger event (actor, timestamp, from/to unit, factor,
rationale). The factor is RECOMPUTED on every replay (forged factor/unit/actor/time => `LEDGER_TAMPERED`). Values scale exactly;
results that are not exact decimals (<=17 digits) are refused (`CONVERSION_NOT_EXACT`). Retracting the event unapproves it.

**Custom units:** only `unitdef:` facts (human-stated or human-confirmed) define them; the registry is rebuilt deterministically
from the ledger (order independent). Duplicates (`DUPLICATE_UNIT`), cycles (`CIRCULAR_DEFINITION`), currency or invalid
definitions (`INVALID_CUSTOM_UNIT`), unknown bases are blocking issues. No separate mutable registry exists.

**Solver gate:** `production_planning/unit_gate.py` (`verify_unit_evidence`, `compile_unit_gated`) recomputes the report from
scratch and binds it to the problem structure. `decision_intelligence.solve_run(..., units=, require_units=)` runs the gate
before the orchestrator; failure raises `PlanningInputError` and nothing is solved. `UnitReport.sha256` now binds the checked
inputs and custom-unit fingerprint.

**Evidence/decisions:** `FormulationRecord` gains `units_status`, `unit_report_sha256`, `conversions`, `custom_units`,
`unit_claims`. `ScenarioRun.units` carries `UnitEvidence`; `admit` re-verifies (`UNIT_EVIDENCE_INVALID` / `UNIT_EVIDENCE_MISSING`
when `DecisionConfig.require_unit_evidence`); scenarios must share the baseline's unit report (`UNIT_EVIDENCE_MISSING` /
`UNIT_EVIDENCE_MISMATCH`); these codes are untrusted rejections. Brief `EvidenceRef` has `unit_status`, `unit_report_sha256`;
brief limitations state the unit status. **Versioned change:** brief schema `qorvexa-decision-brief/1.1` (new fields change
brief hashes).

**Limitations:** units must be re-stated per term (no automatic inference); inexact conversions need the human to choose the
exact direction (usually converting the capacity); `GovernedSessions(require_units=False)` default keeps legacy flows.

---
## Production Planning admission gate (implemented)

Code: `production_planning/admission.py`, `governance/admission.py`; tests `backend/tests/test_admission_gate.py` (16).

**Policies (explicit, typed `AdmissionPolicy`):**
- `STRICT` - recomputed, valid unit evidence is mandatory. Default for `GovernedSessions` (new governed industrial workflows).
- `LEGACY_UNVERIFIED` - compatibility mode; must be selected explicitly (`GovernedSessions(policy=...)`, `solve_run(policy=...)`,
  `DecisionConfig(admission_policy=...)`). Deprecated `GovernedSessions(require_units=True/False)` maps to STRICT/LEGACY.
  `solve_run` also defaults to STRICT; every legacy execution needs an explicit `policy=LEGACY_UNVERIFIED` and produces a visibly
  labelled legacy admission record.

**`admit_for_solve(problem, formulation, policy=, units=, provenance=, expected_model_sha256=) -> AdmissionRecord`** (pure; raises
`AdmissionError`, a `PlanningInputError`, with a specific `.code`; the solver is never touched). It: checks the manifest input
hash; recompiles deterministically (`FORMULATION_TAMPERED`); recomputes the canonical model hash (`STALE_MODEL_HASH` against a
caller-held hash); recomputes the unit report from scratch, verifies its hash (which binds inputs and the custom-unit
fingerprint) and binds the evidence to the exact problem (`UNIT_EVIDENCE_*`, `UNIT_LINEAGE_MISMATCH`); rejects missing evidence
under STRICT, and also when the formulation claims unit lineage but ships none. Evidence is never upgraded or downgraded: status is
`ADMITTED_UNITS_VERIFIED` only when evidence was actually verified, else `ADMITTED_LEGACY_UNVERIFIED` with a warning.
`verify_admission(record, problem, formulation, units)` re-runs admission and requires an identical record.

**`admit_ledger_formulation(ledger, built, policy)`** (governed path) additionally: verifies the ledger chain, re-formulates from
the ledger and rejects a build that no longer matches (`STALE_FORMULATION`), requires every conversion in the record to be an
active, human-authored `CONVERT` event and every unit claim to be an active ledger claim, and stores that provenance in the record.

**`AdmissionRecord`:** status, policy, problem/spec/model hashes, unit status, `unit_report_sha256`, custom-registry fingerprint,
provenance (formulation record hash, ledger head, confirmed conversions, unit-claim lineage), legacy warning, `record_sha256`.

**Execution paths closed:** `decision_intelligence.solve_run` always admits before `orchestrator.solve_json` (and verifies a
supplied record); `GovernedSessions.run_decision` admits the baseline via the ledger gate and every scenario via
`admit_for_solve` before any solve. A failed gate raises before the solver is reached (proved with a spy).
**Deliberately open:** the generic `LPOrchestrator.solve_json` stays usable for any LP (generic Solver Fabric clients are not
governed). A production-planning result produced that way carries no admission record; Decision Intelligence labels it
`NOT_ADMITTED` and rejects it under STRICT (`ADMISSION_MISSING`).

**Decision Intelligence:** `ScenarioRun.admission`; `admit()` re-verifies the record (`ADMISSION_INVALID`, `ADMISSION_NOT_VERIFIED`,
`ADMISSION_MISSING`; untrusted rejections); `DecisionConfig.admission_policy` (STRICT rejects anything not verified; a legacy
admission adds the unresolved assumption `UNIT_VALIDATION_NOT_PERFORMED`, making the recommendation conditional until
acknowledged); brief evidence carries `admission_status` and `admission_sha256`; schema `qorvexa-decision-brief/1.2`.

### What Antigravity must do in the API integration
1. Create industrial production-planning sessions with the default (`STRICT`); never expose a legacy toggle without an explicit,
   clearly labelled user choice that maps to `AdmissionPolicy.LEGACY_UNVERIFIED`.
2. Handle `AdmissionError` (`.code`): `UNIT_EVIDENCE_MISSING`, `UNIT_EVIDENCE_MISMATCH`, `UNIT_VALIDATION_FAILED`,
   `STALE_FORMULATION`, `STALE_MODEL_HASH`, `FORMULATION_TAMPERED`, `UNCONFIRMED_CONVERSION`, `UNIT_CLAIM_LINEAGE`,
   `LEDGER_TAMPERED` -> 409/422 with `{code, message}`; tamper codes also audit-logged. No result is produced.
3. Show formulation issues `CONVERSION_REQUIRED` (exact factor, candidates `slot|to_unit|n/d|exact`) and unit errors, and call
   `convert_units` only on explicit user approval.
4. Show `admission_status`, `unit_report_sha256` and `admission_sha256` per scenario; display the legacy warning and
   `UNIT_VALIDATION_NOT_PERFORMED` prominently. Never present `ADMITTED_LEGACY_UNVERIFIED` or `NOT_ADMITTED` results as
   dimensionally validated.
5. Do not call `LPOrchestrator` directly for production-planning data; use `GovernedSessions`.

**Remaining limitations:** the generic orchestrator cannot be forced through the gate (by design); the strict gate relies on the
caller using the production-planning entry points (no application code outside tests calls them directly yet); units must be stated per term; inexact conversions are refused.
