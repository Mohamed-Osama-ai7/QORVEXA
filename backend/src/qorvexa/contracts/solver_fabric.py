"""Canonical contracts for QORVEXA Solver Fabric and generalized problem formulation.

Provides Pydantic v2 domain models for:
- GeneralizedProblemSpec (ProblemSpec v1.1.0)
- Typed JSON ExprNode AST (with MathJSON bidirectional interoperability)
- Dimension-aware scaled residual verification
- Independent verification certificates (separating solver claims from mathematical proofs)
- SolverFabricJob and SolverFabricResult lifecycle contracts
- v1.0.0 -> v1.1.0 backward compatibility migration adapter
"""

from __future__ import annotations

import copy
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)
from pydantic.alias_generators import to_camel

# Schema Version Constants
CANONICAL_SCHEMA_VERSION = "1.1.0"
PROBLEM_SPEC_SCHEMA_VERSION_1_0 = "1.0.0"
PROBLEM_SPEC_SCHEMA_VERSION_1_1 = "1.1.0"

# Core Epistemic & Problem Domain Types
EpistemicClassification = Literal["extracted_fact", "assumption", "hypothesis"]

ProblemType = Literal[
    "linear_programming",
    "mixed_integer_linear_programming",
    "nonlinear_programming",
    "constraint_programming",
    "scheduling",
    "routing",
    "resource_allocation",
    "simulation_what_if",
    "root_cause_diagnostic",
    "unknown",
]

FormulationStatus = Literal[
    "Draft Ready for Solver Review",
    "Needs Clarification — Missing Information",
    "Insufficient Information",
]

VariableDomain = Literal[
    "continuous",
    "integer",
    "binary",
    "semi_continuous",
    "semi_integer",
    "categorical",
    "discrete_set",
    "unknown",
]

ObjectiveDirection = Literal["minimize", "maximize"]

ConstraintCategory = Literal[
    "capacity",
    "safety",
    "balance",
    "regulatory",
    "operational",
    "temporal",
    "quality",
    "other",
]

ConstraintRelation = Literal[
    "<=",
    ">=",
    "==",
    "range",
    "in_set",
    "sos1",
    "sos2",
    "logical",
    "<",
    ">",
    "!=",
]

ConstraintHardness = Literal["hard", "soft", "unknown"]

MissingInfoSeverity = Literal["blocking", "important", "optional"]

UncertaintyType = Literal[
    "aleatoric_data",
    "epistemic_missing",
    "model_structure",
    "unit_ambiguity",
    "measurement_noise",
]

EvidenceSourceType = Literal[
    "problem_statement",
    "Document",
    "Sensor CSV",
    "Multimodal",
    "Operator Note",
]

MultiObjectiveStrategy = Literal[
    "single_objective",
    "weighted_sum",
    "lexicographic_hierarchical",
    "pareto_frontier_exploration",
]

# ---------------------------------------------------------------------------
# 1. Dimensionality Specification (SI + Custom Base Dimensions)
# ---------------------------------------------------------------------------


class Dimensionality(BaseModel):
    """Dimensional analysis powers for dimensional consistency validation."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    length: float | None = Field(default=None, description="Length dimension power L (meters)")
    mass: float | None = Field(default=None, description="Mass dimension power M (kilograms)")
    time: float | None = Field(default=None, description="Time dimension power T (seconds)")
    current: float | None = Field(default=None, description="Current power I (amperes)")
    temperature: float | None = Field(default=None, description="Temperature Theta (kelvin)")
    currency: float | None = Field(default=None, description="Monetary units power (EUR, USD)")
    dimensionless: bool | None = Field(default=None, description="Explicit dimensionless flag")


# ---------------------------------------------------------------------------
# 2. Canonical Expression AST (Typed JSON ExprNode)
# ---------------------------------------------------------------------------

UnaryOperator = Literal["abs", "neg", "sqrt", "exp", "log", "sin", "cos", "not"]
BinaryOperator = Literal["add", "sub", "mul", "div", "pow", "min", "max", "and", "or", "xor", "mod"]
NaryOperator = Literal["sum", "prod", "all", "any", "piecewise_linear"]
ComparisonOperator = Literal["<=", ">=", "==", "<", ">", "!="]


class LiteralNode(BaseModel):
    """Constant scalar value with optional physical unit and dimension."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    type: Literal["literal"] = "literal"
    data_type: Literal["real", "integer", "boolean", "string"] = Field(..., alias="dataType")
    value: float | int | bool | str
    unit: str | None = None
    dimension: Dimensionality | None = None


class VariableRefNode(BaseModel):
    """Reference to an optimization or simulation decision variable."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    type: Literal["variable_ref"] = "variable_ref"
    variable_id: str = Field(..., alias="variableId")
    time_index: int | None = Field(default=None, alias="timeIndex")
    unit: str | None = None
    dimension: Dimensionality | None = None


class ParameterRefNode(BaseModel):
    """Reference to an external model parameter or calibrated constant."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    type: Literal["parameter_ref"] = "parameter_ref"
    parameter_id: str = Field(..., alias="parameterId")
    default_value: float | None = Field(default=None, alias="defaultValue")
    unit: str | None = None
    dimension: Dimensionality | None = None


class UnaryOpNode(BaseModel):
    """Unary mathematical operation (negation, sqrt, exp, log, abs, etc.)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    type: Literal["unary_op"] = "unary_op"
    operator: UnaryOperator
    argument: ExprNode
    dimension: Dimensionality | None = None


class BinaryOpNode(BaseModel):
    """Binary mathematical or logical operation."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    type: Literal["binary_op"] = "binary_op"
    operator: BinaryOperator
    left: ExprNode
    right: ExprNode
    dimension: Dimensionality | None = None


class NaryOpNode(BaseModel):
    """N-ary associative operation across multiple expression nodes."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    type: Literal["nary_op"] = "nary_op"
    operator: NaryOperator
    arguments: list[ExprNode]
    dimension: Dimensionality | None = None


class ComparisonNode(BaseModel):
    """Relational comparison between two algebraic expressions."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    type: Literal["comparison"] = "comparison"
    operator: ComparisonOperator
    left: ExprNode
    right: ExprNode


class LookupTableNode(BaseModel):
    """Empirical or characteristic performance curve lookup."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    type: Literal["lookup_table"] = "lookup_table"
    curve_id: str = Field(..., alias="curveId")
    input: ExprNode
    interpolation: Literal["linear", "cubic_spline"] = "linear"
    unit: str | None = None
    dimension: Dimensionality | None = None


class StatisticalDistNode(BaseModel):
    """Parametric probability distribution for stochastic parameters."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    type: Literal["statistical_distribution"] = "statistical_distribution"
    distribution: Literal["normal", "uniform", "triangular", "weibull"]
    parameters: dict[str, float]
    unit: str | None = None
    dimension: Dimensionality | None = None


class UncompiledNode(BaseModel):
    """Unparsed natural language or LaTeX summary prior to symbolic compilation."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    type: Literal["uncompiled"] = "uncompiled"
    expression_summary: str = Field(..., alias="expressionSummary")


ExprNode = Annotated[
    LiteralNode
    | VariableRefNode
    | ParameterRefNode
    | UnaryOpNode
    | BinaryOpNode
    | NaryOpNode
    | ComparisonNode
    | LookupTableNode
    | StatisticalDistNode
    | UncompiledNode,
    Field(discriminator="type"),
]

# Rebuild recursive models with forward references
UnaryOpNode.model_rebuild()
BinaryOpNode.model_rebuild()
NaryOpNode.model_rebuild()
ComparisonNode.model_rebuild()
LookupTableNode.model_rebuild()


# ---------------------------------------------------------------------------
# 2.1 MathJSON Interoperability Helpers
# ---------------------------------------------------------------------------

_BINARY_OP_MAP_TO_MATHJSON: dict[str, str] = {
    "add": "Add",
    "sub": "Subtract",
    "mul": "Multiply",
    "div": "Divide",
    "pow": "Power",
    "min": "Min",
    "max": "Max",
    "and": "And",
    "or": "Or",
    "xor": "Xor",
    "mod": "Mod",
}
_BINARY_OP_MAP_FROM_MATHJSON = {v.lower(): k for k, v in _BINARY_OP_MAP_TO_MATHJSON.items()}

_COMPARISON_OP_MAP_TO_MATHJSON: dict[str, str] = {
    "<=": "LessEqual",
    ">=": "GreaterEqual",
    "==": "Equal",
    "<": "Less",
    ">": "Greater",
    "!=": "NotEqual",
}
_COMPARISON_OP_MAP_FROM_MATHJSON = {v.lower(): k for k, v in _COMPARISON_OP_MAP_TO_MATHJSON.items()}


def expr_to_mathjson(node: ExprNode) -> Any:
    """Converts a typed ExprNode AST into standard MathJSON representation."""
    if isinstance(node, LiteralNode):
        return node.value
    if isinstance(node, VariableRefNode):
        if node.time_index is not None:
            return ["At", node.variable_id, node.time_index]
        return node.variable_id
    if isinstance(node, ParameterRefNode):
        return node.parameter_id
    if isinstance(node, UnaryOpNode):
        op_name = node.operator.capitalize() if node.operator != "neg" else "Negate"
        return [op_name, expr_to_mathjson(node.argument)]
    if isinstance(node, BinaryOpNode):
        op_name = _BINARY_OP_MAP_TO_MATHJSON.get(node.operator, node.operator.capitalize())
        return [op_name, expr_to_mathjson(node.left), expr_to_mathjson(node.right)]
    if isinstance(node, NaryOpNode):
        if node.operator == "sum":
            op_name = "Add"
        elif node.operator == "prod":
            op_name = "Multiply"
        else:
            op_name = node.operator.capitalize()
        return [op_name, *[expr_to_mathjson(arg) for arg in node.arguments]]
    if isinstance(node, ComparisonNode):
        op_name = _COMPARISON_OP_MAP_TO_MATHJSON.get(node.operator, "Equal")
        return [op_name, expr_to_mathjson(node.left), expr_to_mathjson(node.right)]
    if isinstance(node, LookupTableNode):
        return ["Lookup", node.curve_id, expr_to_mathjson(node.input)]
    if isinstance(node, StatisticalDistNode):
        return ["Distribution", node.distribution, node.parameters]
    if isinstance(node, UncompiledNode):
        return ["Latex", node.expression_summary]
    return str(node)


def mathjson_to_expr(data: Any) -> ExprNode:
    """Parses standard MathJSON into a typed ExprNode AST."""
    if isinstance(data, (int, float, bool)):
        data_type: Literal["real", "integer", "boolean", "string"]
        if isinstance(data, bool):
            data_type = "boolean"
        elif isinstance(data, int):
            data_type = "integer"
        else:
            data_type = "real"
        return LiteralNode(dataType=data_type, value=data)

    if isinstance(data, str):
        return VariableRefNode(variableId=data)

    if isinstance(data, list) and len(data) > 0:
        op = str(data[0]).lower()
        args = data[1:]

        if op in _COMPARISON_OP_MAP_FROM_MATHJSON and len(args) == 2:
            comp_op = _COMPARISON_OP_MAP_FROM_MATHJSON[op]
            return ComparisonNode(
                operator=comp_op,
                left=mathjson_to_expr(args[0]),
                right=mathjson_to_expr(args[1]),
            )
        if op in _BINARY_OP_MAP_FROM_MATHJSON:
            bin_op = _BINARY_OP_MAP_FROM_MATHJSON[op]
            if len(args) == 2:
                return BinaryOpNode(
                    operator=bin_op,
                    left=mathjson_to_expr(args[0]),
                    right=mathjson_to_expr(args[1]),
                )
            if len(args) > 2 and bin_op in ("add", "mul"):
                nary_op: NaryOperator = "sum" if bin_op == "add" else "prod"
                return NaryOpNode(operator=nary_op, arguments=[mathjson_to_expr(a) for a in args])

        if op in ("negate", "neg") and len(args) == 1:
            return UnaryOpNode(operator="neg", argument=mathjson_to_expr(args[0]))
        if op in ("sqrt", "exp", "log", "abs", "sin", "cos", "not") and len(args) == 1:
            return UnaryOpNode(operator=op, argument=mathjson_to_expr(args[0]))
        if op == "at" and len(args) == 2:
            return VariableRefNode(variableId=str(args[0]), timeIndex=int(args[1]))
        if op == "latex" and len(args) == 1:
            return UncompiledNode(expressionSummary=str(args[0]))

    return UncompiledNode(expressionSummary=str(data))


# ---------------------------------------------------------------------------
# 3. Problem Formulation Sub-Specifications (v1.1.0)
# ---------------------------------------------------------------------------


class TimeHorizonSpec(BaseModel):
    """Temporal horizon and discretization intervals."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    is_specified: bool = Field(..., alias="isSpecified")
    duration: str | None = None
    time_step: str | None = Field(default=None, alias="timeStep")
    unit: str | None = None
    notes: str = ""


class UnitDefinition(BaseModel):
    """Physical quantity unit specification and provenance."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    quantity: str
    unit: str
    dimensionality: Dimensionality | None = None
    source: Literal["extracted_fact", "assumption", "missing"] = "extracted_fact"
    evidence_ref: str = Field(default="", alias="evidenceRef")


class DecisionVariableSpecV1_1(BaseModel):  # noqa: N801
    """Optimization or decision variable specification with scale metadata."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    id: str
    name: str
    symbol: str
    domain: VariableDomain
    unit: str = ""
    dimensionality: Dimensionality | None = None
    nominal_scale: float | None = Field(default=None, alias="nominalScale")
    lower_bound: float | None = Field(default=None, alias="lowerBound")
    upper_bound: float | None = Field(default=None, alias="upperBound")
    discrete_values: list[float | str] | None = Field(default=None, alias="discreteValues")
    initial_value: float | None = Field(default=None, alias="initialValue")
    description: str = ""
    epistemic_status: EpistemicClassification = Field(
        default="extracted_fact", alias="epistemicStatus"
    )
    supporting_evidence_ids: list[str] = Field(default_factory=list, alias="supportingEvidenceIds")


class ObjectiveSpecV1_1(BaseModel):  # noqa: N801
    """Objective formulation with hierarchical priority and tolerances."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    id: str
    direction: ObjectiveDirection
    name: str
    expression_summary: str = Field(..., alias="expressionSummary")
    expression_ast: ExprNode | None = Field(default=None, alias="expressionAst")
    unit: str = ""
    priority_order: int = Field(default=1, ge=1, alias="priorityOrder")
    weight: float | None = None
    abs_tolerance: float | None = Field(default=None, alias="absTolerance")
    rel_tolerance: float | None = Field(default=None, alias="relTolerance")
    epistemic_status: EpistemicClassification = Field(
        default="extracted_fact", alias="epistemicStatus"
    )
    supporting_evidence_ids: list[str] = Field(default_factory=list, alias="supportingEvidenceIds")


class ConstraintSpecV1_1(BaseModel):  # noqa: N801
    """Constraint definition with dimension-aware scale and tolerances."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    id: str
    name: str
    category: ConstraintCategory
    relation: ConstraintRelation
    expression_summary: str = Field(..., alias="expressionSummary")
    expression_ast: ExprNode | None = Field(default=None, alias="expressionAst")
    bound_value: float | None = Field(default=None, alias="boundValue")
    upper_bound_value: float | None = Field(default=None, alias="upperBoundValue")
    unit: str = ""
    nominal_scale: float | None = Field(default=None, alias="nominalScale")
    abs_tolerance: float | None = Field(default=None, alias="absTolerance")
    rel_tolerance: float | None = Field(default=None, alias="relTolerance")
    hardness: ConstraintHardness = "hard"
    penalty_weight: float | None = Field(default=None, alias="penaltyWeight")
    epistemic_status: EpistemicClassification = Field(
        default="extracted_fact", alias="epistemicStatus"
    )
    supporting_evidence_ids: list[str] = Field(default_factory=list, alias="supportingEvidenceIds")


class ExtractedFact(BaseModel):
    """Fact extracted directly from engineering documentation or problem statement."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    id: str
    statement: str
    source_quote: str = Field(default="", alias="sourceQuote")
    evidence_ref: str = Field(default="", alias="evidenceRef")


class FormulationAssumption(BaseModel):
    """Explicitly surfaced assumption requiring review before industrial deployment."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    id: str
    statement: str
    category: Literal["assumption", "hypothesis"] = "assumption"
    rationale: str = ""
    impact_if_false: str = Field(default="", alias="impactIfFalse")
    requires_user_confirmation: bool = Field(default=False, alias="requiresUserConfirmation")


class MissingInformationItem(BaseModel):
    """Identified parameter gap that blocks or degrades mathematical solving."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    id: str
    field: str
    question_for_user: str = Field(..., alias="questionForUser")
    severity: MissingInfoSeverity = "important"
    why_required_for_solver: str = Field(default="", alias="whyRequiredForSolver")


class UncertaintyItem(BaseModel):
    """Quantified aleatoric or epistemic model uncertainty."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    id: str
    parameter_or_aspect: str = Field(..., alias="parameterOrAspect")
    uncertainty_type: UncertaintyType = Field(..., alias="uncertaintyType")
    description: str = ""
    mitigation_or_data_needed: str = Field(default="", alias="mitigationOrDataNeeded")


class SupportingEvidenceRef(BaseModel):
    """Traceable provenance link to underlying source documents or telemetry."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    evidence_id: str = Field(..., alias="evidenceId")
    source_type: EvidenceSourceType = Field(default="problem_statement", alias="sourceType")
    excerpt: str = ""
    reliability_note: str = Field(default="", alias="reliabilityNote")


class SolverHandoffNotes(BaseModel):
    """Handoff metadata and solver recommendations."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    recommended_solver_family: str = Field(..., alias="recommendedSolverFamily")
    readiness_for_mathematical_solver: bool = Field(
        default=True, alias="readinessForMathematicalSolver"
    )
    blocking_gaps_count: int = Field(default=0, alias="blockingGapsCount")
    epistemic_boundary_notice: str = Field(default="", alias="epistemicBoundaryNotice")
    target_solver_id: str | None = Field(default=None, alias="targetSolverId")
    timeout_seconds: float | None = Field(default=None, alias="timeoutSeconds")
    optimality_tolerance: float | None = Field(default=None, alias="optimalityTolerance")


# ---------------------------------------------------------------------------
# 4. Generalized Problem Specification (GeneralizedProblemSpec v1.1.0)
# ---------------------------------------------------------------------------


class GeneralizedProblemSpec(BaseModel):
    """Canonical Generalized Problem Specification (ProblemSpec v1.1.0).

    Strict Epistemic Guardrail:
    AI formulation outputs MUST maintain mathematically_verified = False.
    Mathematical verification requires independent mathematical solver execution.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    schema_version: Literal["1.1.0", "1.0.0"] = Field(default="1.1.0", alias="schemaVersion")
    problem_id: str | None = Field(default=None, alias="problemId")
    title: str
    problem_description: str = Field(..., alias="problemDescription")
    problem_type: ProblemType = Field(default="unknown", alias="problemType")
    formulation_status: FormulationStatus = Field(
        default="Draft Ready for Solver Review", alias="formulationStatus"
    )

    # IMMUTABLE EPISTEMIC GUARD: Always false in formulation
    mathematically_verified: Literal[False] = Field(default=False, alias="mathematicallyVerified")

    time_horizon: TimeHorizonSpec = Field(..., alias="timeHorizon")
    multi_objective_strategy: MultiObjectiveStrategy | None = Field(
        default=None, alias="multiObjectiveStrategy"
    )
    units: list[UnitDefinition] = Field(default_factory=list)

    decision_variables: list[DecisionVariableSpecV1_1] = Field(
        default_factory=list, alias="decisionVariables"
    )
    objectives: list[ObjectiveSpecV1_1] = Field(default_factory=list)
    constraints: list[ConstraintSpecV1_1] = Field(default_factory=list)

    extracted_facts: list[ExtractedFact] = Field(default_factory=list, alias="extractedFacts")
    assumptions: list[FormulationAssumption] = Field(default_factory=list)
    missing_information: list[MissingInformationItem] = Field(
        default_factory=list, alias="missingInformation"
    )
    uncertainties: list[UncertaintyItem] = Field(default_factory=list)
    supporting_evidence: list[SupportingEvidenceRef] = Field(
        default_factory=list, alias="supportingEvidence"
    )

    clarification_questions: list[str] = Field(default_factory=list, alias="clarificationQuestions")
    solver_handoff_notes: SolverHandoffNotes = Field(..., alias="solverHandoffNotes")

    @field_validator("mathematically_verified", mode="before")
    @classmethod
    def _validate_mathematically_verified_epistemic_guard(cls, v: Any) -> Literal[False]:
        if v is True:
            raise ValueError(
                "Epistemic violation: ProblemSpec generated by AI formulation cannot be "
                "mathematically verified. Verification requires independent mathematical "
                "solver execution."
            )
        return False


# ---------------------------------------------------------------------------
# 5. Verification Contracts & Residual Computation
# ---------------------------------------------------------------------------


class ConstraintEvaluation(BaseModel):
    """Detailed residual and satisfaction check for one constraint."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    constraint_id: str = Field(..., alias="constraintId")
    lhs_value: float = Field(..., alias="lhsValue")
    relation: str
    rhs_value: float = Field(..., alias="rhsValue")
    slack: float
    scaled_residual: float = Field(..., alias="scaledResidual")
    is_violated: bool = Field(..., alias="isViolated")
    is_binding: bool = Field(default=False, alias="isBinding")


class FeasibilityVerificationResult(BaseModel):
    """Independent primal feasibility verification outcome."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    verified: bool
    status: Literal[
        "VERIFIED_FEASIBLE",
        "FEASIBLE_WITH_VIOLATIONS",
        "PROVEN_INFEASIBLE",
        "LOCALLY_INFEASIBLE",
        "UNVERIFIED",
    ]
    max_absolute_residual: float = Field(default=0.0, alias="maxAbsoluteResidual")
    max_scaled_residual: float = Field(default=0.0, alias="maxScaledResidual")
    scaled_residual_tolerance_used: float = Field(default=1e-6, alias="scaledResidualToleranceUsed")
    violated_constraints_count: int = Field(default=0, alias="violatedConstraintsCount")
    worst_violated_constraint_id: str | None = Field(
        default=None, alias="worstViolatedConstraintId"
    )
    constraint_evaluations: list[ConstraintEvaluation] = Field(
        default_factory=list, alias="constraintEvaluations"
    )


class OptimalityVerificationResult(BaseModel):
    """Independent optimality and dual certificate verification outcome."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    verified: bool
    status: Literal[
        "CERTIFIED_OPTIMAL",
        "OPTIMAL_WITHIN_GAP",
        "FIRST_ORDER_KKT_POINT",
        "FEASIBLE_SUBOPTIMAL",
        "SOLVER_CLAIMED_OPTIMAL_UNVERIFIED",
        "PROVEN_INFEASIBLE",
        "UNBOUNDED",
        "NOT_APPLICABLE",
    ]
    primal_objective: float | None = Field(default=None, alias="primalObjective")
    dual_bound: float | None = Field(default=None, alias="dualBound")
    absolute_gap: float | None = Field(default=None, alias="absoluteGap")
    relative_gap: float | None = Field(default=None, alias="relativeGap")
    optimality_tolerance_used: float = Field(default=1e-4, alias="optimalityToleranceUsed")
    verification_family: str = Field(default="", alias="verificationFamily")
    is_independently_verifiable: bool = Field(default=True, alias="isIndependentlyVerifiable")
    kkt_stationarity_residual: float | None = Field(default=None, alias="kktStationarityResidual")
    complementarity_residual: float | None = Field(default=None, alias="complementarityResidual")
    constraint_qualification_status: str | None = Field(
        default=None, alias="constraintQualificationStatus"
    )


class InfeasibilityEvidence(BaseModel):
    """Mathematical evidence identifying why a model cannot be satisfied."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    certificate_type: Literal[
        "farkas_certificate",
        "iis_subsystem",
        "solver_claimed_infeasible",
        "unbounded_ray",
    ] = Field(..., alias="certificateType")
    is_independently_verifiable: bool = Field(default=False, alias="isIndependentlyVerifiable")
    conflicting_constraint_ids: list[str] = Field(
        default_factory=list, alias="conflictingConstraintIds"
    )
    farkas_multipliers: dict[str, float] = Field(default_factory=dict, alias="farkasMultipliers")
    explanation: str = ""


class VerificationCertificate(BaseModel):
    """Cryptographic audit token certifying independent verification."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    certificate_id: str = Field(..., alias="certificateId")
    verifier_engine: str = Field(..., alias="verifierEngine")
    certificate_sha256: str = Field(..., alias="certificateSha256")
    verified_at: str = Field(..., alias="verifiedAt")
    model_digest_sha256: str = Field(default="", alias="modelDigestSha256")
    solution_digest_sha256: str = Field(default="", alias="solutionDigestSha256")


def compute_dimension_scaled_residual(
    lhs: float,
    rhs: float,
    relation: str,
    nominal_scale: float | None = None,
    abs_tol: float = 1e-6,
    rel_tol: float = 1e-6,
) -> tuple[float, float, bool, float]:
    """Computes dimension-aware scaled residual and feasibility for a scalar constraint.

    Formula:
        s_con = max(nominal_scale or 1.0, |rhs|, 1.0)
        abs_residual: raw positive violation beyond permissible bound
        scaled_residual: abs_residual / s_con
        is_violated: abs_residual > abs_tol + rel_tol * s_con

    Returns:
        (abs_residual, scaled_residual, is_violated, slack)
    """
    scale = max(
        nominal_scale if nominal_scale is not None and nominal_scale > 0 else 1.0,
        abs(rhs),
        1.0,
    )

    diff = lhs - rhs
    if relation in ("<=", "<"):
        slack = rhs - lhs
        abs_residual = max(0.0, diff)
    elif relation in (">=", ">"):
        slack = lhs - rhs
        abs_residual = max(0.0, -diff)
    elif relation in ("==", "="):
        slack = -abs(diff)
        abs_residual = abs(diff)
    else:
        slack = 0.0
        abs_residual = 0.0

    scaled_residual = abs_residual / scale
    permissible_tol = abs_tol + rel_tol * scale
    is_violated = abs_residual > permissible_tol

    return abs_residual, scaled_residual, is_violated, slack


# ---------------------------------------------------------------------------
# 6. Solver Fabric Job & Execution Lifecycle Contracts
# ---------------------------------------------------------------------------


class SolverExecutionConfig(BaseModel):
    """Runtime solver execution limits and parameters."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    target_solver_id: str = Field(default="highs", alias="targetSolverId")
    timeout_seconds: float = Field(default=60.0, ge=0.5, le=7200.0, alias="timeoutSeconds")
    max_memory_mb: int = Field(default=4096, ge=256, le=32768, alias="maxMemoryMb")
    optimality_tolerance: float = Field(default=1e-4, ge=1e-12, le=0.1, alias="optimalityTolerance")
    feasibility_tolerance: float = Field(
        default=1e-6, ge=1e-12, le=0.1, alias="feasibilityTolerance"
    )
    allow_early_termination: bool = Field(default=True, alias="allowEarlyTermination")
    custom_parameters: dict[str, Any] = Field(default_factory=dict, alias="customParameters")


class SolverFabricJob(BaseModel):
    """Job envelope tracked in the persistent ledger."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    job_id: str = Field(..., alias="jobId")
    problem_id: str = Field(..., alias="problemId")
    target_asset_id: str | None = Field(default=None, alias="targetAssetId")
    tenant_id: str = Field(default="default", alias="tenantId")
    status: Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"] = "QUEUED"
    sub_status_code: str | None = Field(default=None, alias="subStatusCode")
    created_at: str = Field(..., alias="createdAt")
    started_at: str | None = Field(default=None, alias="startedAt")
    completed_at: str | None = Field(default=None, alias="completedAt")
    execution_config: SolverExecutionConfig = Field(
        default_factory=SolverExecutionConfig, alias="executionConfig"
    )
    problem_spec: GeneralizedProblemSpec = Field(..., alias="problemSpec")


# ---------------------------------------------------------------------------
# 7. Solver Fabric Result Contract
# ---------------------------------------------------------------------------


class CandidateSolution(BaseModel):
    """Optimal or feasible candidate decision variable assignments."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    status: Literal[
        "FEASIBLE_CANDIDATE_PRESENT",
        "NO_SOLUTION_FOUND",
        "INFEASIBLE_CERTIFIED",
        "UNBOUNDED",
    ] = "FEASIBLE_CANDIDATE_PRESENT"
    objective_values: list[dict[str, Any]] = Field(default_factory=list, alias="objectiveValues")
    variable_assignments: list[dict[str, Any]] = Field(
        default_factory=list, alias="variableAssignments"
    )


class SolverTerminationInfo(BaseModel):
    """Raw telemetry reported directly by the underlying mathematical solver."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    engine: str
    termination_status: str = Field(..., alias="terminationStatus")
    termination_reason: str = Field(..., alias="terminationReason")
    solve_time_wall_seconds: float = Field(default=0.0, alias="solveTimeWallSeconds")
    solve_time_cpu_seconds: float = Field(default=0.0, alias="solveTimeCpuSeconds")
    iterations_count: int = Field(default=0, alias="iterationsCount")
    branch_and_bound_nodes: int = Field(default=0, alias="branchAndBoundNodes")


class GovernanceReview(BaseModel):
    """Human-in-the-loop governance signoff boundary and audit summary."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    human_approval_required: bool = Field(default=True, alias="humanApprovalRequired")
    approval_reason: str = Field(default="", alias="approvalReason")
    unconfirmed_assumptions_count: int = Field(default=0, alias="unconfirmedAssumptionsCount")
    epistemic_summary: str = Field(default="", alias="epistemicSummary")


class SolverFabricResult(BaseModel):
    """Complete canonical solver outcome decoupling solver claims from verification."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, alias_generator=to_camel)

    job_id: str = Field(..., alias="jobId")
    problem_id: str = Field(..., alias="problemId")
    schema_version: str = Field(default="1.1.0", alias="schemaVersion")
    completed_at: str = Field(..., alias="completedAt")
    total_elapsed_milliseconds: int = Field(default=0, alias="totalElapsedMilliseconds")

    solver_status: SolverTerminationInfo = Field(..., alias="solverStatus")
    candidate_solution: CandidateSolution | None = Field(default=None, alias="candidateSolution")

    feasibility_verification: FeasibilityVerificationResult = Field(
        ..., alias="feasibilityVerification"
    )
    optimality_verification: OptimalityVerificationResult = Field(
        ..., alias="optimalityVerification"
    )

    infeasibility_evidence: InfeasibilityEvidence | None = Field(
        default=None, alias="infeasibilityEvidence"
    )
    verification_certificate: VerificationCertificate | None = Field(
        default=None, alias="verificationCertificate"
    )

    numerical_warnings: list[str] = Field(default_factory=list, alias="numericalWarnings")
    failure_details: str | None = Field(default=None, alias="failureDetails")
    governance_review: GovernanceReview = Field(
        default_factory=GovernanceReview, alias="governanceReview"
    )


# ---------------------------------------------------------------------------
# 8. Migration Adapter (ProblemSpec v1.0.0 -> GeneralizedProblemSpec v1.1.0)
# ---------------------------------------------------------------------------


def adapt_problem_spec_v1_0_to_v1_1(
    raw_or_spec: dict[str, Any] | GeneralizedProblemSpec,
) -> GeneralizedProblemSpec:
    """Safely upgrades a ProblemSpec v1.0.0 payload to GeneralizedProblemSpec v1.1.0.

    Preserves 100% of facts, assumptions, uncertainties, missing info, and evidence.
    Converts:
    - `sense` -> `direction` ("minimize" | "maximize")
    - `priority` -> `priorityOrder` (1 or 2)
    - `relation` ("=" -> "==")
    - Adds default nominal scales for variables and constraints
    - Enforces epistemic guardrail: raises ValueError if mathematicallyVerified is True
    """
    if isinstance(raw_or_spec, GeneralizedProblemSpec):
        if raw_or_spec.mathematically_verified:
            raise ValueError(
                "Epistemic violation: ProblemSpec generated by AI formulation cannot be "
                "mathematically verified."
            )
        return raw_or_spec

    payload = copy.deepcopy(raw_or_spec)

    # Epistemic invariant check
    has_verified_flag = (
        payload.get("mathematicallyVerified") is True
        or payload.get("mathematically_verified") is True
    )
    if has_verified_flag:
        raise ValueError(
            "Epistemic violation: ProblemSpec generated by AI formulation cannot be "
            "mathematically verified. Verification requires independent mathematical "
            "solver execution."
        )
    payload["mathematicallyVerified"] = False

    # Upgrade schema version
    payload["schemaVersion"] = "1.1.0"

    # Adapt objectives
    raw_objectives = payload.get("objectives", [])
    adapted_objectives = []
    for idx, obj in enumerate(raw_objectives):
        obj_copy = copy.deepcopy(obj)
        sense = obj_copy.pop("sense", None)
        if sense:
            if sense == "maximize":
                obj_copy["direction"] = "maximize"
            else:
                obj_copy["direction"] = "minimize"
        elif "direction" not in obj_copy:
            obj_copy["direction"] = "minimize"

        priority = obj_copy.pop("priority", None)
        if priority == "primary":
            obj_copy["priorityOrder"] = 1
        elif priority == "secondary":
            obj_copy["priorityOrder"] = 2
        elif "priorityOrder" not in obj_copy and "priority_order" not in obj_copy:
            obj_copy["priorityOrder"] = idx + 1

        adapted_objectives.append(obj_copy)
    payload["objectives"] = adapted_objectives

    # Adapt constraints
    raw_constraints = payload.get("constraints", [])
    adapted_constraints = []
    for con in raw_constraints:
        con_copy = copy.deepcopy(con)
        rel = con_copy.get("relation", "<=")
        if rel == "=":
            con_copy["relation"] = "=="
        if "nominalScale" not in con_copy and "nominal_scale" not in con_copy:
            bound = (
                con_copy.get("boundValue")
                if "boundValue" in con_copy
                else con_copy.get("bound_value")
            )
            if bound is not None and abs(float(bound)) > 0:
                con_copy["nominalScale"] = abs(float(bound))
            else:
                con_copy["nominalScale"] = 1.0
        adapted_constraints.append(con_copy)
    payload["constraints"] = adapted_constraints

    # Adapt decision variables
    raw_variables = payload.get("decisionVariables") or payload.get("decision_variables") or []
    adapted_variables = []
    for var in raw_variables:
        var_copy = copy.deepcopy(var)
        if "nominalScale" not in var_copy and "nominal_scale" not in var_copy:
            lb = (
                var_copy.get("lowerBound")
                if "lowerBound" in var_copy
                else var_copy.get("lower_bound")
            )
            ub = (
                var_copy.get("upperBound")
                if "upperBound" in var_copy
                else var_copy.get("upper_bound")
            )
            if lb is not None and ub is not None and abs(float(ub) - float(lb)) > 0:
                var_copy["nominalScale"] = abs(float(ub) - float(lb))
            elif ub is not None and abs(float(ub)) > 0:
                var_copy["nominalScale"] = abs(float(ub))
            elif lb is not None and abs(float(lb)) > 0:
                var_copy["nominalScale"] = abs(float(lb))
            else:
                var_copy["nominalScale"] = 1.0
        adapted_variables.append(var_copy)
    payload["decisionVariables"] = adapted_variables

    return GeneralizedProblemSpec.model_validate(payload)
