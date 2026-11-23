# QORVEXA

**Industrial AI Research Lab**  
*Industrial Decision Intelligence Platform*

> **"From Industrial Complexity to Verified Decisions."**

[![Live Demo](https://img.shields.io/badge/Live%20Platform-qorvexa--delta.vercel.app-0ea5e9?style=flat-square)](https://qorvexa-delta.vercel.app)
[![Architecture Status](https://img.shields.io/badge/Architecture-Option%20B%20Verified-22c55e?style=flat-square)](docs/qorvexa/)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-19-61DAFB?style=flat-square&logo=react&logoColor=black)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.7+-3178C6?style=flat-square&logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![License](https://img.shields.io/badge/License-Proprietary-red?style=flat-square)](#)

---

## 1. Executive Overview

**QORVEXA** transforms complex industrial problems into explainable, mathematically grounded, independently verified decisions through AI-assisted problem understanding, specialized solvers, simulation, and governed decision workflows.

Industrial facilities face high-stakes operational trade-offs across asset maintenance, energy efficiency, production scheduling, and component degradation. Pure Large Language Models (LLMs) hallucinate constraints and cannot provide mathematical optimality proofs. Conversely, classical Operations Research (OR) and simulation tools lack contextual agility, struggle with unstructured plant documentation, and remain opaque to operations engineers.

QORVEXA bridges this divide:
- **LLMs interpret, formulate, and coordinate** — they never serve as the unverified source of mathematical truth.
- **Specialized solvers execute** — numerical solutions derive from dedicated, isolated mathematical engines (e.g., HiGHS continuous LP).
- **Independent verification validates** — candidate solver solutions must pass rigorous mathematical feasibility and certificate checks before reaching human engineers.
- **Strict safety governance** — QORVEXA is **100% advisory and read-only**; it never writes to PLCs, changes setpoints, or actuates machinery.

```
SENSE → VALIDATE → UNDERSTAND → RETRIEVE → DIAGNOSE → ASSESS UNCERTAINTY → 
FORMULATE → SELECT METHOD → SOLVE → SIMULATE → VERIFY → ASSESS TRADE-OFFS → 
HUMAN REVIEW → VERIFY OUTCOME → LEARN
```

---

## 2. Core Architecture

QORVEXA decouples contextual understanding, mathematical solving, and safety verification into four specialized platform engines, a multi-paradigm Solver Fabric, and an independent verification layer.

```mermaid
flowchart TD
    subgraph INGRESS["1. Telemetry & Context Ingress"]
        T1["Asset Telemetry (MetroPT-3)"] --> SIG["Signal Integrity & Gating"]
        D1["Shift Logs & OEM Manuals"] --> HYB["Hybrid RAG / Knowledge Graph"]
    end

    subgraph ENGINES["2. Four Intelligence & Orchestration Engines"]
        E1["Ontology & Context Engine"]
        E2["Workflow Orchestration Engine"]
        E3["Decision Intelligence Engine"]
        E4["Governed Agent Engine"]
    end

    subgraph ADAPTIVE["3. Adaptive Problem Solving"]
        MODES{"Paradigm Router"}
        MODES -->|"Default"| M_AUTO["Automatic"]
        MODES -->|"Standard"| M_HYB["Hybrid (LLM + Solvers)"]
        MODES -->|"Math Only"| M_OR["OR-Only"]
        MODES -->|"Qualitative"| M_LLM["LLM-Only"]
        MODES -->|"Safety Critical"| M_EXP["Expert-Controlled"]
    end

    subgraph FABRIC["4. Specialized Solver Fabric"]
        SF1["Mathematical & Numerical"]
        SF2["Optimization & Operations Research"]
        SF3["Constraint Solving"]
        SF4["Statistical & Data-Driven (PCA-SPE)"]
        SF5["Causal & Graph-Based (RCA)"]
        SF6["Physics & Simulation (Compressor CDT)"]
        SF7["Rule-Based Safety Envelopes"]
        
        HIGHS["Isolated HiGHS Worker Process<br/>(Win32 Job Object Enclosure)"]
        SF1 -.-> HIGHS
        SF2 -.-> HIGHS
    end

    subgraph VERIFICATION["5. Independent Verification Layer"]
        CAND["Solver Candidate Vector (x*, y*)"] --> VERIF{"Independent Verifier"}
        CANON["Canonical LP / IR Digest"] --> VERIF
        VERIF -->|"Pass: Proofs Validated"| PROOF["Verified Evidence Package"]
        VERIF -->|"Fail: Infeasible/Unbounded"| REJ["Rejection & Refusal Notice"]
    end

    subgraph GOVERNANCE["6. Human Advisory & Governance"]
        PROOF --> IMPACT["Pareto Impact & Trade-Offs"]
        IMPACT --> HUMAN(("Human Engineer Review"))
        HUMAN -->|"Sign-off"| AUDIT["Append-Only SHA-256 Audit Chain"]
        HUMAN -->|"Observation"| OUTCOME["Outcome Verification Ledger"]
    end

    INGRESS --> ENGINES
    ENGINES --> ADAPTIVE
    ADAPTIVE --> FABRIC
    FABRIC --> VERIFICATION
    VERIFICATION --> GOVERNANCE
```

---

## 3. Four Intelligence & Orchestration Engines

The platform is driven by four dedicated engines that manage context, coordinate workflows, evaluate trade-offs, and enforce safety:

| Engine | Primary Responsibility | Key Capabilities |
| :--- | :--- | :--- |
| **1. Ontology & Context Engine** | Grounding physical assets and operating context | Digital Asset Passports, physical sensor topologies, operating regime identification (loaded vs unloaded), and component relationship trees. |
| **2. Workflow Orchestration Engine** | Deterministic pipeline state execution | Multi-stage lifecycle execution, worker timeout watchdogs, memory commit limits, error handling, and clean fallback routing. |
| **3. Decision Intelligence Engine** | Multi-objective trade-off evaluation | Pareto frontier extraction (maintenance cost vs downtime risk vs energy penalty), uncertainty calibration, and economic consequence estimation. |
| **4. Governed Agent Engine** | Safe, auditable AI agency | Sandboxed tool calling, epistemic labeling (facts vs assumptions), explicit human review checkpoints, and immutable cryptographic audit logging. |

---

## 4. The Specialized Solver Fabric

The **Solver Fabric** is QORVEXA's modular computational ecosystem. Rather than relying on a single monolithic optimizer, the fabric provides specialized engines behind typed runtime contracts:

```mermaid
classDiagram
    class SolverFabric {
        +Mathematical_and_Numerical()
        +Optimization_and_Operations_Research()
        +Constraint_Solving()
        +Statistical_and_Data_Driven()
        +Causal_and_Graph_Based()
        +Physics_and_Simulation()
        +Rule_Based_Solving()
    }
    class HiGHSAdapter {
        +name: str = "highs"
        +solve(request: WorkerRequest) WorkerResponse
    }
    class Win32JobObject {
        +assign_process(proc)
        +set_memory_limit(4GB)
        +kill_on_close()
    }
    class IndependentVerifier {
        +verify_primal_feasibility()
        +verify_dual_feasibility()
        +verify_duality_gap()
        +verify_farkas_certificate()
    }
    SolverFabric --> HiGHSAdapter
    HiGHSAdapter --> Win32JobObject
    HiGHSAdapter ..> IndependentVerifier : candidate output
```

1. **Mathematical & Numerical**: Exact fraction arithmetic (`Fraction`), arbitrary-precision bounds, bounded continuous LP, and HiGHS simplex/IPM execution.
2. **Optimization & Operations Research**: Continuous linear programming, capacity-constrained production scheduling, resource allocation, and multi-scenario optimization.
3. **Constraint Solving**: Temporal intervals, operating regime rules, Boolean satisfiability, and multi-variable bound conflict detection.
4. **Statistical & Data-Driven**: PCA with Squared Prediction Error (PCA-SPE), Out-of-Distribution (OOD) abstention probes, and conformal prediction bands.
5. **Causal & Graph-Based**: Root Cause Analysis (RCA), causal Bayesian graphs, physical topology traversals, and fault propagation trees.
6. **Physics & Simulation**: First-principles compressor thermo-pneumatics (MetroPT-3), fluid pressure decay models, and synthetic benchmark degradation (AI4I 2020).
7. **Rule-Based Solving**: Deterministic engineering safety limits, ISO/IEC maintenance envelopes, and fail-safe interlocks.

---

## 5. Adaptive Multi-Paradigm Problem-Solving

QORVEXA operates across five distinct problem-solving paradigms to adapt to real-world industrial constraints:

- **Automatic (Default)**: Fully autonomous problem intake, regime detection, solver dispatch, and multi-tier verification. Recommended for standard production operations.
- **Hybrid (LLM + Solvers)**: LLMs extract constraints, parameters, and business goals from unstructured shift logs, compiling them into canonical formulations for mathematical solvers.
- **OR-Only**: Bypasses generative AI entirely. Ingests structured telemetry directly into canonical LP formulations with exact arithmetic and deterministic guarantees.
- **LLM-Only**: Engaged only when telemetry is unobservable or problems cannot be mathematically grounded. Provides qualitative synthesis with explicit non-mathematical epistemic warnings.
- **Expert-Controlled**: Senior plant engineers explicitly specify solver algorithms, tolerance bounds, objective weights, and verification rules for safety-critical plant workflows.

---

## 6. End-to-End Decision Workflow

```mermaid
flowchart LR
    S1["01 Sense"] --> S2["02 Validate"]
    S2 --> S3["03 Understand"]
    S3 --> S4["04 Retrieve"]
    S4 --> S5["05 Diagnose"]
    S5 --> S6["06 Uncertainty"]
    S6 --> S7["07 Formulate"]
    S7 --> S8["08 Select Method"]
    S8 --> S9["09 Solve"]
    S9 --> S10["10 Simulate"]
    S10 --> S11["11 Verify"]
    S11 --> S12["12 Trade-offs"]
    S12 --> S13["13 Human Review"]
    S13 --> S14["14 Verify & Learn"]

    classDef stage fill:#111a2c,stroke:#1f2a40,stroke-width:1px,color:#e6ebf5;
    classDef keyStage fill:#0b1220,stroke:#3b82f6,stroke-width:2px,color:#3b82f6;
    class S1,S2,S3,S4,S5,S6,S7,S8,S10,S12,S14 stage;
    class S9,S11,S13 keyStage;
```

1. **Sense**: Stream high-frequency telemetry (air pressure, motor current, oil temperature) and operational events.
2. **Validate**: Signal integrity filtering, sensor range checks, and operating regime separation (loaded vs unloaded).
3. **Understand**: Contextual semantic extraction from maintenance logs and shift handover records.
4. **Retrieve**: Hybrid RAG querying across engineering manuals, OEM schematics, and incident histories.
5. **Diagnose**: Multi-hypothesis Root Cause Analysis (RCA) ranking physical failure mechanisms.
6. **Assess Uncertainty**: OOD abstention scoring and telemetry calibration confidence checks.
7. **Formulate**: Compile business and physical requirements into canonical mathematical models (`CanonicalLP`).
8. **Select Method**: Determine the appropriate paradigm and solver engine based on tractability and constraints.
9. **Solve**: Execute optimization in an isolated child subprocess under strict resource limits.
10. **Simulate**: Evaluate candidate outcomes through physical Digital Twin simulation models.
11. **Verify**: Independent mathematical verification of primal feasibility, dual bounds, and complementarity.
12. **Assess Impact & Trade-offs**: Multi-objective Pareto evaluation across financial cost, reliability, and downtime.
13. **Human Review**: Mandatory advisory presentation to authorized plant engineers for review and sign-off.
14. **Verify Outcome & Learn**: Append-only outcome ledger comparing actual maintenance findings to diagnoses.

---

## 7. First Implemented Domain Vertical: Production Planning

The first production-grade domain vertical implemented on the Solver Fabric is continuous **Production Planning**:

- **Continuous Decision Quantities**: Models production volumes ($x_j \ge 0$) across continuous multi-product lines.
- **Resource Capacities & Consumption**: Enforces shared resource limits (labor, machine runtime, raw materials):
  $$\sum_{j} A_{i,j} x_j \le b_i \quad \forall i \in \text{Resources}$$
- **Market Demand Bounds**: Lower contract commitments and upper demand absorption caps ($d_j^{\min} \le x_j \le d_j^{\max}$).
- **Dual Business Objectives**: Margin maximization ($\max \sum m_j x_j$) or unit production cost minimization.
- **Deterministic Compilation**: Compiles domain entities to canonical CSR format with deterministic IR SHA-256 digests.
- **Traceability Manifest**: Bidirectional mapping linking business entity names directly to matrix row/column indices.
- **Independent Scenarios**: Capacity expansion, demand surge, and supply disruption counterfactuals.
- **Evidence-Gated Reports**: Analytical report generation is strictly gated by trusted solver verification evidence.
- **Verification Evidence**: **64 dedicated unit/integration tests passed** and **220 combined tests passed**.

---

## 8. Industrial Language Understanding & Factory Terminology

To bridge colloquial shop-floor communication and formal optimization models, QORVEXA implements a 13-stage deterministic, bounded language intelligence pipeline (`qorvexa.industrial.understanding`):

- **Multilingual Factory Intake**: Direct support for Egyptian Colloquial Arabic (`عامية مصري`), Modern Standard Arabic (`فصحى`), English, shop-floor code-switching, and Arabizi numerals and terminology.
- **Quote-Grounded Claims**: Every extracted parameter, constraint, and capacity is strictly bound to verbatim character offsets within the source shift notes; fabricated, ungrounded, or distorted claims are marked `UNSUPPORTED` or `CONFLICTED`.
- **Contradiction & Conflict Detection**: Evaluates numeric assertions within identical operational scopes (converting dimensional units exactly), surfacing mathematical and physical contradictions prior to formulation.
- **Factory-Scoped Versioned Terminology**: Plant jargon, equipment aliases, and local product names are managed in factory-isolated dictionaries (`TerminologyBook`). Changes require an authorized human `TERMINOLOGY_REVIEWER`, appending immutable version events with full audit tracking.
- **Untrusted Proposals Pipeline**: Extracted operational claims are emitted exclusively as untrusted `PROPOSAL` entries into the Fact Ledger (`emit_proposals`). LLMs cannot confirm facts, alter models, or trigger optimization runs autonomously.
- **Interactive Clarification Loop**: Automatically identifies missing parameters (capacity gaps, undefined cost metrics) and prompts plant operators in their native language and dialect with targeted, non-duplicative questions.

---

## 9. Accurate Implementation Status

To preserve engineering integrity, QORVEXA explicitly demarcates verified code from active work and planned architecture:

| Component | Status | Test / Evidence Reference |
| :--- | :---: | :--- |
| **Solver Fabric Runtime Contracts** | **Implemented & Tested** | `tests/test_sf_runtime_contracts.py`, `test_sf_canonical_lp.py` (593 tests passed) |
| **Exact Arithmetic & Math Hardening** | **Implemented & Tested** | `tests/test_sf_exact.py`, `test_sf_math_hardening.py` |
| **HiGHS Worker & Adapter (Win32 Isolated)** | **Implemented & Tested** | `tests/test_sf_highs_adapter.py` (21/21 passed; Win32 Job Object, 4GB cap) |
| **Independent Mathematical Verifier** | **Implemented & Tested** | `tests/test_sf_verifier.py` (primal/dual feasibility, duality gap, Farkas rays) |
| **Production Planning Formulation Slice** | **Implemented & Tested** | `tests/test_production_planning.py` (64/64 dedicated passed; 220 combined) |
| **Industrial Language Understanding** | **Implemented & Tested** | `tests/test_industrial_understanding.py` (43/43 passed; 13-stage pipeline) |
| **Factory Terminology Lifecycle & Isolation** | **Implemented & Tested** | `tests/test_industrial_understanding_api.py` (11/11 passed; SQLite versioned store) |
| **Dimensional Units Verification Engine** | **Implemented & Tested** | `tests/test_industrial_units.py` (64/64 passed; dimensional checking) |
| **Fact Ledger & Governed Sessions (SQLite WAL)** | **Implemented & Tested** | `tests/test_governance.py`, `test_formulation.py` (tamper-evident hash chain) |
| **Decision Intelligence & Pareto Frontier** | **Implemented & Tested** | `tests/test_decision_intelligence*.py` (multi-scenario ranking & trade-offs) |
| **Phase B End-to-End Orchestrator Binding** | **Implemented & Tested** | `tests/test_sf_phase_b_integration.py` (21/21 passed) |
| **Live HiGHS Solves on Production Planning** | **Implemented & Tested** | `tests/test_industrial_api_e2e.py` (verified with real HiGHS subprocess) |
| **MetroPT-3 Real Telemetry Anomaly Detection** | **Implemented & Tested** | `tests/test_anomaly_*.py` (4/4 operator leak episodes detected; 0.026 FA/day) |
| **Cryptographic Audit Trail (SHA-256)** | **Implemented & Tested** | `tests/test_audit_contention.py` (tamper-evident hash chain verification) |
| **Safety Boundary Enforcement** | **Implemented & Tested** | `tests/test_safety_boundary.py` (enforces zero PLC/SCADA write endpoints) |
| **Public Platform Preview UI & Planning Studio** | **Implemented & Deployed** | Deployed on Vercel: [qorvexa-delta.vercel.app](https://qorvexa-delta.vercel.app) |
| **Multi-Solver Concurrent Arbitration** | *Planned* | Concurrent dispatch across MILP, CSP, and numerical engines |
| **Deep Fluid-Thermal Digital Twin Coupling** | *Planned* | Dynamic coupling between continuous optimizer and non-linear simulator |
| **Industrial OT Connectors (OPC-UA/MQTT)** | *Planned* | Industrial client libraries and live plant historian streaming |

---

## 9. Safety & Governance Principles

1. **Advisory and Read-Only**: QORVEXA never executes autonomous machine actuations, setpoint changes, or PLC writes.
2. **LLMs Are Not Mathematical Solvers**: Generative models structure and explain problems; mathematical truth is computed solely by specialized solvers and validated by independent verifiers.
3. **Independent Verification**: Solver candidate vectors ($x^*, y^*$) are strictly separated from verified evidence. Unverified candidates are never displayed as ground truth.
4. **Epistemic Clarity**: Confirmed physical telemetry facts, mathematical proofs, calibrated statistical estimates, and engineering assumptions are visibly distinguished.
5. **Tamper-Evident Auditability**: Every recommendation, constraint modification, and human sign-off is hashed into an append-only SHA-256 audit ledger.

---

## 10. Repository Layout

```
QORVEXA/
├── backend/
│   ├── src/qorvexa/
│   │   ├── anomaly/              # MetroPT-3 anomaly detection & PCA-SPE detectors
│   │   ├── api/                  # FastAPI service (advisory endpoints, security, health)
│   │   ├── assets/               # Digital Asset Passports & sensor manifests
│   │   ├── contracts/            # Frozen runtime contracts (solver_runtime, solver_fabric)
│   │   ├── industrial/
│   │   │   └── production_planning/ # Continuous production planning formulation slice
│   │   ├── solver_fabric/        # Canonical LP, HiGHS adapter, worker process, verifier
│   │   └── twin/                 # Compressor physics & degradation simulation models
│   ├── tests/                    # 1,800+ hermetic backend tests (pytest)
│   └── pyproject.toml            # Dependencies (FastAPI, NumPy, SciPy, highspy==1.16.0)
├── frontend/
│   ├── src/
│   │   ├── auth/                 # Enterprise OIDC provider & strict authentication gate
│   │   ├── features/
│   │   │   ├── public/           # Public product & architecture landing page
│   │   │   ├── solver/           # Solver workspace & problem formulation wizard
│   │   │   ├── anomaly/          # Real-time MetroPT-3 telemetry inspection
│   │   │   ├── twin/             # Digital Twin simulator interface
│   │   │   └── incidents/        # Incident investigation & human outcome verification
│   │   └── routes/               # TanStack Router page layouts & control room shell
│   └── package.json              # React 19, TypeScript 5.7, Tailwind CSS v4, Vite 8
└── docs/
    └── qorvexa/                  # Architectural briefs, formulation design, evaluation strategies
```

---

## 11. Quick Start (Local Development)

### Prerequisites
- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- Node.js 24+ and npm

### Backend Setup
```bash
# Clone the repository
git clone https://github.com/Mohamed-Osama-ai7/QORVEXA.git
cd QORVEXA/backend

# Install dependencies and sync virtual environment
uv sync

# Run the complete test suite (includes Solver Fabric and HiGHS tests)
uv run pytest

# Launch the advisory FastAPI backend
uv run uvicorn qorvexa.api.app:app --host 127.0.0.1 --port 8000 --reload
```

### Frontend Setup
```bash
cd ../frontend

# Install dependencies
npm ci

# Run unit and integration tests
npm test

# Start the Vite development server
npm run dev
# Operational preview available at http://localhost:5173
```

---

## 12. Deployment & Live Platform Preview

- **Public Preview URL**: [https://qorvexa-delta.vercel.app](https://qorvexa-delta.vercel.app)
- **Deployment Platform**: Vercel (Edge Network / Vite SPA)
- **Public Mode Capabilities**: Verified architectural showcase, multi-paradigm router details, Solver Fabric specification, 14-stage workflow walkthrough, Production Planning formulation review, and real-time implementation status matrix.
- **Enterprise Mode**: Live industrial telemetry, asset control rooms, and incident audit workflows require an enterprise OIDC Identity Provider (Keycloak / Okta / Azure AD).

---

## 13. Team & Research Direction

- **Project Name**: QORVEXA
- **Descriptor**: Industrial AI Research Lab
- **Product Category**: Industrial Decision Intelligence Platform
- **Technical Architecture & Direction**: Mohamed Osama
- **Affiliation**: Industrial Decision Intelligence Research, Cairo, Egypt
- **Repository**: [https://github.com/Mohamed-Osama-ai7/QORVEXA](https://github.com/Mohamed-Osama-ai7/QORVEXA)
