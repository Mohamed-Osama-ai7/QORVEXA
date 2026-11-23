"""Fail-closed admission gate for production-planning execution.

Nothing production-planning-related reaches a solver unless ``admit_for_solve`` succeeds for the
exact compiled problem. Policies are explicit:

* ``STRICT``: recomputed, valid unit evidence is mandatory.
* ``LEGACY_UNVERIFIED``: compatibility mode; the result is permanently labelled unverified.

Evidence is never upgraded: a record says VERIFIED only if unit evidence was actually recomputed
and bound to this problem. The generic Solver Fabric stays available for non-industrial LPs; this
gate governs production-planning paths (``solve_run``, governed sessions).
"""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from qorvexa.industrial.production_planning.compiler import (
    CompiledFormulation,
    canonical_input_json,
    compile_problem,
)
from qorvexa.industrial.production_planning.model import PlanningInputError, PlanningProblem
from qorvexa.industrial.production_planning.unit_gate import verify_unit_evidence
from qorvexa.industrial.units.evidence import UnitEvidence, registry_of
from qorvexa.solver_fabric import build_model_from_derived, derive_problem_forms

LEGACY_WARNING = (
    "LEGACY_UNVERIFIED: no unit evidence was validated; dimensional consistency of this model "
    "is NOT established."
)


class AdmissionPolicy(StrEnum):
    STRICT = "STRICT"
    LEGACY_UNVERIFIED = "LEGACY_UNVERIFIED"


class AdmissionStatus(StrEnum):
    ADMITTED_UNITS_VERIFIED = "ADMITTED_UNITS_VERIFIED"
    ADMITTED_LEGACY_UNVERIFIED = "ADMITTED_LEGACY_UNVERIFIED"


class AdmissionError(PlanningInputError):
    """Admission refused; ``code`` is the specific stable reason. The solver was NOT invoked."""


class AdmissionProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    formulation_record_sha256: str = ""
    ledger_head: str = ""
    # (slot, from_unit, to_unit, exact factor, actor, rationale, timestamp)
    conversions: tuple[tuple[str, str, str, str, str, str, str], ...] = ()
    unit_claims: tuple[tuple[str, int, str], ...] = ()  # (key, claim seq, basis)


class AdmissionRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: AdmissionStatus
    policy: AdmissionPolicy
    problem_input_sha256: str
    spec_sha256: str
    model_sha256: str
    unit_status: str
    unit_report_sha256: str
    custom_registry_fingerprint: str
    provenance: AdmissionProvenance
    warning: str
    record_sha256: str = ""

    @property
    def units_verified(self) -> bool:
        return self.status is AdmissionStatus.ADMITTED_UNITS_VERIFIED


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_model_sha256(formulation: CompiledFormulation) -> str:
    """Canonical model hash via the existing public pipeline (no solver)."""
    model = build_model_from_derived(derive_problem_forms(formulation.to_spec()), None)
    return model.model_sha256


def _seal(rec: AdmissionRecord) -> AdmissionRecord:
    return rec.model_copy(
        update={"record_sha256": _sha(rec.model_dump_json(exclude={"record_sha256"}))}
    )


def admit_for_solve(
    problem: PlanningProblem,
    formulation: CompiledFormulation,
    *,
    policy: AdmissionPolicy,
    units: UnitEvidence | None = None,
    provenance: AdmissionProvenance | None = None,
    expected_model_sha256: str | None = None,
) -> AdmissionRecord:
    """Validate everything, or raise AdmissionError. Pure: never calls a solver."""
    man = formulation.manifest
    input_sha = _sha(canonical_input_json(problem))
    if man.input_sha256 != input_sha:
        raise AdmissionError("PROBLEM_MANIFEST_MISMATCH", "manifest does not belong to the problem")
    try:
        again = compile_problem(problem, problem_id=man.problem_id, lineage=man.lineage)
        model_sha = compute_model_sha256(formulation)
    except Exception as exc:
        raise AdmissionError("FORMULATION_INVALID", f"formulation rejected: {exc}") from exc
    if again.spec_json != formulation.spec_json or again.manifest != man:
        raise AdmissionError("FORMULATION_TAMPERED", "spec/manifest differ from recompilation")
    if expected_model_sha256 is not None and expected_model_sha256 != model_sha:
        raise AdmissionError("STALE_MODEL_HASH", "model hash changed since it was admitted")
    claimed = {x.split(":", 1)[1] for x in man.lineage if x.startswith("unit_report:")}
    unit_status = "LEGACY_UNITS_UNVERIFIED"
    report_sha = fingerprint = ""
    status = AdmissionStatus.ADMITTED_LEGACY_UNVERIFIED
    warning = LEGACY_WARNING
    if units is None:
        if policy is AdmissionPolicy.STRICT or claimed:
            raise AdmissionError(
                "UNIT_EVIDENCE_MISSING",
                "unit evidence is required (STRICT, or the formulation claims unit lineage)",
            )
    else:
        try:
            report = verify_unit_evidence(units, problem)
        except AdmissionError:
            raise
        except PlanningInputError as exc:
            raise AdmissionError(exc.code, exc.message) from exc
        if claimed and claimed != {report.sha256}:
            raise AdmissionError(
                "UNIT_LINEAGE_MISMATCH", "lineage unit report differs from evidence"
            )
        unit_status, report_sha = "DIMENSIONALLY_VALIDATED", report.sha256
        fingerprint = _sha(repr(registry_of(units).fingerprint()))
        status, warning = AdmissionStatus.ADMITTED_UNITS_VERIFIED, ""
    return _seal(
        AdmissionRecord(
            status=status,
            policy=policy,
            problem_input_sha256=input_sha,
            spec_sha256=man.spec_sha256,
            model_sha256=model_sha,
            unit_status=unit_status,
            unit_report_sha256=report_sha,
            custom_registry_fingerprint=fingerprint,
            provenance=provenance or AdmissionProvenance(),
            warning=warning,
        )
    )


def verify_admission(
    record: AdmissionRecord,
    problem: PlanningProblem,
    formulation: CompiledFormulation,
    units: UnitEvidence | None,
) -> None:
    """Re-run admission from scratch and require the identical record (tamper/stale check)."""
    fresh = admit_for_solve(
        problem,
        formulation,
        policy=record.policy,
        units=units,
        provenance=record.provenance,
        expected_model_sha256=record.model_sha256,
    )
    if fresh != record:
        raise AdmissionError("ADMISSION_INVALID", "admission record differs from recomputation")


def require_strict(record: AdmissionRecord) -> None:
    if not record.units_verified:
        raise AdmissionError("ADMISSION_NOT_VERIFIED", "STRICT requires verified unit evidence")
