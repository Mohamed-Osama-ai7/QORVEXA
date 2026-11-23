# Production planning (continuous LP) - formulation slice

Package: `backend/src/qorvexa/industrial/production_planning/` (`model`, `compiler`, `scenario`, `report`).
Status: **formulation/domain only.** Nothing here solves. It is not an end-to-end product until the real HiGHS worker (Phase B) is connected.

## Formulation
`x[p]` = quantity of product p (continuous), `min[p] <= x[p] <= max[p]`, `sum_p use[r,p] x[p] <= cap[r]`.
* `MAXIMIZE_MARGIN`: maximize `sum margin[p] x[p]` (any sign of margin).
* `MINIMIZE_COST`: minimize `sum cost[p] x[p]`; costs must be >= 0 and at least one product needs a positive minimum commitment.

Demand commitments/caps are variable bounds (traced in the manifest), capacities are `<=` rows (category `capacity`). Output is the existing strict-ingress `GeneralizedProblemSpec` JSON with typed `ExprNode` ASTs (`nary_op sum` of `mul(literal, variable_ref)`); `expressionSummary` is inert text. The compiler re-parses its own output through `parse_problem_spec_json`, and tests push it through intake, Encoding A, lowering and the orchestrator (up to a stub worker).

IDs are deterministic and independent of input order: variables `VAR-0001..` (products sorted by id), rows `CAP-0001..` (resources sorted by id), objective `OBJ-0001`.

## Strict input
Exact `Decimal` only (floats/bools refused, <= 17 digits, |v| <= 1e12), explicit units that must match across product/resource/consumption (never converted), single currency, unique ids, valid references, `min <= max`, size limits (200 products, 200 resources, 4000 consumptions, 1 MB). Integer/binary, setup, lot size, sequencing, scheduling, nonlinear and unit-conversion keys are refused with `UNSUPPORTED_FEATURE`; unknown fields with `UNSUPPORTED_OR_UNKNOWN_FIELD`. Duplicate JSON keys and NaN/Infinity are refused.

Note: the existing M1 pipeline canonicalizes decimals to binary64 (e.g. 0.1). The manifest keeps the original Decimal business values.

## Traceability
`FormulationManifest`: variable -> product/unit/bounds+source, constraint -> resource/capacity/terms (`consumption:R:P`), objective terms (`product:P.value_per_unit`), input and spec SHA-256, lineage.

## Scenarios
`ScenarioSpec` changes capacities, demand bounds (`clear_max` removes a cap) or margins/costs. Each scenario re-validates the whole problem, leaves the base untouched, compiles independently and deterministically, records lineage (base input hash, scenario id, optional parent) and has status `FORMULATED_NOT_SOLVED`.

## Report (`build_production_report`)
Consumes a trusted `SolverRuntimeResult` and requires the caller's expected canonical `model_sha256`. A recommendation is issued only for `SOLVED` + `GAP_NUMERICAL` with bound lowering evidence, a verified primal-feasibility claim and primal values for exactly the manifest variables; it states that this is not an exact optimality proof. Utilization, remaining capacity, demand fulfilment and "at capacity" are recomputed from manifest coefficients and the verified primal point. Infeasible/unbounded reports need bound exact evidence. Otherwise `NO_VERIFIED_RECOMMENDATION`.

## Phase B connection
`compiled.spec_json` -> `LPOrchestrator(adapter).solve_json(...)` -> `OrchestrationOutcome` (`model_sha256`, `result`) -> `build_production_report(manifest, outcome.result, expected_model_sha256=outcome.model_sha256)`.
Not yet available in runtime results (documented, not invented): verified duals/shadow prices, so bottlenecks are "at capacity on the primal point" only; a scenario-to-result binding beyond the model hash.
