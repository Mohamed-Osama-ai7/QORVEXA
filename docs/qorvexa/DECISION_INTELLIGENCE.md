# Decision Intelligence (production planning slice)

Package: `backend/src/qorvexa/industrial/decision_intelligence/`. It turns verified Production Planning runs into an explainable, deterministic **decision brief**. It never solves, verifies, compiles or builds production reports itself; it consumes those components' outputs and re-checks their binding.

A mathematically valid result is not automatically the best business decision: the engine reports trade-offs, unresolved assumptions and always requires human approval.

## Modules
| Module | Responsibility |
|---|---|
| `models.py` | `ScenarioRun`, `DecisionConfig`, `Preference`, `HardConstraint`, `MetricObjective`, `Rejection` |
| `trust.py` | admission gate (`admit`): evidence and binding checks |
| `metrics.py` | metrics from a verified `ProductionReport` only |
| `diff.py` | business-input differences baseline vs scenario; unmodeled-assumption codes |
| `comparison.py` | compatibility, lineage, objective/product/resource/demand deltas |
| `tradeoffs.py` | deterministic trade-off records (AVAILABLE / UNAVAILABLE) |
| `ranking.py` | objective-first, constraint-first, weighted-preference rankings |
| `pareto.py` | dominance analysis |
| `recommendation.py` | recommendation policy and evidence coverage |
| `explanation.py`, `service.py` | brief assembly, `decide()`, `solve_run()` helper |

## Trusted input contract
A `ScenarioRun` = `(scenario_id, PlanningProblem, CompiledFormulation, SolverRuntimeResult | None, model_sha256)`; the last two come from the real `OrchestrationOutcome`. `admit()` rejects, with a stable code, anything that fails:
`NO_RESULT`, `PROBLEM_MANIFEST_MISMATCH` (manifest not from this problem), `FORMULATION_INVALID`, `FORMULATION_TAMPERED` (the spec JSON and manifest must equal a deterministic recompilation of the supplied problem, so a spec/manifest swap cannot pass), `OBJECTIVE_MISMATCH` (the runtime objective must agree with the objective recomputed from the verified primal), `MODEL_HASH_MISMATCH` (the engine **recomputes** the canonical model hash from the compiled spec through the public ingress -> intake -> Encoding A path, so wrong or reused results are caught), `MISSING_LOWERING_EVIDENCE`, `RESULT_FAILED`, `RESULT_UNKNOWN`, `VARIABLE_MAPPING_MISMATCH`, `UNVERIFIED_RESULT` (the existing `build_production_report` gate). Admitted states keep the existing meanings: `VERIFIED_NUMERICAL_SOLUTION`, `INFEASIBLE_VERIFIED`, `UNBOUNDED_VERIFIED`. `GAP_NUMERICAL` is always called numerical verification, never a proof of optimality. No shadow prices or dual sensitivities are inferred.

## Scenario lineage and comparability
A scenario is compared only if its objective mode, currency, period label, product ids/units and resource ids/units equal the baseline's and its manifest lineage contains `base_input_sha256:<baseline input hash>` and `scenario:<its id>`, with its own distinct model hash. Otherwise it is listed under `rejected_scenarios` with `INCOMPATIBLE_OBJECTIVE|CURRENCY|HORIZON|UNITS`, `INCOMPATIBLE_PROBLEM` (consumption coefficients or resource kinds differ, i.e. not a what-if of this baseline) or `LINEAGE_MISMATCH`. Lineage strings alone are never trusted: a forged lineage cannot admit a different problem. Duplicate ids raise `DecisionInputError`. Infeasible scenarios stay in the comparison as a status change without numbers.

## Comparison semantics
Exact `Decimal` (50 digits). `absolute = new - base`; `relative = (new - base)/|base|`, **undefined (None) when base = 0**. Effect (IMPROVED / DETERIORATED / UNCHANGED within 1e-6 relative) depends on the mode (higher is better for margin, lower for cost). Also per-product quantity changes, resource used/remaining/utilization changes, `BECAME_BINDING`/`NO_LONGER_BINDING` ("at capacity" on the verified primal point, not a shadow price), demand shortfall.

## Ranking
* **Objective-first**: by verified objective; standard competition ranks with tolerance ties (1,1,3).
* **Constraint-first**: requires explicit `HardConstraint`s (`metric`, `<=`/`>=`, threshold); violators (or metric unavailable) are rejected with reasons, the rest ranked by objective with slacks shown.
* **Weighted preference**: requires explicit `Preference(metric, direction, weight>0)`; nothing is defaulted. Per metric over eligible scenarios `norm = (v-min)/(max-min)` (MAXIMIZE) or `(max-v)/(max-min)` (MINIMIZE); zero-range metrics contribute 0 and are flagged; `score = sum(w*norm)/sum(w)`. Each entry keeps raw metrics, weights, normalized values and per-metric contributions. Hard constraints are applied first and cannot be outweighed. Missing metrics exclude the scenario (never zero). Statuses: `OK`, `MISSING_CONFIGURATION`, `INVALID_CONFIGURATION`, `NO_ELIGIBLE_SCENARIOS`.

Metric keys: `objective`, `total_volume` (single product unit only), `mean_utilization`, `max_utilization`, `bottleneck_count`, `total_shortfall`, `quantity:<P>`, `used:<R>`, `remaining:<R>`, `utilization:<R>`, `shortfall:<P>`.

## Pareto
User-chosen `MetricObjective`s with explicit directions (none configured -> `NOT_CONFIGURED`). A dominates B if it is at least as good everywhere (tolerance-aware) and strictly better somewhere. Equal vectors are both non-dominated and listed in `equal_groups`; scenarios lacking a metric are excluded. Each dominated scenario carries an explanation. Pareto-optimal is not "universally best".

## Trade-offs
Rules over verified numbers only (new bottlenecks, shrinking slack, utilization vs objective, cost vs volume, mix shift, observed objective change per capacity unit, raised commitments, lost feasibility). Each has improved/affected metric, magnitude, direction, evidence, assumptions, limitations. Opportunity cost, risk and market effects are listed as UNAVAILABLE/UNSUPPORTED.

## Recommendation policy
1. baseline not verified-solved or no solved candidate -> `NO_VERIFIED_RECOMMENDATION`
2. ranking not configured/invalid, or nobody meets hard requirements -> `REQUIRES_HUMAN_REVIEW`
3. several scenarios share rank 1 -> `NO_CLEAR_WINNER`
4. unique winner with adverse trade-offs, unresolved assumptions (`CAPACITY_CHANGE_COST_NOT_MODELED`, `DEMAND_CHANGE_ACCEPTANCE_NOT_MODELED`, `VALUE_CHANGE_BASIS_NOT_VALIDATED`; clearable only via `DecisionConfig.acknowledged_assumptions`) or untrusted scenarios left out -> `CONDITIONALLY_RECOMMENDED`
5. otherwise `RECOMMENDED`.
`human_approval_required` is always true. There is no probabilistic confidence: `EvidenceCoverage` counts submitted/admitted/rejected scenarios, the verification class and `exact_optimality_proof=false`.

## Example (real HiGHS, base product mix)
Baseline: max 40A+30B; machine 2A+B<=100; labor A+B<=80; A<=40. Scenarios: MACH120 (machine 120), MACH80 (machine 80), B90 (minimum 90 units of B).
```python
from qorvexa.industrial.decision_intelligence import (
    DecisionConfig, MetricDirection, MetricObjective, brief_json, decide, solve_run,
)
from qorvexa.industrial.production_planning import (
    CapacityChange, DemandBoundsChange, ScenarioSpec,
    compile_problem, compile_scenario, validate_planning_dict,
)
from qorvexa.solver_fabric import LPOrchestrator
from qorvexa.solver_fabric.highs_adapter import HighsAdapter

raw = {  # strict Production Planning input (exact decimals as strings)
    "metadata": {"problem_id": "PP-DEMO", "title": "Product mix"},
    "mode": "MAXIMIZE_MARGIN", "currency": "USD",
    "products": [
        {"id": "A", "name": "Product A", "unit": "pcs", "value_per_unit": "40",
         "value_currency": "USD", "min_quantity": "0", "max_quantity": "40"},
        {"id": "B", "name": "Product B", "unit": "pcs", "value_per_unit": "30",
         "value_currency": "USD", "min_quantity": "0"},
    ],
    "resources": [
        {"id": "LABOR", "name": "Labor", "kind": "LABOR", "unit": "h", "capacity": "80"},
        {"id": "MACHINE", "name": "Machine", "kind": "MACHINE", "unit": "h", "capacity": "100"},
    ],
    "consumptions": [
        {"resource_id": r, "product_id": p, "amount_per_unit": a,
         "resource_unit": "h", "product_unit": "pcs"}
        for r, p, a in (("MACHINE", "A", "2"), ("MACHINE", "B", "1"),
                        ("LABOR", "A", "1"), ("LABOR", "B", "1"))
    ],
}
orch = LPOrchestrator(HighsAdapter())            # real HiGHS subprocess
bp = validate_planning_dict(raw)
base = solve_run(orch, "BASE", bp, compile_problem(bp), policy=AdmissionPolicy.LEGACY_UNVERIFIED)  # explicit legacy opt-in

def scenario(sid, change):
    s = compile_scenario(bp, ScenarioSpec(scenario_id=sid, changes=(change,)))
    return solve_run(orch, sid, s.problem, s.formulation, policy=AdmissionPolicy.LEGACY_UNVERIFIED)  # own solve + model hash

runs = [scenario("MACH120", CapacityChange(resource_id="MACHINE", capacity="120")),
        scenario("MACH80", CapacityChange(resource_id="MACHINE", capacity="80")),
        scenario("B90", DemandBoundsChange(product_id="B", min_quantity="90"))]

config = DecisionConfig(                          # everything here is the user's explicit choice
    pareto_objectives=(
        MetricObjective(metric="objective", direction=MetricDirection.MAXIMIZE),
        MetricObjective(metric="total_volume", direction=MetricDirection.MAXIMIZE)),
    # acknowledged_assumptions=frozenset({"CAPACITY_CHANGE_COST_NOT_MODELED"}),
)
brief = decide(base, runs, config)
print(brief_json(brief))                          # deterministic JSON, content hash in brief_sha256
```
Input contracts: `ScenarioRun(scenario_id, PlanningProblem, CompiledFormulation, SolverRuntimeResult | None, model_sha256 | None)` (built by `solve_run` / `run_from_outcome` from a real `OrchestrationOutcome`); `DecisionConfig(ranking_mode, hard_constraints, preferences, pareto_objectives, acknowledged_assumptions)` with exact-decimal weights/thresholds (binary floats are refused).

Executive summary produced:
```
Decision objective: maximize contribution margin (USD).
Baseline objective: 2600 USD (numerically verified, GAP_NUMERICAL).
B90: INFEASIBLE_VERIFIED.
MACH120: objective 2800 (200, 7.69%) - improved.
MACH80: objective 2400 (-200, -7.69%) - deteriorated.
Recommendation: CONDITIONALLY_RECOMMENDED (scenario: MACH120).
Human approval is required before any production change.
```
Objective-first ranking: MACH120 (1), BASE (2), MACH80 (3); B90 not ranked. Pareto: MACH120 non-dominated; BASE and MACH80 dominated. Trade-off for MACH120: capacity +20 moved the objective +200 (average 10 per capacity unit between two solved scenarios, not a shadow price). Condition: the cost of obtaining the capacity is not modeled; with `acknowledged_assumptions={"CAPACITY_CHANGE_COST_NOT_MODELED"}` the status becomes `RECOMMENDED`. Binding resources of the winner: LABOR, MACHINE (at capacity on the verified primal point).

## Human approval flow
`recommendation.human_approval_required` is always `true` and `human_approval_reasons` states why (the engine supports a decision, it does not authorize or execute it; costs, demand acceptance and risks outside the LP are not represented). Suggested flow: the brief is shown to a decision maker; `conditions` and `unresolved_assumptions` are resolved outside the engine; only then may the user re-run `decide` with the matching `acknowledged_assumptions`, which turns a `CONDITIONALLY_RECOMMENDED` winner into `RECOMMENDED` (still subject to human sign-off). The `brief_sha256` identifies exactly what was approved.

## Determinism note
The brief is deterministic for the same trusted inputs (independent of scenario order). Re-running the real solver can change binary64 noise in the primal vector and therefore the content hash; the hash identifies the brief, not the problem.

## API / frontend recommendations
Expose `decide()` behind an endpoint returning `brief_json` (schema `qorvexa-decision-brief/1`, content hash `brief_sha256`); render `executive_summary`, show `recommendation.status` with `conditions` and `unresolved_assumptions`, keep raw metrics beside every rank, and require an explicit human approval action. Let an LLM only verbalize the brief; numbers must come from it.

## Unsupported / unavailable
Shadow prices and formal sensitivity (no verified duals in runtime results), probabilistic risk or confidence, Monte Carlo, market demand, cost of scenario changes, MILP/scheduling, cross-currency or cross-unit comparison, execution or control of any plant system. Integration gap: the runtime result does not carry the scenario id, so binding to a scenario relies on the recomputed model hash and lineage.


> **Admission (current):** `solve_run` defaults to `AdmissionPolicy.STRICT` (valid unit evidence required; see
> `INDUSTRIAL_UNITS.md`). Unit-less models need an explicit `policy=AdmissionPolicy.LEGACY_UNVERIFIED` (import from
> `qorvexa.industrial.production_planning.admission`); the examples above are legacy and say so. Runs that were not admitted
> (`NOT_ADMITTED`) or were admitted as legacy make the recommendation conditional (`UNIT_VALIDATION_NOT_PERFORMED`), and are never
> described as verified industrial evidence.
