# LP correctness audit and bound-conflict design

Scope: `verifier.py`, `orchestrator.py`, `encoding_a.py`; tests in `backend/tests/test_sf_lp_adversarial_matrix.py`.

## Audit result
Re-derived independently: dual bound D(y) = c0 + sum h(X_j, c - A^T y)_j + sum h(R_i, y_i); Farkas value D0 (c and c0 excluded); unboundedness recession conditions; exact primal feasibility; signed gap. No new defect found in the production code beyond the earlier fixes (signed gap guard, anchor recovery, exact-rechecked dual repair). The new matrix confirms:

* row-multiplier sign rules for `>=`, `<=`, equality and ranged rows; zero coefficients never produce `0 * inf`;
* weak duality `D(y) <= c.x0 + c0` for any y on 400 random feasible models, and no Farkas certificate and no interval conflict exists for them;
* every repaired dual is recomputed exactly and obeys the same bound;
* a ray alone, a status alone, touching (feasible) systems, wrong Farkas orientation, zero slope, and rays leaving finite bounds never yield a definitive result;
* exact certificates survive worker timeout/crash; numerical-only candidates stay `UNKNOWN`.

Mutation check (temporary, reverted): weakening the Farkas strictness, the improving-slope test or the upper-bound recession test each made the matrix fail.

## Contradictory variable bounds (`x >= 10`, `x <= 5`)
Today: intake rejects with `BOUND_CONTRADICTION_EXACT` -> job FAILED, no solver call. Kept (fail closed).

Why it cannot be INFEASIBLE now: `FarkasEvidence` is bound to `model_sha256`, but the canonical builder rejects `lower > upper`, so no canonical model (and no hash) exists. Hashing something else would be an invented binding.

Smallest sound change (needs a runtime-contract change, so it is out of this task):

* New evidence DTO `BoundConflictEvidence`:
  `input_sha256` (SHA-256 of the raw ingress bytes, which exist before intake), `variable_id`, `lower` and `upper` (exact decimal/rational strings of the ingested bounds), `conflict_value` (= lower - upper, must be > 0).
* New claim kind (or reuse `INFEASIBILITY` with level `EXACT_RATIONAL` and a distinct `exactness_scope`) and an optional `bound_conflict_evidence` field on `SolverRuntimeResult`; coupling rule: present iff outcome INFEASIBLE with that claim and no model-bound evidence.
* Verifier check: re-ingest the raw bytes, re-derive the variable's bounds exactly, confirm they equal the stated strings, `lower - upper > 0`, and `sha256(raw) == input_sha256`. Pure exact arithmetic, independent of any solver.
* Orchestrator: map `IntakeBoundContradictionError` to this result (job COMPLETED, INFEASIBLE) instead of FAILED.
* Migration: additive (new optional field and enum value); existing results and the canonical model are unchanged; frozen M1 contracts untouched. Requires lifting the freeze on `solver_runtime.py` only.

## Remaining limitations
* Multi-row infeasibility is certified only when the solver supplies a Farkas ray (or a single-row conflict exists).
* Repair/anchor recovery are heuristics; failures end `UNKNOWN`.
* Repair is capped at 40,000 cells.
