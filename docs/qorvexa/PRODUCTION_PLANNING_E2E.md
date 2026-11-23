# Production planning - first working vertical slice (real HiGHS)

Status: continuous-LP production planning runs end to end with the real HiGHS subprocess and the independent exact verifier. This is a software vertical slice validated on synthetic data only.

## Flow
```
production data (JSON/dict)
 -> validate_planning_dict / parse_planning_json      (strict, exact decimals, explicit units)
 -> compile_problem                                    (deterministic GeneralizedProblemSpec JSON + manifest)
 -> LPOrchestrator(HighsAdapter()).solve_json(spec_json)
      strict ingress -> intake -> Encoding A -> canonical exact LP -> binary64 lowering
      -> independent lowering verification -> HiGHS worker subprocess (candidates only)
      -> independent exact verification -> SolverRuntimeResult
 -> build_production_report(manifest, outcome.result, expected_model_sha256=outcome.model_sha256)
 -> scenarios: compile_scenario(base, spec) -> same pipeline -> compare reports
```
Maximization is converted to minimization exactly once, by the canonical compiler; the runtime result reports the original-direction objective.

## Supported inputs
Products (min/max quantity, margin or cost), resources (capacity, kind, unit), per-unit consumption, one currency, objective mode `MAXIMIZE_MARGIN` or `MINIMIZE_COST`, continuous quantities only. Decimals are exact strings/ints/Decimal. Unsupported features (integer/binary, setup, lot size, sequencing, scheduling, nonlinear, unit conversion) are refused explicitly.

## Verification behaviour
* `SOLVED` is issued only at tier `GAP_NUMERICAL`: the primal vector is checked exactly (tolerance 1e-6 on violation), the objective is recomputed exactly, a weak-duality bound is recomputed exactly from the worker's multipliers (with exact-rechecked repair when needed) and the gap must be within 1e-6 absolute or relative. **This is numerical verification, not a formal exact optimality proof.**
* `INFEASIBLE` only with an exact Farkas certificate (the worker's ray, or the verifier's own single-row interval conflict), re-verified in rational arithmetic.
* `UNBOUNDED` only with an exactly feasible anchor and an exactly recession-feasible, strictly improving ray. Without both (for example HiGHS reports unbounded but returns no ray) the result is `UNKNOWN`.
* Solver status/termination is metadata. Timeouts, crashes, wrong dimensions, hash mismatches and non-finite values fail closed.

## Report
Recommendations require a `SOLVED`/`GAP_NUMERICAL` result bound to the expected model hash with a verified primal-feasibility claim and primal values for exactly the manifest variables. Utilization, remaining capacity and demand fulfilment are recomputed from the manifest and the verified primal point. No shadow prices or dual interpretation are produced ("bottleneck" = at capacity on the primal point). Results of another model hash are never reused.

## Scenarios
Capacity, demand-bound and margin/cost changes produce independent formulations with their own model hash and lineage (base input hash, scenario id, optional parent). A scenario stays `FORMULATED_NOT_SOLVED` until it is run through the orchestrator.

## Observed (real solver, this repository)
| Case | Result |
|---|---|
| Example A product mix | SOLVED/GAP_NUMERICAL, 2600 at (20, 60) |
| Example B multi-resource | SOLVED/GAP_NUMERICAL, 13 |
| Example C cost minimization | SOLVED/GAP_NUMERICAL, 35 at (10, 5) |
| Scenario: machine capacity 100 -> 120 | SOLVED, 2800 (+200), distinct model hash |
| Scenario: machine capacity 15 (min cost) | INFEASIBLE, exact Farkas certificate |
| Unbounded with exact anchor and solver ray | UNBOUNDED (exact evidence) |
| Unbounded, no ray from solver | UNKNOWN (fail closed) |

Notes: M1 ingress canonicalizes decimal inputs to binary64 (e.g. 0.1) before the exact model is built, so lowering itself rounds nothing for such inputs. Each real solve costs about 1-3 s because of the subprocess.

## Known limitations
Continuous LP only (no MILP, setups, scheduling); synthetic data only, no real-factory validation; no PLC/SCADA, Digital Twin or LLM integration; no verified duals/shadow prices in runtime results; free columns may still end `UNKNOWN` if the dual bound is -infinity and cannot be repaired; scenario-to-result binding is by model hash only.
