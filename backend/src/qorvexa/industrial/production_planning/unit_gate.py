"""Pre-solver unit gate: a model with failed, forged or mismatched unit evidence is never compiled.

Additive to the existing compile path: ``compile_problem`` (legacy, no unit metadata) is
unchanged and its results are never labelled dimensionally validated.
"""

from __future__ import annotations

from qorvexa.industrial.production_planning.compiler import CompiledFormulation, compile_problem
from qorvexa.industrial.production_planning.model import PlanningInputError, PlanningProblem
from qorvexa.industrial.units import Compat, UnitError, UnitReport, classify, parse_unit
from qorvexa.industrial.units.evidence import UnitEvidence, registry_of, verify_evidence


def _bad(code: str, message: str) -> PlanningInputError:
    return PlanningInputError(code, message)


def verify_unit_evidence(ev: UnitEvidence, problem: PlanningProblem) -> UnitReport:
    """Recompute the report from scratch and bind the evidence to THIS problem's structure."""
    try:
        report = verify_evidence(ev)
        reg = registry_of(ev)
        inp = ev.inputs
        if inp.currency != problem.currency:
            raise _bad("UNIT_EVIDENCE_MISMATCH", "currency differs from the problem")
        pu = {p.id: p.unit for p in problem.products}
        if {p.id: p.unit for p in inp.products} != pu:
            raise _bad("UNIT_EVIDENCE_MISMATCH", "product ids/units differ from the problem")
        ru = {r.id: r.unit for r in problem.resources}
        if {r.id: r.capacity_unit for r in inp.resources} != ru:
            raise _bad("UNIT_EVIDENCE_MISMATCH", "resource ids/units differ from the problem")
        have = {(c.resource_id, c.product_id): c.coefficient_unit for c in inp.consumptions}
        want = {(c.resource_id, c.product_id) for c in problem.consumptions}
        if set(have) != want:
            raise _bad("UNIT_EVIDENCE_MISMATCH", "consumption pairs differ from the problem")
        for (r, p), cu in have.items():
            if classify(parse_unit(cu, reg), parse_unit(f"({ru[r]})/({pu[p]})", reg)) is not (
                Compat.SAME_UNIT
            ):
                raise _bad("UNIT_EVIDENCE_MISMATCH", f"coefficient unit of {r}/{p} differs")
    except UnitError as exc:
        raise _bad(exc.code, exc.message) from exc
    return report


def compile_unit_gated(
    problem: PlanningProblem,
    ev: UnitEvidence,
    *,
    problem_id: str | None = None,
    lineage: tuple[str, ...] = (),
) -> CompiledFormulation:
    """Compile only after the unit evidence is recomputed, valid and bound to the problem."""
    report = verify_unit_evidence(ev, problem)
    return compile_problem(
        problem, problem_id=problem_id, lineage=(*lineage, f"unit_report:{report.sha256}")
    )
