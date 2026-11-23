"""Tests for QORVEXA Solver Fabric canonical contracts, AST expressions, and verification.

Milestone 1 Verification Suite:
- Ingestion and lossless adaptation of Research Lab v1.0.0 ProblemSpec payloads
- Strict epistemic invariant enforcement (mathematicallyVerified == False)
- Typed JSON ExprNode AST serialization and bidirectional MathJSON conversion
- Dimension-aware scaled residual computation
- Decoupled solver claims vs independent mathematical verification certificates
- SolverFabricJob and SolverFabricResult lifecycle contracts
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from qorvexa.contracts.solver_fabric import (
    BinaryOpNode,
    CandidateSolution,
    ComparisonNode,
    ConstraintEvaluation,
    FeasibilityVerificationResult,
    GeneralizedProblemSpec,
    GovernanceReview,
    InfeasibilityEvidence,
    LiteralNode,
    LookupTableNode,
    NaryOpNode,
    OptimalityVerificationResult,
    SolverExecutionConfig,
    SolverFabricJob,
    SolverFabricResult,
    SolverTerminationInfo,
    StatisticalDistNode,
    UnaryOpNode,
    UncompiledNode,
    VariableRefNode,
    VerificationCertificate,
    adapt_problem_spec_v1_0_to_v1_1,
    compute_dimension_scaled_residual,
    expr_to_mathjson,
    mathjson_to_expr,
)


def build_research_lab_v1_0_sample_payload() -> dict[str, Any]:
    """Generates the canonical 24-hour parallel pump station ProblemSpec v1.0.0 payload."""
    return {
        "schemaVersion": "1.0.0",
        "formulationStatus": "Draft Ready for Solver Review",
        "mathematicallyVerified": False,
        "problemType": "mixed_integer_linear_programming",
        "title": "Parallel Pump Station Energy Minimization",
        "problemDescription": (
            "Select pump on/off states and discharge flow rates across two parallel centrifugal "
            "pumps over a 24-hour horizon to satisfy hourly demand at minimum electricity cost "
            "without violating MCSF or maximum head limits."
        ),
        "timeHorizon": {
            "isSpecified": True,
            "duration": "24",
            "timeStep": "1",
            "unit": "hours",
            "notes": "Explicitly specified as 24 hourly periods.",
        },
        "units": [
            {
                "quantity": "Volumetric Flow Rate",
                "unit": "m3/h",
                "source": "extracted_fact",
                "evidenceRef": "EV-DOC-01",
            },
            {
                "quantity": "Electricity Tariff",
                "unit": "USD/kWh",
                "source": "extracted_fact",
                "evidenceRef": "PROBLEM_STATEMENT",
            },
        ],
        "decisionVariables": [
            {
                "id": "VAR-01",
                "name": "Pump Flow Rate",
                "symbol": "q_{p,t}",
                "domain": "continuous",
                "unit": "m3/h",
                "lowerBound": 0.0,
                "upperBound": 145.0,
                "description": "Flow delivered by pump p during hour t.",
                "epistemicStatus": "extracted_fact",
                "supportingEvidenceIds": ["PROBLEM_STATEMENT", "EV-DOC-01"],
            },
            {
                "id": "VAR-02",
                "name": "Pump Commitment",
                "symbol": "u_{p,t}",
                "domain": "binary",
                "unit": "dimensionless",
                "lowerBound": 0.0,
                "upperBound": 1.0,
                "description": "1 if pump p is running during hour t, 0 otherwise.",
                "epistemicStatus": "extracted_fact",
                "supportingEvidenceIds": ["PROBLEM_STATEMENT"],
            },
        ],
        "objectives": [
            {
                "id": "OBJ-01",
                "sense": "minimize",
                "name": "Total 24-Hour Pumping Energy Cost",
                "expressionSummary": "sum_{p,t} tariff_t * power(q_{p,t}, u_{p,t})",
                "unit": "USD",
                "priority": "primary",
                "epistemicStatus": "extracted_fact",
                "supportingEvidenceIds": ["PROBLEM_STATEMENT"],
            }
        ],
        "constraints": [
            {
                "id": "CON-01",
                "name": "Minimum Continuous Stable Flow (MCSF)",
                "category": "safety",
                "relation": ">=",
                "expressionSummary": "q_{p,t} >= 38 * u_{p,t}",
                "boundValue": 38.0,
                "unit": "m3/h",
                "hardness": "hard",
                "epistemicStatus": "extracted_fact",
                "supportingEvidenceIds": ["EV-DOC-01"],
            },
            {
                "id": "CON-02",
                "name": "Hourly Plant Demand Satisfaction",
                "category": "balance",
                "relation": ">=",
                "expressionSummary": "sum_p q_{p,t} >= demand_t",
                "boundValue": None,
                "unit": "m3/h",
                "hardness": "hard",
                "epistemicStatus": "extracted_fact",
                "supportingEvidenceIds": ["PROBLEM_STATEMENT"],
            },
        ],
        "extractedFacts": [
            {
                "id": "FACT-01",
                "statement": "Minimum continuous stable flow for Kestrel-MP400 is 38 m3/h.",
                "sourceQuote": "Minimum Continuous Stable Flow (MCSF): 38 m3/h",
                "evidenceRef": "EV-DOC-01",
            }
        ],
        "assumptions": [
            {
                "id": "ASM-01",
                "statement": "Hourly demand profile is deterministic and known ahead of schedule.",
                "category": "assumption",
                "rationale": (
                    "No stochastic demand scenarios were provided in the problem statement."
                ),
                "impactIfFalse": "Deterministic dispatch may under-serve peak excursions.",
                "requiresUserConfirmation": False,
            }
        ],
        "missingInformation": [],
        "uncertainties": [
            {
                "id": "UNC-01",
                "parameterOrAspect": "Power-vs-flow curve linearity",
                "uncertaintyType": "model_structure",
                "description": (
                    "Pump power draw is nonlinear in flow; piecewise linearization needed for MILP."
                ),
                "mitigationOrDataNeeded": (
                    "Confirm piecewise-linear break points from pump curve before solver execution."
                ),
            }
        ],
        "supportingEvidence": [
            {
                "evidenceId": "EV-DOC-01",
                "sourceType": "Document",
                "excerpt": "MCSF = 38 m3/h; Rated Flow BEP = 145 m3/h",
                "reliabilityNote": "Verified technical manual extract.",
            }
        ],
        "clarificationQuestions": [],
        "solverHandoffNotes": {
            "recommendedSolverFamily": "MILP Solver (HiGHS / CBC / Gurobi)",
            "readinessForMathematicalSolver": True,
            "blockingGapsCount": 0,
            "epistemicBoundaryNotice": (
                "Draft formulation only; requires mathematical verification."
            ),
        },
    }


# ===========================================================================
# 1. Backward Compatibility & Migration Adapter Tests
# ===========================================================================


def test_adapt_problem_spec_v1_0_to_v1_1_success() -> None:
    """Verifies that a canonical Research Lab v1.0.0 payload is losslessly migrated to v1.1.0."""
    raw_v1_0 = build_research_lab_v1_0_sample_payload()
    adapted = adapt_problem_spec_v1_0_to_v1_1(raw_v1_0)

    assert isinstance(adapted, GeneralizedProblemSpec)
    assert adapted.schema_version == "1.1.0"
    assert adapted.mathematically_verified is False
    assert adapted.title == "Parallel Pump Station Energy Minimization"
    assert adapted.problem_type == "mixed_integer_linear_programming"

    # Verify decision variables
    assert len(adapted.decision_variables) == 2
    var1 = adapted.decision_variables[0]
    assert var1.id == "VAR-01"
    assert var1.domain == "continuous"
    assert var1.lower_bound == 0.0
    assert var1.upper_bound == 145.0
    assert var1.nominal_scale == 145.0  # Characteristic upper - lower bound

    # Verify objectives (sense -> direction, priority -> priorityOrder)
    assert len(adapted.objectives) == 1
    obj1 = adapted.objectives[0]
    assert obj1.id == "OBJ-01"
    assert obj1.direction == "minimize"
    assert obj1.priority_order == 1

    # Verify constraints (relation checked, nominalScale populated)
    assert len(adapted.constraints) == 2
    con1 = adapted.constraints[0]
    assert con1.id == "CON-01"
    assert con1.relation == ">="
    assert con1.bound_value == 38.0
    assert con1.nominal_scale == 38.0

    # Verify provenance and evidence preservation
    assert len(adapted.extracted_facts) == 1
    assert adapted.extracted_facts[0].id == "FACT-01"
    assert len(adapted.assumptions) == 1
    assert adapted.assumptions[0].id == "ASM-01"
    assert len(adapted.uncertainties) == 1
    assert adapted.uncertainties[0].id == "UNC-01"
    assert len(adapted.supporting_evidence) == 1
    assert adapted.supporting_evidence[0].evidence_id == "EV-DOC-01"
    assert (
        adapted.solver_handoff_notes.recommended_solver_family
        == "MILP Solver (HiGHS / CBC / Gurobi)"
    )


def test_adapt_v1_0_edge_cases_mapping() -> None:
    """Verifies edge-case mappings (sense='satisfy'/'maximize', priority, relation='=')."""
    payload = build_research_lab_v1_0_sample_payload()
    payload["objectives"] = [
        {
            "id": "OBJ-SAT",
            "sense": "satisfy",
            "name": "Feasibility Satisfaction Only",
            "expressionSummary": "0",
            "unit": "",
            "priority": "secondary",
            "epistemicStatus": "extracted_fact",
            "supportingEvidenceIds": [],
        },
        {
            "id": "OBJ-MAX",
            "sense": "maximize",
            "name": "Maximize Throughput",
            "expressionSummary": "q_tot",
            "unit": "m3/h",
            "priority": "primary",
            "epistemicStatus": "extracted_fact",
            "supportingEvidenceIds": [],
        },
    ]
    payload["constraints"] = [
        {
            "id": "CON-EQ",
            "name": "Exact Balance",
            "category": "balance",
            "relation": "=",
            "expressionSummary": "flow_in = flow_out",
            "boundValue": 0.0,
            "unit": "m3/h",
            "hardness": "hard",
            "epistemicStatus": "extracted_fact",
            "supportingEvidenceIds": [],
        }
    ]

    adapted = adapt_problem_spec_v1_0_to_v1_1(payload)

    # Check objective mappings
    assert adapted.objectives[0].direction == "minimize"
    assert adapted.objectives[0].priority_order == 2
    assert adapted.objectives[1].direction == "maximize"
    assert adapted.objectives[1].priority_order == 1

    # Check constraint equality mapping '=' -> '=='
    assert adapted.constraints[0].relation == "=="
    assert adapted.constraints[0].nominal_scale == 1.0


# ===========================================================================
# 2. Epistemic Invariant Guardrail Enforcement Tests
# ===========================================================================


def test_epistemic_guardrail_rejection_direct_instantiation() -> None:
    """Rejects direct instantiation of GeneralizedProblemSpec with mathematicallyVerified = True."""
    payload = build_research_lab_v1_0_sample_payload()
    payload["mathematicallyVerified"] = True

    with pytest.raises(ValidationError) as exc_info:
        GeneralizedProblemSpec.model_validate(payload)

    assert "Epistemic violation" in str(exc_info.value)


def test_epistemic_guardrail_rejection_migration_adapter() -> None:
    """Rejects adaptation when payload explicitly claims mathematicallyVerified = True."""
    payload = build_research_lab_v1_0_sample_payload()
    payload["mathematicallyVerified"] = True

    with pytest.raises(ValueError, match="Epistemic violation"):
        adapt_problem_spec_v1_0_to_v1_1(payload)


def test_epistemic_guardrail_rejection_snake_case_alias() -> None:
    """Rejects adaptation when payload specifies mathematically_verified = True via snake_case."""
    payload = build_research_lab_v1_0_sample_payload()
    del payload["mathematicallyVerified"]
    payload["mathematically_verified"] = True

    with pytest.raises(ValueError, match="Epistemic violation"):
        adapt_problem_spec_v1_0_to_v1_1(payload)


# ===========================================================================
# 3. Typed JSON ExprNode AST & MathJSON Interoperability Tests
# ===========================================================================


def test_expr_node_ast_construction_and_serialization() -> None:
    """Constructs algebraic constraint tree: CON-01: q_{p,t} >= 38 * u_{p,t} and validates AST."""
    ast = ComparisonNode(
        operator=">=",
        left=VariableRefNode(variableId="VAR-01", unit="m3/h"),
        right=BinaryOpNode(
            operator="mul",
            left=LiteralNode(dataType="real", value=38.0, unit="m3/h"),
            right=VariableRefNode(variableId="VAR-02", unit="dimensionless"),
        ),
    )

    dumped = ast.model_dump(by_alias=True)
    assert dumped["type"] == "comparison"
    assert dumped["operator"] == ">="
    assert dumped["left"]["type"] == "variable_ref"
    assert dumped["left"]["variableId"] == "VAR-01"
    assert dumped["right"]["type"] == "binary_op"
    assert dumped["right"]["operator"] == "mul"
    assert dumped["right"]["left"]["value"] == 38.0

    # Round-trip through Pydantic
    reconstructed = ComparisonNode.model_validate(dumped)
    assert isinstance(reconstructed.left, VariableRefNode)
    assert isinstance(reconstructed.right, BinaryOpNode)
    assert reconstructed.right.operator == "mul"


def test_expr_node_mathjson_bidirectional_conversion() -> None:
    """Validates lossless bidirectional conversion between ExprNode AST and MathJSON."""
    ast = ComparisonNode(
        operator="<=",
        left=BinaryOpNode(
            operator="add",
            left=VariableRefNode(variableId="x_1"),
            right=VariableRefNode(variableId="x_2"),
        ),
        right=LiteralNode(dataType="real", value=100.0),
    )

    # Convert to MathJSON
    mathjson = expr_to_mathjson(ast)
    assert mathjson == ["LessEqual", ["Add", "x_1", "x_2"], 100.0]

    # Convert back to ExprNode AST
    converted_back = mathjson_to_expr(mathjson)
    assert isinstance(converted_back, ComparisonNode)
    assert converted_back.operator == "<="
    assert isinstance(converted_back.left, BinaryOpNode)
    assert converted_back.left.operator == "add"
    assert isinstance(converted_back.right, LiteralNode)
    assert converted_back.right.value == 100.0


def test_expr_node_specialized_nodes() -> None:
    """Tests UnaryOpNode, NaryOpNode, LookupTableNode, StatisticalDistNode, and UncompiledNode."""
    unary = UnaryOpNode(operator="sqrt", argument=LiteralNode(dataType="real", value=16.0))
    assert expr_to_mathjson(unary) == ["Sqrt", 16.0]

    nary = NaryOpNode(
        operator="sum",
        arguments=[
            VariableRefNode(variableId="v1"),
            VariableRefNode(variableId="v2"),
            VariableRefNode(variableId="v3"),
        ],
    )
    assert expr_to_mathjson(nary) == ["Add", "v1", "v2", "v3"]

    lookup = LookupTableNode(
        curveId="CURVE-PUMP-HEAD",
        input=VariableRefNode(variableId="flow_rate"),
        interpolation="cubic_spline",
    )
    assert lookup.interpolation == "cubic_spline"
    assert expr_to_mathjson(lookup) == ["Lookup", "CURVE-PUMP-HEAD", "flow_rate"]

    dist = StatisticalDistNode(
        distribution="normal",
        parameters={"mean": 10.0, "std": 1.5},
    )
    assert expr_to_mathjson(dist) == ["Distribution", "normal", {"mean": 10.0, "std": 1.5}]

    uncompiled = UncompiledNode(expressionSummary=r"\sum_{i=1}^n c_i x_i \le B")
    assert uncompiled.expression_summary == r"\sum_{i=1}^n c_i x_i \le B"


# ===========================================================================
# 4. Dimension-Aware Scaled Residual Calculation Tests
# ===========================================================================


def test_dimension_scaled_residual_linear_constraints() -> None:
    """Tests scaled residual evaluation for <=, >=, and == relations with slacks."""
    # Test <= constraint: LHS=85.4, RHS=100.0 -> feasible, slack=14.6
    abs_res, scaled_res, is_violated, slack = compute_dimension_scaled_residual(
        lhs=85.4, rhs=100.0, relation="<=", nominal_scale=100.0
    )
    assert abs_res == 0.0
    assert scaled_res == 0.0
    assert not is_violated
    assert pytest.approx(slack, rel=1e-6) == 14.6

    # Test <= constraint violated: LHS=110.0, RHS=100.0 -> violated by 10.0
    abs_res, scaled_res, is_violated, slack = compute_dimension_scaled_residual(
        lhs=110.0, rhs=100.0, relation="<=", nominal_scale=100.0
    )
    assert abs_res == 10.0
    assert pytest.approx(scaled_res, rel=1e-6) == 0.1
    assert is_violated
    assert pytest.approx(slack, rel=1e-6) == -10.0

    # Test >= constraint binding: LHS=38.0, RHS=38.0 -> feasible, binding (slack=0)
    abs_res, scaled_res, is_violated, slack = compute_dimension_scaled_residual(
        lhs=38.0, rhs=38.0, relation=">=", nominal_scale=38.0
    )
    assert abs_res == 0.0
    assert not is_violated
    assert pytest.approx(slack, rel=1e-6) == 0.0

    # Test == constraint exact: LHS=50.0, RHS=50.0 -> feasible
    abs_res, scaled_res, is_violated, slack = compute_dimension_scaled_residual(
        lhs=50.0, rhs=50.0, relation="==", nominal_scale=50.0
    )
    assert abs_res == 0.0
    assert not is_violated


def test_dimension_scaled_residual_physical_scaling_discrepancy() -> None:
    """Asserts that characteristic physical scale prevents false violations in high/low units."""
    # Case A: High magnitude physical unit (Pressure in Pa = 100,000 Pa)
    # A tiny numerical noise of 0.05 Pa on 100,000 Pa pipe is 0.5 ppm drift
    abs_res, scaled_res, is_violated, _ = compute_dimension_scaled_residual(
        lhs=100000.05,
        rhs=100000.0,
        relation="<=",
        nominal_scale=100000.0,
        abs_tol=1e-6,
        rel_tol=1e-6,
    )
    assert pytest.approx(abs_res, rel=1e-6) == 0.05
    assert scaled_res < 1e-6
    # Permissible tolerance = 1e-6 + 1e-6 * 100,000 = 0.100001 Pa
    # 0.05 <= 0.100001 -> Certified Feasible!
    assert not is_violated

    # Case B: Small magnitude physical unit (Clearance in meters = 0.005 m = 5 mm)
    # An error of 0.001 m (1 mm) on a 5 mm clearance is a 20% violation
    abs_res_b, _, is_violated_b, _ = compute_dimension_scaled_residual(
        lhs=0.006,
        rhs=0.005,
        relation="<=",
        nominal_scale=0.005,
        abs_tol=1e-6,
        rel_tol=1e-6,
    )
    assert pytest.approx(abs_res_b, rel=1e-6) == 0.001
    # Scale max(0.005, 0.005, 1.0) = 1.0 -> Tol = 2e-6 m. 0.001 m > 2e-6 m -> Violated!
    assert is_violated_b


# ===========================================================================
# 5. Decoupled Solver Claims vs Independent Verification Certificates
# ===========================================================================


def test_solver_fabric_result_decoupled_verification() -> None:
    """Verifies SolverFabricResult decouples solver claims from independent verification."""
    result = SolverFabricResult(
        jobId="job-parallel-pump-001",
        problemId="prob-parallel-pump",
        schemaVersion="1.1.0",
        completedAt="2026-11-21T23:55:00.000Z",
        totalElapsedMilliseconds=1850,
        solverStatus=SolverTerminationInfo(
            engine="HiGHS v1.7.2",
            terminationStatus="SOLVER_CLAIMED_OPTIMAL",
            terminationReason="OPTIMAL_SOLUTION_FOUND",
            solveTimeWallSeconds=1.24,
            solveTimeCpuSeconds=2.35,
            iterationsCount=340,
            branchAndBoundNodes=38,
        ),
        candidateSolution=CandidateSolution(
            status="FEASIBLE_CANDIDATE_PRESENT",
            objectiveValues=[
                {"id": "OBJ-01", "direction": "minimize", "value": 42.18, "unit": "USD"}
            ],
            variableAssignments=[
                {"variableId": "VAR-01", "symbol": "q_{1,1}", "value": 85.4, "unit": "m3/h"},
                {
                    "variableId": "VAR-02",
                    "symbol": "u_{1,1}",
                    "value": 1.0,
                    "unit": "dimensionless",
                },
            ],
        ),
        feasibilityVerification=FeasibilityVerificationResult(
            verified=True,
            status="VERIFIED_FEASIBLE",
            maxAbsoluteResidual=0.0,
            maxScaledResidual=0.0,
            scaledResidualToleranceUsed=1e-6,
            violatedConstraintsCount=0,
            worstViolatedConstraintId=None,
            constraintEvaluations=[
                ConstraintEvaluation(
                    constraintId="CON-01",
                    lhsValue=85.4,
                    relation=">=",
                    rhsValue=38.0,
                    slack=47.4,
                    scaledResidual=0.0,
                    isViolated=False,
                    isBinding=False,
                )
            ],
        ),
        optimalityVerification=OptimalityVerificationResult(
            verified=True,
            status="CERTIFIED_OPTIMAL",
            primalObjective=42.18,
            dualBound=42.18,
            absoluteGap=0.0,
            relativeGap=0.0,
            optimalityToleranceUsed=1e-4,
            verificationFamily="MILP_DUAL_BOUND_CHECK",
            isIndependentlyVerifiable=True,
        ),
        verificationCertificate=VerificationCertificate(
            certificateId="CERT-20261121-001",
            verifierEngine="Core Independent Residual Checker v1.3.0",
            certificateSha256="d8a291f07b1c4e9f3b2a18e9c7d6e5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d9e8",
            verifiedAt="2026-11-21T23:55:00.100Z",
        ),
        governanceReview=GovernanceReview(
            humanApprovalRequired=True,
            approvalReason="Dispatch requires operator acknowledgment",
            unconfirmedAssumptionsCount=0,
            epistemicSummary=(
                "Mathematical optimality certified. Physical validity relies on EV-DOC-01."
            ),
        ),
    )

    dumped = result.model_dump(by_alias=True)
    assert dumped["solverStatus"]["terminationStatus"] == "SOLVER_CLAIMED_OPTIMAL"
    assert dumped["optimalityVerification"]["status"] == "CERTIFIED_OPTIMAL"
    assert dumped["optimalityVerification"]["isIndependentlyVerifiable"] is True
    assert dumped["verificationCertificate"]["certificateId"] == "CERT-20261121-001"


def test_cp_sat_solver_claim_vs_verification_status() -> None:
    """Verifies CP-SAT is recorded as OPTIMAL_WITHIN_GAP with isIndependentlyVerifiable=False."""
    result = SolverFabricResult(
        jobId="job-cpsat-scheduling-001",
        problemId="prob-batch-schedule",
        schemaVersion="1.1.0",
        completedAt="2026-11-21T23:55:00.000Z",
        totalElapsedMilliseconds=920,
        solverStatus=SolverTerminationInfo(
            engine="OR-Tools CP-SAT v9.8",
            terminationStatus="SOLVER_CLAIMED_OPTIMAL",
            terminationReason="OPTIMAL_BOUND_REACHED",
            solveTimeWallSeconds=0.88,
            solveTimeCpuSeconds=0.88,
            iterationsCount=1200,
            branchAndBoundNodes=0,
        ),
        feasibilityVerification=FeasibilityVerificationResult(
            verified=True,
            status="VERIFIED_FEASIBLE",
            maxAbsoluteResidual=0.0,
            maxScaledResidual=0.0,
        ),
        optimalityVerification=OptimalityVerificationResult(
            verified=True,
            status="OPTIMAL_WITHIN_GAP",
            primalObjective=140.0,
            dualBound=140.0,
            absoluteGap=0.0,
            relativeGap=0.0,
            verificationFamily="CP_SAT_BEST_BOUND_AUDIT",
            isIndependentlyVerifiable=False,  # SAT conflict proofs are solver-internal
        ),
    )

    assert result.solver_status.termination_status == "SOLVER_CLAIMED_OPTIMAL"
    assert result.optimality_verification.status == "OPTIMAL_WITHIN_GAP"
    assert result.optimality_verification.is_independently_verifiable is False


def test_continuous_nlp_certified_as_first_order_kkt_point() -> None:
    """Verifies continuous NLP local solutions are certified strictly as FIRST_ORDER_KKT_POINT."""
    result = SolverFabricResult(
        jobId="job-ipopt-heat-exchanger-001",
        problemId="prob-heat-exchanger",
        schemaVersion="1.1.0",
        completedAt="2026-11-21T23:55:00.000Z",
        totalElapsedMilliseconds=3200,
        solverStatus=SolverTerminationInfo(
            engine="Ipopt v3.14.14",
            terminationStatus="SOLVER_CLAIMED_LOCALLY_OPTIMAL",
            terminationReason="CONVERGENCE_TO_FIRST_ORDER_STATIONARITY",
            solveTimeWallSeconds=3.12,
            solveTimeCpuSeconds=3.12,
            iterationsCount=45,
            branchAndBoundNodes=0,
        ),
        feasibilityVerification=FeasibilityVerificationResult(
            verified=True,
            status="VERIFIED_FEASIBLE",
            maxAbsoluteResidual=1.2e-7,
            maxScaledResidual=1.2e-7,
        ),
        optimalityVerification=OptimalityVerificationResult(
            verified=True,
            status="FIRST_ORDER_KKT_POINT",  # Certified strictly as first-order KKT
            primalObjective=842.15,
            kktStationarityResidual=4.5e-7,
            complementarityResidual=2.1e-8,
            constraintQualificationStatus="LICQ_SATISFIED",
            verificationFamily="NLP_KKT_RESIDUAL_AUDIT",
            isIndependentlyVerifiable=True,
        ),
    )

    assert result.optimality_verification.status == "FIRST_ORDER_KKT_POINT"
    assert result.optimality_verification.kkt_stationarity_residual == 4.5e-7
    assert result.optimality_verification.complementarity_residual == 2.1e-8


def test_infeasibility_evidence_iis_subsystem() -> None:
    """Verifies InfeasibilityEvidence modeling for Irreducible Infeasible Subsystem (IIS)."""
    evidence = InfeasibilityEvidence(
        certificateType="iis_subsystem",
        isIndependentlyVerifiable=True,
        conflictingConstraintIds=["CON-01", "CON-04"],
        explanation="Constraint CON-01 (q >= 38) contradicts CON-04 (max allowable flow q <= 30).",
    )

    assert evidence.certificate_type == "iis_subsystem"
    assert evidence.conflicting_constraint_ids == ["CON-01", "CON-04"]
    assert evidence.is_independently_verifiable is True


# ===========================================================================
# 6. Solver Fabric Job Lifecycle Envelope Tests
# ===========================================================================


def test_solver_fabric_job_envelope_creation() -> None:
    """Verifies SolverFabricJob envelope holding GeneralizedProblemSpec and execution config."""
    problem = adapt_problem_spec_v1_0_to_v1_1(build_research_lab_v1_0_sample_payload())

    job = SolverFabricJob(
        jobId="job-20261121-001",
        problemId="prob-pump-station",
        targetAssetId="asset-kestrel-pump-station",
        tenantId="tenant-industrial-demo",
        status="QUEUED",
        createdAt="2026-11-21T23:55:00.000Z",
        executionConfig=SolverExecutionConfig(
            targetSolverId="highs",
            timeoutSeconds=60.0,
            maxMemoryMb=4096,
            optimalityTolerance=1e-4,
            feasibilityTolerance=1e-6,
        ),
        problemSpec=problem,
    )

    assert job.status == "QUEUED"
    assert job.execution_config.timeout_seconds == 60.0
    assert job.execution_config.max_memory_mb == 4096
    assert job.problem_spec.title == "Parallel Pump Station Energy Minimization"

    dumped = job.model_dump(by_alias=True)
    assert dumped["jobId"] == "job-20261121-001"
    assert dumped["executionConfig"]["maxMemoryMb"] == 4096
    assert dumped["problemSpec"]["schemaVersion"] == "1.1.0"
