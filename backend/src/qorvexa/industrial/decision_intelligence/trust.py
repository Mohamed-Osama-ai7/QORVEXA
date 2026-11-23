"""Admission gate: only trusted, correctly bound, verified runtime outputs enter the engine."""

from __future__ import annotations

import hashlib

from qorvexa.contracts.solver_runtime import MathOutcome
from qorvexa.industrial.decision_intelligence.metrics import report_metrics
from qorvexa.industrial.decision_intelligence.models import Admitted, Rejection, ScenarioRun
from qorvexa.industrial.production_planning.admission import (
    AdmissionError,
    require_strict,
    verify_admission,
)
from qorvexa.industrial.production_planning.compiler import canonical_input_json, compile_problem
from qorvexa.industrial.production_planning.model import PlanningInputError
from qorvexa.industrial.production_planning.report import ReportStatus, build_production_report
from qorvexa.industrial.production_planning.unit_gate import verify_unit_evidence
from qorvexa.industrial.units.evidence import VALIDATED
from qorvexa.solver_fabric import build_model_from_derived, derive_problem_forms


def recompute_model_sha256(run: ScenarioRun) -> str:
    """Canonical model hash from the compiled spec via the existing public pipeline (no solver)."""
    model = build_model_from_derived(derive_problem_forms(run.formulation.to_spec()), None)
    return model.model_sha256


def admit(run: ScenarioRun, require_units: bool = False) -> Admitted | Rejection:
    def no(code: str, reason: str) -> Rejection:
        return Rejection(scenario_id=run.scenario_id, code=code, reason=reason)

    man = run.formulation.manifest
    result = run.result
    if result is None or run.model_sha256 is None:
        return no("NO_RESULT", "no runtime result or model hash was supplied")
    input_sha = hashlib.sha256(canonical_input_json(run.problem).encode("utf-8")).hexdigest()
    if man.input_sha256 != input_sha:
        return no("PROBLEM_MANIFEST_MISMATCH", "manifest does not belong to the supplied problem")
    try:
        again = compile_problem(run.problem, problem_id=man.problem_id, lineage=man.lineage)
    except Exception as exc:
        return no("FORMULATION_INVALID", f"problem could not be recompiled: {exc}")
    if again.spec_json != run.formulation.spec_json or again.manifest != man:
        return no(
            "FORMULATION_TAMPERED",
            "compiled spec/manifest differ from a deterministic recompilation of the problem",
        )
    try:
        expected = recompute_model_sha256(run)
    except Exception as exc:  # compiled formulation must re-run the strict pipeline
        return no("FORMULATION_INVALID", f"compiled formulation was rejected: {exc}")
    if run.model_sha256 != expected:
        return no(
            "MODEL_HASH_MISMATCH",
            "supplied model hash differs from the hash recomputed from this scenario's "
            "formulation (wrong or reused result)",
        )
    low = result.lowering_evidence
    if low is None:
        return no("MISSING_LOWERING_EVIDENCE", "result carries no lowering evidence")
    if low.model_sha256 != expected:
        return no("MODEL_HASH_MISMATCH", "lowering evidence is bound to a different model")
    if result.outcome == MathOutcome.FAILED:
        return no("RESULT_FAILED", "solver run failed")
    if result.outcome == MathOutcome.UNKNOWN:
        return no("RESULT_UNKNOWN", "result is UNKNOWN: nothing was mathematically established")
    ids = {v.variable_id for v in man.variables}
    if result.outcome == MathOutcome.SOLVED and (
        result.primal_solution is None or set(result.primal_solution) != ids
    ):
        return no("VARIABLE_MAPPING_MISMATCH", "primal variables differ from the manifest")
    report = build_production_report(man, result, expected_model_sha256=expected)
    if report.status == ReportStatus.NO_VERIFIED_RECOMMENDATION:
        return no("UNVERIFIED_RESULT", report.reason)
    if report.objective_matches_runtime is False:
        return no(
            "OBJECTIVE_MISMATCH",
            "runtime objective disagrees with the objective recomputed from the verified primal",
        )
    units_status, unit_sha = "LEGACY_UNITS_UNVERIFIED", ""
    if run.units is None:
        if require_units:
            return no("UNIT_EVIDENCE_MISSING", "unit evidence is required but absent")
    else:
        try:
            unit_sha = verify_unit_evidence(run.units, run.problem).sha256
            units_status = VALIDATED
        except PlanningInputError as exc:
            return no("UNIT_EVIDENCE_INVALID", f"{exc.code}: {exc.message}")
    adm_status, adm_sha = "NOT_ADMITTED", ""
    adm = run.admission
    if adm is None:
        if require_units:
            return no("ADMISSION_MISSING", "STRICT requires an admission record")
    else:
        try:
            verify_admission(adm, run.problem, run.formulation, run.units)
            if require_units:
                require_strict(adm)
        except AdmissionError as exc:
            code = "ADMISSION_INVALID" if exc.code != "ADMISSION_NOT_VERIFIED" else exc.code
            return no(code, f"{exc.code}: {exc.message}")
        if adm.model_sha256 != expected:
            return no("ADMISSION_INVALID", "admission record belongs to a different model")
        adm_status, adm_sha = adm.status.value, adm.record_sha256
    return Admitted(
        run,
        report,
        report_metrics(report),
        expected,
        input_sha,
        units_status,
        unit_sha,
        adm_status,
        adm_sha,
    )
