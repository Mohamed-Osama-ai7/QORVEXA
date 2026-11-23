"""Autonomous problem formulation: fact ledger, gap/contradiction analysis, validated builder.

Advisory only. Produces a Production Planning problem and its compiled formulation; it never
solves and never writes to any machine.
"""

from qorvexa.industrial.formulation.analysis import (
    FactState,
    FormulationRecord,
    FormulationResult,
    FormulationState,
    FormulationStatus,
    Issue,
    ResolvedFact,
    analyze,
    apply_defaults,
    confirm_keys,
    formulate,
)
from qorvexa.industrial.formulation.draft import propose_from_draft
from qorvexa.industrial.formulation.ledger import (
    Actor,
    ActorKind,
    Basis,
    Entry,
    FormulationError,
    Ledger,
    Op,
)
from qorvexa.industrial.formulation.unitflow import convert_units

__all__ = [
    "Actor",
    "ActorKind",
    "Basis",
    "Entry",
    "FactState",
    "FormulationError",
    "FormulationRecord",
    "FormulationResult",
    "FormulationState",
    "FormulationStatus",
    "Issue",
    "Ledger",
    "Op",
    "ResolvedFact",
    "analyze",
    "apply_defaults",
    "confirm_keys",
    "convert_units",
    "formulate",
    "propose_from_draft",
]
