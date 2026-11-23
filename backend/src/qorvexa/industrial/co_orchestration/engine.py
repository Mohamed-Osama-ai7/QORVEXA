"""The bounded optimize -> verify -> simulate -> evaluate -> refine loop.

Every iteration is recorded; candidates are content-addressed and deduplicated; budgets
(iterations, candidates, wall clock), cancellation and partial failures are explicit. Solves run
sequentially through the caller's real ``LPOrchestrator``; simulations may run on a bounded thread
pool and are collected in deterministic order. Re-running an experiment already in the store
returns the stored, hash-verified package without recomputation.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime

from qorvexa.industrial.co_orchestration.contracts import (
    Candidate,
    CandidateOrigin,
    CandidateSpec,
    CandidateStatus,
    DecisionPackage,
    ExperimentConfig,
    IterationRecord,
    Recommendation,
    RecommendationStatus,
    StopReason,
)
from qorvexa.industrial.co_orchestration.decision import decide
from qorvexa.industrial.co_orchestration.evaluate import contradictions, evaluate, twin_for
from qorvexa.industrial.co_orchestration.optimize import (
    derive_problem,
    problem_sha256,
    solve_and_verify,
)
from qorvexa.industrial.co_orchestration.refine import refinements
from qorvexa.industrial.co_orchestration.store import ExperimentStore, package_sha256
from qorvexa.industrial.decision_intelligence.models import Admitted
from qorvexa.industrial.digital_twin import FactoryModel, ScenarioError, model_sha256
from qorvexa.industrial.digital_twin.kernel import CancelToken
from qorvexa.industrial.production_planning import PlanningInputError, PlanningProblem
from qorvexa.industrial.production_planning.scenario import CapacityChange, ScenarioSpec
from qorvexa.solver_fabric import LPOrchestrator

_BASELINE_FATAL = {
    CandidateStatus.ADMISSION_REJECTED,
    CandidateStatus.NOT_VERIFIED,
    CandidateStatus.SCENARIO_INVALID,
}


def _sha(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def experiment_id(problem: PlanningProblem, twin: FactoryModel, cfg: ExperimentConfig) -> str:
    return (
        "exp-"
        + _sha([problem_sha256(problem), model_sha256(twin), cfg.model_dump(mode="json")])[:24]
    )


def sensitivity_specs(cfg: ExperimentConfig) -> list[CandidateSpec]:
    out = []
    for s in cfg.sensitivity:
        for cap in s.capacities:
            out.append(
                CandidateSpec(
                    label=f"sens:{s.resource_id}={cap}",
                    origin=CandidateOrigin.SENSITIVITY,
                    planning=ScenarioSpec(
                        scenario_id=f"sens-{s.resource_id}-{cap}".replace(".", "_")[:60],
                        changes=(CapacityChange(resource_id=s.resource_id, capacity=cap),),
                    ),
                    twin_changes=s.twin_changes,
                )
            )
    return out


@dataclass
class _Pending:
    cand: Candidate
    problem: PlanningProblem
    twin: FactoryModel
    admitted: Admitted


class CoOrchestrator:
    def __init__(
        self,
        solver: LPOrchestrator,
        store: ExperimentStore | None = None,
        *,
        clock: Callable[[], str] = _now,
    ) -> None:
        self.solver, self.store, self.clock = solver, store, clock

    def run(
        self,
        base_problem: PlanningProblem,
        twin: FactoryModel,
        cfg: ExperimentConfig,
        *,
        cancel: CancelToken | None = None,
    ) -> DecisionPackage:
        exp_id = experiment_id(base_problem, twin, cfg)
        if self.store is not None:
            hit = self.store.get(exp_id)
            if hit is not None:
                return hit  # idempotent: same inputs, verified stored result, no recomputation
        started = time.monotonic()
        budget = cfg.budget
        twin_sha = model_sha256(twin)
        candidates: list[Candidate] = []
        problems: dict[str, tuple[PlanningProblem, FactoryModel]] = {}
        seen: dict[str, str] = {}  # content key -> candidate id
        iterations: list[IterationRecord] = []
        queue: list[CandidateSpec] = [
            CandidateSpec(label="baseline", origin=CandidateOrigin.BASELINE),
            *cfg.portfolio,
            *sensitivity_specs(cfg),
        ]
        stop: StopReason | None = None
        baseline_id: str | None = None
        for it in range(1, budget.max_iterations + 1):
            this_iter: list[str] = []
            pending: list[_Pending] = []
            for spec in queue:
                if cancel is not None and cancel.is_set():
                    stop = StopReason.CANCELLED
                    break
                if time.monotonic() - started > budget.wall_clock_s:
                    stop = StopReason.WALL_CLOCK
                    break
                if (
                    sum(1 for c in candidates if c.status is not CandidateStatus.DUPLICATE)
                    >= budget.max_candidates
                ):
                    cid = "cand-skip-" + _sha(spec.model_dump(mode="json"))[:12]
                    candidates.append(
                        Candidate(
                            candidate_id=cid,
                            iteration=it,
                            spec=spec,
                            problem_sha256=None,
                            twin_model_sha256=None,
                            status=CandidateStatus.SKIPPED_BUDGET,
                            reasons=("candidate budget exhausted",),
                        )
                    )
                    this_iter.append(cid)
                    stop = StopReason.MAX_CANDIDATES
                    continue
                cand, extra = self._prepare(spec, it, base_problem, twin, seen)
                candidates.append(cand)
                this_iter.append(cand.candidate_id)
                if spec.origin is CandidateOrigin.BASELINE:
                    baseline_id = cand.candidate_id
                if extra is None:
                    continue
                problem, ctwin = extra
                problems[cand.candidate_id] = (problem, ctwin)
                out = solve_and_verify(self.solver, cand.candidate_id, problem)
                cand = cand.model_copy(
                    update={
                        "math": out.evidence,
                        "contradictions": contradictions(problem, ctwin, cfg.bindings),
                    }
                )
                if out.status is not None or out.admitted is None:
                    cand = cand.model_copy(
                        update={
                            "status": out.status or CandidateStatus.NOT_VERIFIED,
                            "reasons": out.reasons,
                        }
                    )
                    candidates[-1] = cand
                    continue
                candidates[-1] = cand
                pending.append(_Pending(cand, problem, ctwin, out.admitted))
            # simulate verified plans (bounded parallelism, deterministic collection order)
            if pending and stop not in (StopReason.CANCELLED, StopReason.WALL_CLOCK):

                def job(p: _Pending):  # type: ignore[no-untyped-def]
                    return evaluate(
                        p.admitted, p.problem, p.twin, cfg, p.cand.spec.label, cancel, self.clock
                    )

                if budget.max_workers > 1 and len(pending) > 1:
                    with ThreadPoolExecutor(max_workers=budget.max_workers) as pool:
                        results = list(pool.map(job, pending))
                else:
                    results = [job(p) for p in pending]
                for p, (status, reasons, sim, vio) in zip(pending, results, strict=True):
                    idx = next(
                        i for i, c in enumerate(candidates) if c.candidate_id == p.cand.candidate_id
                    )
                    candidates[idx] = candidates[idx].model_copy(
                        update={"status": status, "reasons": reasons, "sim": sim, "violations": vio}
                    )
            elif pending:
                for p in pending:
                    idx = next(
                        i for i, c in enumerate(candidates) if c.candidate_id == p.cand.candidate_id
                    )
                    candidates[idx] = candidates[idx].model_copy(
                        update={
                            "status": CandidateStatus.SIMULATION_INCOMPLETE,
                            "reasons": (f"not simulated: {stop.value if stop else ''}",),
                        }
                    )
            base = next((c for c in candidates if c.candidate_id == baseline_id), None)
            if it == 1 and base is not None and base.status in _BASELINE_FATAL:
                stop = StopReason.BASELINE_FAILED
            new_specs: list[CandidateSpec] = []
            if stop is None and cfg.refine_on_bottleneck:
                for cid in this_iter:
                    c = next(x for x in candidates if x.candidate_id == cid)
                    if c.candidate_id in problems:
                        pr, tw = problems[c.candidate_id]
                        new_specs += refinements(c, pr, tw, cfg)
            iterations.append(
                IterationRecord(
                    iteration=it,
                    candidate_ids=tuple(this_iter),
                    new_specs=tuple(s.label for s in new_specs),
                    note=stop.value
                    if stop
                    else ("refinement proposed" if new_specs else "no justified refinement"),
                )
            )
            if stop is not None:
                break
            # drop refinements that reproduce an already evaluated candidate
            fresh = []
            for s in new_specs:
                try:
                    key = self._key(
                        derive_problem(base_problem, s.planning),
                        twin_for(twin, s.twin_changes, s.label),
                    )
                except (PlanningInputError, ScenarioError, ValueError):
                    fresh.append(s)
                    continue
                if key not in seen:
                    fresh.append(s)
            if not fresh:
                stop = StopReason.CONVERGED
                break
            queue = fresh
        else:
            stop = StopReason.MAX_ITERATIONS
        assert stop is not None  # noqa: S101
        mode = base_problem.mode
        if stop is StopReason.BASELINE_FAILED:
            par, ranking = None, None
            rec = Recommendation(
                status=RecommendationStatus.BASELINE_NOT_VERIFIED,
                candidate_id=None,
                rationale=tuple(
                    next(c for c in candidates if c.candidate_id == baseline_id).reasons
                ),
                trade_offs=(),
                uncertainty=(),
            )
        else:
            par, ranking, rec = decide(candidates, cfg.decision, mode, baseline_id)
            if (
                stop is StopReason.CANCELLED
                and rec.status is RecommendationStatus.RECOMMENDED_FOR_HUMAN_REVIEW
            ):
                rec = rec.model_copy(
                    update={
                        "status": RecommendationStatus.INCOMPLETE,
                        "rationale": (
                            *rec.rationale,
                            "experiment was cancelled: not every candidate was evaluated",
                        ),
                    }
                )
        limits = [
            "LP formulation only (continuous quantities); "
            "no MILP, scheduling or sequencing optimisation.",
            "Simulation results are model outputs, not factory measurements.",
            "Adjustments are limited to calendar-justified capacity reductions; "
            "no other automatic change is made.",
        ]
        if any(c.contradictions for c in candidates):
            limits.append(
                "Planning and twin models disagree for some candidates; "
                "contradictions are listed, not resolved."
            )
        if stop in (
            StopReason.MAX_ITERATIONS,
            StopReason.MAX_CANDIDATES,
            StopReason.WALL_CLOCK,
            StopReason.CANCELLED,
        ):
            limits.append(f"Stopped by {stop.value}: further candidates may exist.")
        pkg = DecisionPackage(
            experiment_id=exp_id,
            experiment_label=cfg.experiment_label,
            config_sha256=_sha(cfg.model_dump(mode="json")),
            baseline_problem_sha256=problem_sha256(base_problem),
            twin_baseline_sha256=twin_sha,
            baseline_candidate_id=baseline_id,
            candidates=tuple(candidates),
            iterations=tuple(iterations),
            stop_reason=stop,
            pareto=par,
            ranking=ranking,
            recommendation=rec,
            limitations=tuple(limits),
            created_at=self.clock(),
        )
        pkg = pkg.model_copy(update={"package_sha256": package_sha256(pkg)})
        if self.store is not None and stop is not StopReason.CANCELLED:
            self.store.save(pkg)
        return pkg

    @staticmethod
    def _key(problem: PlanningProblem, twin: FactoryModel) -> str:
        return _sha([problem_sha256(problem), model_sha256(twin)])

    def _prepare(
        self,
        spec: CandidateSpec,
        it: int,
        base: PlanningProblem,
        twin: FactoryModel,
        seen: dict[str, str],
    ) -> tuple[Candidate, tuple[PlanningProblem, FactoryModel] | None]:
        try:
            problem = derive_problem(base, spec.planning)
        except (PlanningInputError, ValueError) as exc:
            cid = "cand-invalid-" + _sha(spec.model_dump(mode="json"))[:12]
            return Candidate(
                candidate_id=cid,
                iteration=it,
                spec=spec,
                problem_sha256=None,
                twin_model_sha256=None,
                status=CandidateStatus.SCENARIO_INVALID,
                reasons=(str(exc),),
            ), None
        try:
            ctwin = twin_for(twin, spec.twin_changes, spec.label)
        except (ScenarioError, ValueError) as exc:
            cid = "cand-invalid-" + _sha(spec.model_dump(mode="json"))[:12]
            return Candidate(
                candidate_id=cid,
                iteration=it,
                spec=spec,
                problem_sha256=problem_sha256(problem),
                twin_model_sha256=None,
                status=CandidateStatus.SCENARIO_INVALID,
                reasons=(str(exc),),
            ), None
        key = self._key(problem, ctwin)
        cid = "cand-" + key[:16]
        if key in seen:
            return Candidate(
                candidate_id=f"{cid}-dup{it}-{_sha(spec.label)[:6]}",
                iteration=it,
                spec=spec,
                problem_sha256=problem_sha256(problem),
                twin_model_sha256=model_sha256(ctwin),
                status=CandidateStatus.DUPLICATE,
                duplicate_of=seen[key],
                reasons=(f"identical problem and twin model as {seen[key]}",),
            ), None
        seen[key] = cid
        return Candidate(
            candidate_id=cid,
            iteration=it,
            spec=spec,
            problem_sha256=problem_sha256(problem),
            twin_model_sha256=model_sha256(ctwin),
            status=CandidateStatus.NOT_VERIFIED,
        ), (problem, ctwin)
