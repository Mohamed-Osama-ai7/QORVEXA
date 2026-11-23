# Optimization–Simulation Co-Orchestration (OSCO)

Package `backend/src/qorvexa/industrial/co_orchestration/` · tests `backend/tests/test_optimization_simulation_orchestration.py` (24).

`Production Planning (STRICT) → Solver Fabric (real HiGHS) → independent verification → Digital Twin → operational evaluation →
justified refinement → Decision Intelligence Pareto/ranking → decision package (awaiting human approval)`

A mathematically optimal plan is not automatically the best operational decision. OSCO solves, verifies, simulates, finds where the
verified plan breaks operationally, proposes only evidence-justified adjustments, re-optimizes, and compares the candidates.

## Modules (all reuse public contracts; nothing re-implemented)
| Module | Role | Reused contracts |
|---|---|---|
| `contracts.py` | versioned config (`qorvexa-osco/1`), candidate specs, statuses, evidence, decision package | DI `MetricObjective`, `HardConstraint`, `Preference`, `RankingMode`; PP `ScenarioSpec`; twin `Change`, `QuantityPolicy` |
| `optimize.py` | unit evidence → `compile_unit_gated` → `solve_run(policy=STRICT)` → `trust.admit` | Units Engine, Production Planning, Decision Intelligence service/trust, Solver Fabric `LPOrchestrator` |
| `evaluate.py` | verified plan → `import_plan` → twin scenario → `simulate` → metrics, violations; planning-vs-twin contradiction check; calendar productive time | Digital Twin public API |
| `refine.py` | bottleneck-driven capacity refinement with a numeric justification | PP `CapacityChange`, Units Engine |
| `decision.py` | eligibility filter, DI `pareto` + `rank_scenarios` on combined metrics, recommendation, trade-offs, uncertainty | Decision Intelligence |
| `engine.py` | `CoOrchestrator.run`: bounded iterations, dedupe, budgets, cancellation, bounded parallel simulation, idempotency | — |
| `store.py` | `InMemoryExperimentStore`, `SqliteExperimentStore` (own table `osco_experiments`), hash verification | — |

## The loop (every step is recorded in `DecisionPackage.iterations`)
1. Candidates of iteration 1: the baseline, the explicit portfolio, explicit sensitivity capacities (`SensitivitySpec`).
2. Each candidate = Production Planning scenario changes + Digital Twin changes, identified by `cand-<sha(problem, twin)>`;
   an identical candidate is recorded as `DUPLICATE` with `duplicate_of`, never re-solved.
3. STRICT admission and a real solve; `trust.admit` independently re-verifies. Infeasible/unbounded/unverified candidates are
   never simulated (a simulation cannot override mathematics).
4. Verified quantities become twin orders only through the caller's `QuantityPolicy` (fractional LP output with
   `EXACT_INTEGER_ONLY` → `PLAN_NOT_IMPORTABLE`; the exact solver quantity and the difference are kept).
5. Simulation (deterministic or seeded stochastic). Timeout, cancellation and event limits → `SIMULATION_INCOMPLETE`, no evidence.
   Deadlock and unfinished plans are operational findings.
6. Operational requirements (all opt-in): complete run, plan achieved, max late orders, max makespan, max utilization, max late
   probability (stochastic only).
7. Refinement (only if `refine_on_bottleneck`): when a violating candidate's top simulated bottleneck is bound to a time-measured
   resource whose LP capacity exceeds the scheduled productive time of its bound machines in `[0, due]` (shifts − breaks − planned
   downtime − explicit breakdowns, overlaps counted once), re-optimize with that capacity. Capacity is never raised, never rounded.
8. Stop: `CONVERGED` (no new justified, non-duplicate candidate), `MAX_ITERATIONS`, `MAX_CANDIDATES`, `WALL_CLOCK`, `CANCELLED`,
   `BASELINE_FAILED` (baseline admission/verification failed).

## Decision rules
* Eligible = verified optimization **and** complete simulation **and** no violation. Others stay listed with reasons.
* Pareto and ranking are Decision Intelligence functions over `objective` (verified) plus `sim_*` metrics. There are no default
  weights: `ranking_mode` is mandatory; `WEIGHTED_PREFERENCE` without preferences → no recommendation; no Pareto objectives →
  `NOT_CONFIGURED`.
* Planning/twin contradictions (`RATE_MISMATCH`, `PRODUCT_MISSING_IN_TWIN`, …) are preserved and block the recommendation
  (`BLOCKED_BY_CONTRADICTIONS`). Ties → `TIE_REQUIRES_HUMAN_CHOICE`. A cancelled experiment never yields a recommendation.
* `Recommendation.human_approval_required` is always true and `approved` is always false; nothing is confirmed in the ledger.

## Hand-checked journey (synthetic; expected values computed independently)
Two products on a cutter (07:00–16:00) and a welder (08:00–16:00), both with a 12:00–12:30 break; margins A 10, B 6 EGP/piece;
welding 0.1 / 0.05 h per piece, cutting 0.05 / 0.025 h; 8 h of each; demand caps A ≤ 100, B ≤ 200; due 16:00; max 0 late orders.

| Step | Independent expectation | Engine result |
|---|---|---|
| LP optimum (vertex enumeration with exact fractions) | A=0, B=160, 960 EGP | HiGHS, STRICT, verified: A=0, B=160, 960 |
| Simulate optimum | welder works 7.5 h until 16:00 → 150 pieces | 150 completed; PLAN_NOT_ACHIEVED, LATE_ORDERS, INCOMPLETE_WITHIN_HORIZON; bottleneck WELD |
| Refinement evidence | productive welding time in [0, 16:00] = 27 000 s = 7.5 h | `CapacityChange(WELD_H, 7.5)` with that justification |
| Re-optimize | A=0, B=150, 900 EGP | verified 900 |
| Simulate refined | 150 × 180 s from 08:00 + 30 min break → 16:00 exactly | makespan 57 600 s, 0 late, feasible |
| Recommendation | lower objective, operationally superior | refined plan, trade-off "+60 EGP" for the baseline shown, approval required |

Further independently checked cases: a 2 h welder breakdown 11:00–13:00 overlapping the break leaves 6 h → B=120 → finishes 16:00;
two welders + cutter downtime 07:00–10:00 moves the bottleneck to the cutter, makespan 14:33 = cutter 10:00–12:00 (80 pieces),
12:30 + 80 × 90 s, + 180 s weld; LP with 15 welding hours → A=50, B=200, 1 700 EGP and the welders stay saturated (7.5 h each).

## Integration contracts (for Antigravity)
* Construct `CoOrchestrator(LPOrchestrator(HighsAdapter()), SqliteExperimentStore(path))`; never pass a mock solver.
* Inputs: an admitted-workflow `PlanningProblem`, a twin `FactoryModel` (e.g. from `build_from_ontology`) with orders left empty,
  and an `ExperimentConfig` whose bindings, conversion policy, requirements, ranking mode and budget are chosen by the user.
* Show per candidate: status, reasons, math evidence (verified flag, admission), simulation evidence (kind, seed, replications,
  CIs), violations, contradictions, lineage (`parent_id`, `justification`). Show `stop_reason`, `limitations`, `statement`.
* Use `verify_package` after loading; identical inputs return the stored package without re-solving (idempotent).
* Approval stays a separate human action in the governed workflow; OSCO never writes to the ledger.

## Limitations (genuine)
* LP only (continuous production quantities); no MILP, scheduling, sequencing or nonlinear optimisation.
* Automatic refinement is limited to calendar-justified capacity reductions on time-measured resources; other adjustments must be
  supplied explicitly as portfolio candidates.
* The planning/twin rate check compares deterministic per-piece times only; stochastic twin times are reported as not comparable.
* Utilization-based bottleneck candidates inherit the twin's dispatch order (machine id) for parallel machines.
* Solves run sequentially; simulations run on at most 4 threads (pure Python, so parallel speed-up is limited).
* Synthetic data only; results are not real-plant validation.
