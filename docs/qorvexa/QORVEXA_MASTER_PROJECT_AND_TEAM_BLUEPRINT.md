# QORVEXA — Master Project History, Technical Blueprint & Team Architecture

**Document Identifier:** `QORVEXA-MASTER-BLUEPRINT-2026.1`  
**Effective Baseline Snapshot:** November 23, 2026 (Dated Implementation Snapshot)  
**Repository:** `https://github.com/Mohamed-Osama-ai7/QORVEXA`  
**Audited Branch Baseline:** `origin/collaboration/qorvexa-current` (Baseline commit `21f6ebdbc1bc6a80004056fe6988e3dbdc4dbba8`)  
**Governance & Documentation Branch:** `docs/qorvexa/master-project-blueprint` (Derived from governance commit `73872cc37f9a1f5a1a87a7ea8a5a0c1ef21a8d7b`)  
**Verified Test Suite Snapshot:** 1,924 Backend Hermetic Tests across 114 test files · 75 Frontend Vitest Tests across 20 test files · 0 Failures  
**License Status by Branch:** Collaboration branch (`collaboration/qorvexa-current`) is Proprietary / All Rights Reserved; `main` branch retains legacy MIT license; public forks hold historical snapshot terms (See [LICENSING_TRANSITION.md](LICENSING_TRANSITION.md))  
**Unmerged Feature Work Isolation Notice:** The separate Multi-Worker Solver Pool commit `632d626` (`feat(solver): add bounded multi-worker execution pool`), developed on feature branch `feat/mohamed/multi-worker-solver-pool`, is **not** included in this blueprint's verified baseline. It is tracked independently and is neither integrated nor merged into `collaboration/qorvexa-current`.

---

## Executive Overview & Non-Negotiable Epistemic Laws

**QORVEXA** is an **Industrial Decision Intelligence Platform** developed by the **QORVEXA Research Lab**. The platform solves a fundamental breakdown in modern manufacturing: the chasm between colloquial, dialectal operational reality on the factory floor and rigorous, mathematically certified optimization in enterprise management systems.

Unlike generic enterprise AI or predictive maintenance dashboards that generate unverified text or passive anomaly charts, QORVEXA enforces an end-to-end, closed-loop pipeline from dialectal factory language intake (Egyptian Arabic `عامية مصري`, Arabizi, shift notes) to verified mathematical formulations, isolated solver execution, physics-informed discrete-event simulation, and tamper-evident human governance.

### The Five Epistemic Laws of QORVEXA
1. **Separation of Cognitive and Numerical Labor:** Large Language Models (LLMs) formulate hypotheses and extract language structures; dedicated mathematical engines compute numbers; decoupled independent verifiers certify solutions; authenticated humans govern operational decisions.
2. **Immutability of the Unverified State:** `mathematicallyVerified: false` is an immutable constant during AI formulation. Under no circumstances may an LLM, prompt instruction, or heuristic agent certify mathematical correctness.
3. **Solution Proof $\ne$ Model Fidelity:** Proving that a candidate solution vector $x^*$ satisfies an optimization model $\mathcal{M}$ does **not** prove that $\mathcal{M}$ represents the physical factory. Model fidelity is an empirical engineering responsibility grounded in validated physical evidence and simulation stress-testing.
4. **Adaptive Multi-Paradigm Routing:** Real industrial problems cannot be forced into a single mathematical matrix. Problems must be adaptively classified and routed to the proper solver family (Continuous LP, Native MILP, CP-SAT, Discrete-Event Simulation, or Diagnostic RCA).
5. **Zero-Trust Solver Verification:** A solver's self-reported exit code (e.g., `OPTIMAL`) is **never** accepted as ground truth. Feasibility and optimality must be independently audited using double-precision IEEE 754 arithmetic and dimensionally scaled tolerances.

---

## 1. Evolution: FactoryMind CDT to QORVEXA

### 1.1 Academic Origins (Fall 2026)
The project originated under the name **FactoryMind CDT** (Cognitive Digital Twin / Causal Digital Twin), conceived as an applied research initiative in industrial AI and cyber-physical systems. The initial project scope, documented in the archival paper [FactoryMind_CDT_Project_Documentation_with_UI_UX.pdf](../reference/FactoryMind_CDT_Project_Documentation_with_UI_UX.pdf), proposed combining predictive maintenance on heavy industrial rotating equipment (illustrated by an operational centrifugal pump `M-004`) with causal reasoning and digital twin modeling.

### 1.2 The M0–M5 Milestone Progression
Under the technical direction of Lead Architect Mohamed Osama (`@Mohamed-Osama-ai7`), the platform transitioned from an academic concept into an operational, modular software platform across six major milestones:

| Milestone | Branch / PR | Primary Capabilities Delivered | Status |
|---|---|---|:---:|
| **M0: Engineering Foundation** | PR #1 (`666a956`) | Modular FastAPI backend, React/TypeScript SPA, CI pipeline with pinned action SHAs, read-only OT safety boundary. | **IMPLEMENTED** |
| **M1: MetroPT-3 Anomaly Pipeline** | PR #2 (`1f9a893`) | Ingestion and unsupervised anomaly detection on UCI Dataset #791 (MetroPT-3 APU compressor), `/score` and `/replay` APIs, React telemetry monitor. | **IMPLEMENTED** |
| **M2-WP0: Detection Hardening** | PR #4 (`e4e17f9`) | Nested Leave-One-Event-Out (LOEO) cross-validation protocol, physics-informed `duty_rule` baseline, `ExceedanceCalibrator`, stateful `AlarmTracker`. | **IMPLEMENTED** |
| **M2-WP1: Auth & Persistence** | PR #5 (`ba7ac37`) | OIDC resource-server authentication, 5-role RBAC, SQLite append-only persistence with SHA-256 tamper-evident hash chaining. | **IMPLEMENTED** |
| **M2-WP2: Operating Regimes & Gating** | PR #6 (`03c3fd5`) | Causal 4-regime classifier (`stopped`, `unloaded`, `loaded`, `starting`), 4-class event taxonomy, sensor data integrity gating (`stale_telemetry`, `signal_inconsistency`). | **IMPLEMENTED** |
| **M2-WP3: Event Evidence Packages** | PR #7 (`8ad761a`) | Grouped event evidence foundations, structured physical observation logging, timestamp alignment. | **IMPLEMENTED** |
| **M2-WP4: Explainable Physical RCA** | PR #8 (`004cb9a`) | Directed Acyclic Graph (DAG) causal traversal, symptom-cause hypothesis ranking, physical failure propagation analysis. | **IMPLEMENTED** |
| **M2-WP5: Decision Workspace** | PR #9 (`aa2a50b`) | Human decision review workspace, multi-action comparison, trade-off matrix, outcome verification logging. | **IMPLEMENTED** |
| **M3-WP0: Asset Digital Passport** | PR #10 (`0744c2d`) | Hardened digital asset passport (`passport.py`), nominal operating envelopes, physical sensor roles, unobserved quantity boundaries. | **IMPLEMENTED** |
| **M3-WP1: Multi-Model ML & AI4I** | PR #11 (`e58c1e4`) | Multi-model specialist catalog, UCI AI4I 2020 predictive maintenance benchmark, out-of-distribution (OOD) abstention probe. | **IMPLEMENTED** |
| **M3-WP2: Hybrid RAG & Knowledge** | PR #12 (`7842d35`) | In-memory Hybrid RAG (BM25 + dense retrieval), Reciprocal Rank Fusion (RRF), technical PDF parsing. Commit `59c88d01` contributed by Ahmed Essam. | **IMPLEMENTED** |
| **M4-WP0: Discrete-Event Digital Twin** | PR #13 (`e33804b`) | Integer tick event clock, machine micro-states (`PROCESSING`, `STARVED`, `BLOCKED`, `DOWN`), scrap accounting, Monte Carlo pseudo-random streams. | **IMPLEMENTED** |
| **M4-WP1: Impact & Optimization** | PR #14 (`f720dd5`) | Canonical LP compiler, bounded maintenance scheduling, production planning formulation. | **IMPLEMENTED** |
| **M4-WP2: Controlled Industrial Copilot** | PR #15 (`a67542d`) | Read-only copilot runtime, citation grounding, red-team prompt guardrails, zero-actuation enforcement. | **IMPLEMENTED** |
| **M5-WP0: Read-Only OT Connectors** | PR #16 (`77cbeb0`) | Edge connector descriptors for OPC UA, MQTT, and Historians; URL syntax validation, batch preflight check. | **PARTIALLY IMPLEMENTED** |

### 1.3 Re-Branding to QORVEXA & Branch Licensing Discrepancy
In late November 2026, the project underwent a formal strategic re-positioning:
- **Product Name:** **QORVEXA**
- **Descriptor:** **Industrial AI Research Lab**
- **Product Category:** **Industrial Decision Intelligence Platform**
- **Branch-Specific License Divergence:**
  - **`main` Branch:** Retains the historical open-source **MIT License** (`Copyright (c) 2026 FactoryMind CDT Contributors`). As a result, the unauthenticated GitHub REST API reports the repository license as MIT.
  - **`collaboration/qorvexa-current` Branch:** Transitioned in commit `bb04cba` to a closed-source **Proprietary / All Rights Reserved** commercial license (`Copyright (c) 2026 Mohamed Osama / QORVEXA. All rights reserved. PROPRIETARY AND CONFIDENTIAL`).
  - **Historical Snapshot & Fork Implications:** Public forks created during the MIT window (`beshoysaad468-sys/QORVEXA` and `AliMotassem164/FactoryMind-CDT`) and local clones retain MIT rights to historical snapshots up to their respective fork dates. Under copyright law non-retroactivity principles, the repository does not have a single unified license across all branches. This document provides factual repository observations and does not constitute a legal opinion. Full transition details are documented in [LICENSING_TRANSITION.md](LICENSING_TRANSITION.md).

---

## 2. Product Vision & Commercial Strategy

```mermaid
flowchart LR
    Market["Proposed Market Opportunity\n(Food & Beverage · Cable · Chemicals · Process)"] --> Problem["Research Hypothesis\nColloquial Shift Language vs Rigid Solvers\nLLM Hallucinations vs High Safety Risks"]
    Problem --> Qorvexa["QORVEXA Platform Architecture\nGrounded NLP + Unit Validation + Isolated Solvers\nDigital Twin DES + Pareto Decision Intelligence"]
    Qorvexa --> Commercial["Approved Product Direction\nReduced Scrap · Lower Downtime · Certified Plans\nZero Liability (Strictly Advisory / Read-Only)"]
```

### 2.1 Target Industry Verticals (`PROPOSED MARKET OPPORTUNITY`)
The following manufacturing sectors have been identified as proposed commercial opportunities based on architectural alignment; they represent research targets rather than validated customer engagements:
1. **Multi-Stage Dependent Production:** Continuous lines where upstream starvation or downstream blocking cascades across machines (e.g., continuous cable extrusion, beverage packaging).
2. **Shift-Based Dialectal Reporting:** Plants where operators log events in colloquial dialects (e.g., Egyptian Arabic, Arabizi, multilingual shop-floor jargon) that standard ERP/MES systems fail to parse.
3. **Strict Dimensional & Scrap Constraints:** Operations where material wastage, specific energy consumption, and unit mismatches carry heavy financial penalties.
4. **High Downtime Costs:** Facilities where unpredicted compressor or motor failure halts entire production sections ($>\$10,000/\text{hr}$).

### 2.2 Operational Breakdown & Closed-Loop Solution (`RESEARCH HYPOTHESIS`)
The core research hypothesis of QORVEXA posits that combining deterministic dialectal natural language grounding, formal dimensional unit validation, isolated mathematical optimization, and discrete-event simulation into an evidence-justified co-orchestration loop can significantly reduce operational scrap and scheduling downtime compared to standalone heuristics or ungrounded generative AI.

### 2.3 Commercial Operating Model (`APPROVED PRODUCT DIRECTION`)
- **Enterprise Platform Licensing:** Tiered commercial licensing modeled on connected facilities, monitored physical assets, and solver worker concurrency.
- **Factory-Isolated Data Tenancy:** Append-only SQLite WAL ledgers and terminology dictionaries isolated per factory ID. Zero cross-tenant data leakage.
- **Strict Advisory Safety Boundary:** QORVEXA enforces an absolute read-only architectural invariant. The software provides mathematically verified decision packages and recommendations to authorized human managers, completely eliminating physical liability for industrial actuation.
- **Commercial Traction Status:** QORVEXA is currently an advanced research platform and engineering prototype. **There are no active customer contracts, commercial traction, enterprise deployments, or industrial partnerships at this stage.** All operational evaluations rely strictly on peer-reviewed historical datasets and synthetic simulation benchmarks.

---

## 3. Platform Workflows: Conceptual Vision vs. Implemented Baseline

QORVEXA maintains a strict distinction between the complete intended platform conceptual architecture and the currently verified, operational workflow baseline.

### 3.1 Complete Intended Platform Conceptual Pipeline (`TARGET ARCHITECTURAL VISION`)

The complete architectural vision of QORVEXA encompasses a 14-stage lifecycle integrating cognitive text understanding, causal diagnosis, mathematical optimization, physical simulation, and continuous learning:

```mermaid
flowchart TD
    C1["1. Industrial Intake & Signal Capture\n(Dialectal Shift Notes · Voice · Telemetry Anomaly Streams)"] --> C2["2. Evidence Retrieval\n(Hybrid Sparse/Dense RAG · Asset Passports · Maintenance SOPs)"]
    C2 --> C3["3. Dialectal Language Understanding\n(13-Stage Parsing · Quote Grounding · Contradiction Analysis)"]
    C3 --> C4["4. Ontology & Context Resolution\n(Plant Taxonomic Graph · Entity Hierarchy · Scope Binding)"]
    C4 --> C5["5. Causal Diagnosis & Root-Cause Attribution\n(Structural Causal Models · DAG Traversal · Sensor Fault Isolation)"]
    C5 --> C6["6. Uncertainty Assessment & Degradation Modeling\n(Remaining Useful Life · Weibull Curves · Sensor Drift Margins)"]
    C6 --> C7["7. Proposal Generation & Fact Ledger Admission\n(Untrusted Proposals · Cryptographic SHA-256 Hash Chaining)"]
    C7 --> C8["8. Strict Dimensional Units Validation\n([M]^a [L]^b [T]^c [Theta]^d Physical Invariant Verification)"]
    C8 --> C9["9. Canonical Formulation Compilation\n(c^T x · A x <= b · Variable Lower/Upper Bounds)"]
    C9 --> C10["10. Adaptive Solver Selection (AMPPS)\n(Dynamic Problem Classifier · Continuous LP · MILP · CP-SAT)"]
    C10 --> C11["11. Isolated Solver Execution & Decoupled Verification\n(Subprocess Worker · Dual Rays · Duality Gap · Farkas Certificates)"]
    C11 --> C12["12. Discrete-Event Digital Twin Stress-Testing (OSCO)\n(1s Ticks · Micro-States · Stochastic Seeds · Bottleneck Refinement)"]
    C12 --> C13["13. Multi-Criteria Decision Evaluation\n(Exact 50-Digit Decimal Pareto Frontier · Executive Briefs)"]
    C13 --> C14["14. Human Approval, Outcome Verification & Learning\n(Authenticated Sign-Off · Audit Vault · Closed-Loop Bayesian Feedback)"]
```

### 3.2 Currently Implemented End-to-End Workflow (`ACTIVE BASELINE`)

The active baseline implementation deployed in the Production Planning & Decision Studio currently executes an operational 11-stage subset of this pipeline, covered by 1,924 automated tests:

```mermaid
flowchart TD
    I1["1. Factory Language Intake\n(Colloquial Egyptian Arabic · Arabizi · Shift Notes)"] --> I2["2. Language Understanding & Grounding\n(13-Stage Extraction Pipeline · Character Quote Offsets)"]
    I2 --> I3["3. Factory Terminology Resolution\n(Isolated SQLite Dictionary · Versioned Confirmed Terms)"]
    I3 --> I4["4. Untrusted Proposal Generation\n(Tagged Candidate Claims · Evidence Offsets)"]
    I4 --> I5["5. Fact Ledger Admission\n(Append-Only SQLite WAL · SHA-256 Tamper-Evident Hash Chain)"]
    I5 --> I6["6. Strict Dimensional Units Gate\n(Exact Dimensional Analysis · Incompatible Units Blocked)"]
    I6 --> I7["7. Canonical Formulation Compiler\n(Deterministic Mapping to c^T x · A x <= b)"]
    I7 --> I8["8. Core Solver Fabric Execution\n(Isolated HiGHS Subprocess · Win32 Job Object · JSON-IPC)"]
    I8 --> I9["9. Independent Mathematical Verifier\n(Double-Precision Primal/Dual Residuals · Duality Gap · Farkas Rays)"]
    I9 --> I10["10. Digital Twin Simulation & OSCO Loop\n(1s Integer Tick DES · Machine Micro-States · Bottleneck Refinement)"]
    I10 --> I11["11. Decision Package & Human Approval\n(Exact 50-Digit Decimal Pareto Frontier · Executive Brief · Audit Log)"]
```

### 3.3 Stage Mapping & Grouping Analysis

The 11-stage operational baseline reflects a focused realization of the 14-stage conceptual architecture, optimized for industrial production planning in the active Decision Studio:
- **Language & Context Ingestion (Conceptual Stages 1–4 $\to$ Baseline Stages 1–3):** In the active production planning workflow, text intake directly triggers the 13-stage deterministic understanding pipeline (`understanding/`) and factory terminology dictionary (`ontology/`). External technical manual retrieval (RAG, Conceptual Stage 2) is implemented in `knowledge/rag.py` and evaluated on benchmark retrieval cases, but is invoked during documentation lookup rather than the standard production planning shift path.
- **Diagnostic & Degradation Bypass (Conceptual Stages 5–6):** Causal root-cause analysis (Conceptual Stage 5) and degradation/RUL modeling (Conceptual Stage 6) are active in the asset maintenance vertical (evaluated on the MetroPT-3 APU benchmark in `causal/` and `anomaly/`). In the pure production planning studio path, where operational parameters are extracted from shift handoffs, this diagnostic branch is bypassed, routing directly from confirmed facts to formulation compilation.
- **Proposal, Fact Ledger & Dimensional Validation (Conceptual Stages 7–8 $\to$ Baseline Stages 4–6):** Proposal generation, Fact Ledger admission, and dimensional units checking are each maintained as distinct, audited stages in both the conceptual architecture and active baseline.
- **Formulation, Solver Dispatch & Verification (Conceptual Stages 9–11 $\to$ Baseline Stages 7–9):** Formulation compilation, HiGHS solver execution, and decoupled mathematical verification remain separate operational stages. Adaptive solver selection (AMPPS, Conceptual Stage 10) currently dispatches deterministically to the isolated HiGHS 1.16.0 continuous LP worker pending Member 3's MILP/CP-SAT adapters.
- **Simulation, Decision Evaluation & Approval (Conceptual Stages 12–14 $\to$ Baseline Stages 10–11):** The OSCO simulation loop (Stage 10) stress-tests verified plans using discrete-event simulation. Multi-criteria Pareto frontier evaluation, executive trade-off briefing, human approval sign-off, and persistent cryptographic audit logging are integrated into the final Decision Package stage (Stage 11).

---

## 4. The Four Core Platform Engines

QORVEXA is structured around four authoritative platform engines that coordinate specialized analytical, numerical, and simulation subsystems:

```mermaid
flowchart TD
    subgraph E1["1. Ontology & Context Engine"]
        E1_1["Factory Knowledge Fabric"]
        E1_2["Asset Taxonomic Hierarchy"]
        E1_3["Event-Sourced SQLite Graph"]
        E1_4["Entity Mention Resolver"]
    end

    subgraph E2["2. Workflow Orchestration Engine"]
        E2_1["Pipeline State Machine"]
        E2_2["OSCO Solve <--> Simulate Loop"]
        E2_3["Bottleneck Capacity Refiner"]
        E2_4["Candidate Lifecycle & Dedupe"]
    end

    subgraph E3["3. Decision Intelligence Engine"]
        E3_1["Multi-Criteria Pareto Frontier"]
        E3_2["50-Digit Exact Decimal Comparisons"]
        E3_3["Constraint Binding Tracker"]
        E3_4["Executive Decision Packages"]
    end

    subgraph E4["4. Governed Agent Engine"]
        E4_1["Supervisor Agent Runtime"]
        E4_2["Dependency-Aware Task Graph"]
        E4_3["Execution Budgets & Timeouts"]
        E4_4["Red-Team Guardrails & Audit"]
    end

    E1 -->|Context & Hierarchy| E2
    E2 -->|Evaluated Candidates| E3
    E4 -->|Orchestrated Invocations| E2
    E3 -->|Governed Packages| E4
```

### Engine 1: Ontology & Context Engine
- **Package Path:** `backend/src/qorvexa/industrial/ontology/`
- **Active Tests:** 70 tests in `tests/test_industrial_ontology.py`
- **Core Responsibilities:**
  - Maintains the factory knowledge fabric as an event-sourced, append-only SQLite WAL property graph.
  - Enforces versioned concept and relation schemas (24 concepts, 14 relations), including asset containment (`CONTAINS`), equipment feeds (`FEEDS`), and operational scoping.
  - Provides deterministic entity resolution and mention linking, mapping colloquial aliases to canonical plant assets.
  - Maintains temporal validity windows and structural integrity checks without allowing cycles.
- **Key Interfaces:** `Ontology`, `ConceptSchema`, `RelationSchema`, `GraphState`, `FactoryOntology`, `OntologyStore`, `EntityRecord`.

### Engine 2: Workflow Orchestration Engine
- **Conceptual Engine Scope:** Serves as the overarching platform orchestration plane governing end-to-end task progression, pipeline state machines, lifecycle stage transitions, execution budgets, and asynchronous coordination across all platform subsystems.
- **Specialized OSCO Capability:** Within this broader workflow orchestration architecture, **Optimization–Simulation Co-Orchestration (OSCO)** operates as a specialized, high-leverage coordination capability. OSCO specifically closes the feedback loop between mathematical optimization (HiGHS) and discrete-event simulation (DES), detecting machine bottleneck violations and iteratively refining operational capacities.
- **Package Path & Implementation Mapping:**
  - *Platform Workflow Plane (Conceptual & Distributed):* Orchestration state machines and stage transitions are coordinated across API route handlers (`backend/src/qorvexa/api/industrial.py`), pipeline state abstractions, and studio interaction controllers.
  - *Specialized OSCO Package (Implemented):* `backend/src/qorvexa/industrial/co_orchestration/` (24 tests in `tests/test_optimization_simulation_orchestration.py`).
- **Core Responsibilities:**
  - Manages stage transitions across the operational pipeline, enforcing preconditions, execution budgets, and cancellation signals.
  - Coordinates the specialized OSCO loop: dispatches canonical formulations to the Solver Fabric, routes verified primal plans to discrete-event simulation, inspects machine micro-state telemetry (`STARVED`, `BLOCKED`, `DOWN`), and computes bottleneck capacity refinements.
  - Enforces candidate lifecycle rules and cryptographic deduplication (SHA-256 problem/twin hashing).
  - Routes evaluated scenario candidates to the Decision Intelligence Engine.
- **Key Interfaces:** `CoOrchestrator`, `contracts.py`, `optimize.py`, `evaluate.py`, `refine.py`, `decision.py`, `store.py`.

### Engine 3: Decision Intelligence Engine
- **Package Path:** `backend/src/qorvexa/industrial/decision_intelligence/`
- **Active Tests:** 84 tests in `tests/test_decision_intelligence*.py`
- **Core Responsibilities:**
  - Evaluates multi-scenario candidate portfolios across competing economic and physical criteria (throughput, margin, overtime cost, makespan, energy).
  - Calculates non-dominated Pareto frontiers using high-precision 50-digit exact `Decimal` arithmetic.
  - Tracks constraint state transitions (`BECAME_BINDING`, `NO_LONGER_BINDING`) on verified primal vectors.
  - Formulates structured, human-readable decision packages and executive trade-off briefs for plant leadership.
- **Key Interfaces:** `ParetoFrontier`, `DecisionPackage`, `MetricObjective`, `HardConstraint`, `Preference`, `RankingMode`.

### Engine 4: Governed Agent Engine
- **Package Path:** `backend/src/qorvexa/copilot/` & `backend/src/qorvexa/agents/`
- **Active Tests:** Hermetic agent and copilot suites in `tests/test_copilot_*.py` and `tests/test_agent_*.py`
- **Core Responsibilities:**
  - Orchestrates supervisor and specialized sub-agent execution across query decomposition, evidence retrieval, diagnostic reasoning, and simulation invocation.
  - Enforces per-task token budgets, wall-clock timeout caps, and cooperative task cancellation.
  - Sandboxes untrusted model outputs into structured proposals, strictly preventing agents from directly mutating the Fact Ledger or bypassing admission gates.
  - Enforces red-team prompt guardrails against prompt injection and autonomous actuation commands.
  - **Zero Actuation Invariant:** Agents cannot approve industrial decisions autonomously or control physical equipment.
- **Key Interfaces:** `SupervisorAgent`, `AgentTask`, `ExecutionContext`, `CopilotService`, `GuardrailRule`.

### Architectural Mapping of Specialized Subsystems
Specialized numerical and analytical capabilities integrate directly into these four engines rather than existing as standalone platform engines:
- **Language Understanding & Factory Terminology:** Ingestion frontend and entity extractor feeding Engine 1 (Detailed in Section 5.3).
- **Core Solver Fabric & Mathematical Solvers:** Numerical execution workers dispatched by Engine 2 (Detailed in Section 6).
- **Discrete-Event Digital Twin:** Operational validation simulator invoked by Engine 2 (Detailed in Section 8).
- **Fact Ledger & Dimensional Units Gate:** Immutable state store and physical verification gate guarding Engine 3 (Detailed in Section 11).

---

## 5. RAG, Retrieval, Embeddings & GraphRAG

### 5.1 Hybrid Search & Retrieval Internals (IMPLEMENTED)
- **Source Module:** [backend/src/qorvexa/knowledge/rag.py](../../backend/src/qorvexa/knowledge/rag.py)
- **Active Tests:** 12 tests in `test_knowledge_rag_kg.py`
- **Algorithm:** Combines sparse BM25 keyword matching with dense embedding vector similarity using **Reciprocal Rank Fusion (RRF)**:
  $$RRF(d) = \sum_{m \in \{\text{BM25}, \text{Dense}\}} \frac{1}{k + r_m(d)}$$
  where $k = 60$ and $r_m(d)$ is the ordinal rank of document chunk $d$ in system $m$.
- **Chunking Strategy:** Structural markdown section chunking with exact character boundaries (minimum 30 characters, maximum 1,800 characters per segment).
- **Corpus & Passports:** Ingests asset digital passports (`passport.py`), repository technical specifications, and the official MetroPT-3 Data Description PDF.
- **Licensing Compliance:** PDF chunks are tagged `CC BY 4.0 (UCI #791)`; repository documentation chunks are tagged `Proprietary (Repository Documentation)`.

### 5.2 Advanced GraphRAG Architecture (RESEARCH / DESIGN ONLY)
- **Documentation:** [INDUSTRIAL_ONTOLOGY.md](INDUSTRIAL_ONTOLOGY.md)
- **Architecture:** Authored by Technical Lead Mohamed Osama (`@Mohamed-Osama-ai7`). Specifies multi-hop graph traversal across factory equipment hierarchies, asset failure modes, and operational constraints. Distinguishes structural graph reachability from unverified text embeddings.
- **Implementation Status:** The underlying relational ontology store is implemented (`test_industrial_ontology.py`), but autonomous multi-hop agentic graph traversal is currently in **Research / Design Only** status.

### 5.3 Industrial Language Understanding & Grounding (IMPLEMENTED)
- **Package Path:** `backend/src/qorvexa/industrial/understanding/`
- **Active Tests:** 43 tests in `tests/test_industrial_understanding.py`
- **Architectural Role:** Serves as the untrusted ingestion frontend and deterministic entity extractor for the Ontology & Context Engine. It processes unstructured, noisy factory language—specifically Egyptian colloquial Arabic (`عامية مصري`), Arabizi, English, and mixed shift jargon—into grounded, candidate claims without granting unverified natural language authority over system state.
- **13-Stage Deterministic Extraction Pipeline:**
  1. `input_normalization`: Normalizes Unicode, whitespace, and Arabic-Indic numerals (`١٢٣` $\to$ `123`).
  2. `script_detection`: Identifies Arabic, Latin/Arabizi, and mixed script segments.
  3. `dialect_arabizi_expansion`: Maps Egyptian dialect idioms and Arabizi transliterations (e.g., `3attal`, `el-khatt el-tani`) to standardized vocabulary.
  4. `tokenization_segmentation`: Deconstructs text into sentence and clause units with character offsets.
  5. `entity_candidate_extraction`: Extracts candidate plant assets, lines, tanks, machines, and components.
  6. `quantity_unit_extraction`: Extracts numerical quantities and dimensional physical units (e.g., hours, liters, kg, bars).
  7. `temporal_window_extraction`: Extracts operational shift boundaries, downtimes, and duration windows.
  8. `claim_boundary_attribution`: Binds extracted quantities and temporal scopes to specific candidate entities.
  9. `verbatim_quote_grounding`: Computes exact start and end character offsets (`quote_start`, `quote_end`) linking every claim to verbatim source text.
  10. `contradiction_detection`: Identifies conflicting assertions (e.g., simultaneous claims of a line being down for 2 hours and running at full capacity).
  11. `gap_analysis`: Identifies missing critical parameters required for downstream mathematical optimization.
  12. `clarification_planning`: Formulates targeted, structured clarification prompts when required parameters are ambiguous or missing.
  13. `untrusted_proposal_emission`: Packages grounded claims into immutable, tagged candidate proposals (`emit_proposals`) routed to the Fact Ledger admission gate.
- **Safety Invariant:** The extraction pipeline emits untrusted proposals only. It cannot directly mutate the Fact Ledger, bypass the Units Gate, or trigger optimization without validation.

---

## 6. Solver Fabric, Optimization Methods & AMPPS

### 6.1 Isolated Worker Execution Protocol (IMPLEMENTED)
Solvers execute in decoupled worker processes enclosed in Win32 Job Objects (or Linux cgroups) with hard limits:
- **Max Memory Cap:** 4 GB default limit; processes exceeding memory are terminated immediately by the operating system watchdog.
- **Execution Timeout:** Strict deadline enforcement; timeouts kill the worker cleanly without leaving orphaned subprocesses.
- **Framed JSON-IPC:** Input problems and output solution vectors communicate over length-prefixed stdin/stdout streams.
- **Multi-Worker Pool Feature Isolation:** The single isolated worker execution protocol is fully implemented and verified in the baseline. A separate multi-worker pool implementation (`632d626`: `feat(solver): add bounded multi-worker execution pool`), developed on the independent feature branch `feat/mohamed/multi-worker-solver-pool`, is under active development by Member 1 and is **not** included, integrated, or merged into the verified baseline snapshot.

### 6.2 Implementation Status Across Optimization Methods
To uphold absolute mathematical honesty, the repository distinguishes implemented solvers from future roadmap targets:

| Optimization Subsystem | Maturity Status | Technical Evidence & Capabilities |
|---|:---:|---|
| **Continuous Linear Programming (LP)** | **`IMPLEMENTED`** | HiGHS 1.16.0 continuous simplex and interior-point method (IPM) solves. Full primal and dual ray extraction. Verified in `test_sf_highs_adapter.py`. |
| **Independent LP Verifier** | **`IMPLEMENTED`** | Decoupled verification of primal feasibility, dual feasibility, and duality gap in `verifier.py`. |
| **Mixed-Integer Linear Programming (Native MILP)** | **`PLANNED / NOT IMPLEMENTED`** | Integer handling is currently limited to post-solve heuristic rounding of continuous LP solutions. This is **not** a native branch-and-cut MILP solver. Full MILP adapter is assigned to Member 3 (Mohamed Adel). |
| **Constraint Programming (CP-SAT)** | **`PLANNED`** | Discrete scheduling and machine sequencing models using OR-Tools CP-SAT are planned for Workstream 3. |
| **Nonlinear Programming (NLP)** | **`PLANNED`** | Continuous nonlinear optimization with KKT point verification is planned for future research phases. |

### 6.3 Adaptive Multi-Paradigm Problem Solving (AMPPS) (DESIGNED)
Defined in [PROBLEM_SOLVER_CONTRACT_ARCHITECTURE.md](PROBLEM_SOLVER_CONTRACT_ARCHITECTURE.md), AMPPS dynamically classifies the mathematical structure of an incoming industrial problem and routes it to the optimal paradigm:
- Continuous capacity allocation $\rightarrow$ **Continuous LP**
- Machine sequencing and shift scheduling $\rightarrow$ **CP-SAT / MILP**
- Non-convex physical fluid dynamics $\rightarrow$ **ODE Digital Twin Simulation**
- Sensor anomaly isolation $\rightarrow$ **Diagnostic RCA**

---

## 7. Causal Intelligence & Industrial Analytics

### 7.1 Physics-Informed Anomaly Detection (IMPLEMENTED)
- **Dataset:** UCI Dataset #791 — MetroPT-3 APU air compressor on Lisbon electric train #054 (2020).
- **Physical Sensor Envelope:** 7 analog telemetry streams: pressure (`bar`), motor current (`A`), temperatures (`°C`), oil level, flow rate.
- **Physics Baseline (`duty_rule`):** Implements a physics-informed operational duty rule tracking compressor active cycle duration against receiver pressure buildup rate ($dP/dt$).
- **Statistical Model (`pca_spe`):** Squared Prediction Error on Principal Component Analysis residual space with exceedance calibration.
- **Operating Regime Classifier:** Segregates telemetry into 4 distinct physical regimes: `stopped`, `unloaded`, `loaded`, `starting`.
- **Integrity Gating:** Automatically detects and abstains upon `stale_telemetry` ($>60\text{s}$ repeated flatline) or `signal_inconsistency` (current active while pressure reports zero).

### 7.2 Explainable Root-Cause Analysis (RCA) (IMPLEMENTED / PROTOTYPE)
- **Implementation:** `backend/src/qorvexa/anomaly/` and `rca_cases.v1.jsonl` benchmark suites.
- **Capabilities:** Traverses directed causal graphs representing component relationships (Compressor $\rightarrow$ Air Dryer $\rightarrow$ Reservoir $\rightarrow$ Brake Valves) to attribute anomalies to specific root causes (e.g., air leak, intake valve failure, pressure switch failure).
- **Epistemic Standard:** All causal claims must remain evidence-grounded. Statistical correlation or co-occurrence is strictly forbidden from being reported as causal attribution.

---

## 8. Digital Twin & Discrete-Event Simulation

```mermaid
flowchart LR
    Plan["Verified HiGHS Plan\n(Exact Output Quantities)"] --> Import["Plan Importer\n(QuantityPolicy:\nFLOOR / CEIL / EXACT)"]
    Import --> Kernel["DES Simulation Kernel\n(1s Integer Ticks · Event Queue)"]
    Kernel --> States["Machine Micro-States\nPROCESSING · STARVED\nBLOCKED · DOWN"]
    States --> Bottlenecks["Bottleneck Detection\n& Capacity Refinement"]
    Bottlenecks --> OSCO["OSCO Closed Loop\n(Re-Optimize with Evidence)"]
```

### 8.1 Simulation Kernel Architecture (IMPLEMENTED)
- **Package Path:** `backend/src/qorvexa/industrial/digital_twin/`
- **Clock Resolution:** Exact integer ticks (1 tick = 1 second). Continuous time durations from operational shift inputs are converted via the Units Engine (`1.5 min` $\rightarrow 90\text{s}$). Fractional seconds are rejected as `DURATION_NOT_ON_RESOLUTION`.
- **Machine State Accounting:** Every machine tracks cumulative seconds in:
  - `PROCESSING`: Active transformation of production units.
  - `STARVED`: Idle awaiting upstream parts or raw materials.
  - `BLOCKED`: Finished part unable to unload due to full downstream buffer.
  - `DOWN`: Unplanned failure or planned maintenance shift.
- **Stochastic Disturbance Modeling:** Independent pseudo-random streams per replication generated via deterministic SHA-256 seeding (`seed:replication:stream`), supporting Triangular and Exponential downtime distributions.

### 8.2 Optimization–Simulation Co-Orchestration (OSCO) (IMPLEMENTED)
- **Package Path:** `backend/src/qorvexa/industrial/co_orchestration/`
- **Core Loop:** Ingests a mathematically optimal LP plan, converts quantities to simulation orders under an explicit `QuantityPolicy`, simulates stochastic execution, detects buffer overflows and line starvations, and dynamically refines LP capacity bounds based solely on physical simulation evidence.

---

## 9. Read-Only Industrial Integration

### 9.1 Edge Connector Descriptors (PARTIALLY IMPLEMENTED)
- **Source Path:** [backend/src/qorvexa/connectors/](../../backend/src/qorvexa/connectors/)
- **Protocols Supported:** OPC UA (`opc.tcp://`), MQTT (`mqtt://`, `mqtts://`), Industrial Historians (OSIsoft PI, InfluxDB).
- **Current Capabilities:** In-memory source descriptors, URL syntax validation, network connection preflight checks, and supply-chain software attestation (`attestation.py`).

### 9.2 Strict Physical Safety Boundary (INVARIANT)
To ensure absolute zero risk of industrial accidents or property damage:
- **Zero Actuation Invariant:** QORVEXA contains **no software pathways, socket write commands, or protocol payloads capable of actuating physical machinery, altering PLC ladder logic, or modifying DCS setpoints**.
- **Advisory Architecture:** All system outputs are packaged as advisory decision packages requiring explicit human sign-off.

---

## 10. Multi-Agent Orchestration & Governance

### 10.1 Supervisor & Specialist Architecture (PARTIALLY IMPLEMENTED)
- **Source Paths:** `backend/src/qorvexa/copilot/` & `backend/src/qorvexa/agents/`
- **Execution Model:** Supervisor agent orchestrating specialized sub-agents (Retrieval Agent, Anomaly Diagnostics Agent, Formulation Agent, Simulation Agent).
- **Execution Governance:**
  - Strict wall-clock timeouts and cancellation token propagation.
  - Per-task token consumption budgets.
  - Idempotent task retry policies.
  - Untrusted proposal isolation: Agent outputs are stored as proposals and cannot write directly to the Fact Ledger.

### 10.2 Red-Team Prompt Guardrails (IMPLEMENTED)
- The copilot runtime enforces red-team guardrails against prompt injection, unauthorized physical control queries, and hallucinations.
- The copilot abstains with an explicit safety refusal whenever asked to command machinery or bypass verification gates.

---

## 11. Decision Intelligence & Governance

### 11.1 Fact Ledger & Cryptographic Provenance (IMPLEMENTED)
- **Package Path:** `backend/src/qorvexa/industrial/governance/`
- **Storage:** Isolated SQLite WAL database per factory tenant.
- **Hash Chain:** Every admitted fact record stores a SHA-256 hash computed over its sequence ID, timestamp, entity references, numeric values, and the previous record's hash ($H_i = \text{SHA256}(H_{i-1} \parallel \text{Payload}_i)$). Tampering with historical records invalidates the ledger chain.

### 11.2 Multi-Scenario Pareto Frontier Analysis (IMPLEMENTED)
- **Package Path:** `backend/src/qorvexa/industrial/decision_intelligence/`
- **Comparison Engine:** High-precision 50-digit exact `Decimal` arithmetic.
- **Pareto Efficiency:** Computes non-dominated operational plans across conflicting objectives (e.g., maximizing throughput margin while minimizing overtime cost and makespan).
- **Decision Packages:** Generates executive briefs detailing trade-offs, binding constraints (`BECAME_BINDING`, `NO_LONGER_BINDING`), and demand shortfalls.

---

## 12. Models, Algorithms, Technologies & Research Catalog

### 12.1 Mathematical & Computational Technologies
- **Linear Programming Solver:** HiGHS 1.16.0 (`highspy==1.16.0`) isolated subprocess.
- **Scientific Computing:** NumPy (`numpy==2.5.4`), SciPy (`scipy==1.18.1`), Pandas (`pandas==3.1.1`).
- **Precision Arithmetic:** Python standard library `Decimal` (50-digit precision) and `fractions.Fraction`.

### 12.2 Machine Learning & Statistical Algorithms
- **Physics Baseline:** `duty_rule` (air compressor duty-cycle physics baseline).
- **Anomaly Detection:** `isolation_forest` (Scikit-learn `IsolationForest`), `pca_spe` (Principal Component Analysis Squared Prediction Error).
- **Statistical Calibration:** `ExceedanceCalibrator` (extreme value exceedance probabilities), `AlarmTracker` (persistence-filtered alert states).
- **Evaluation Protocols:** Nested Leave-One-Event-Out (LOEO) cross-validation, precision-recall curve analysis, False Alarms per Operational Day (FA/op d).

### 12.3 NLP & Understanding Technologies
- **Language Pipeline:** 13-stage deterministic extraction pipeline (`understanding-pipeline/1.0`).
- **Multilingual Tokenizer:** Regex-based Arabic-Indic digit normalizer, Arabizi mapping table, Egyptian dialectal lexicon.
- **Provider Adapters:** Neutral LLM adapter interfaces (`LLMProvider`, `UnavailableProvider`) with strict schema parsing.

### 12.4 Web & Infrastructure Stack
- **Backend Framework:** FastAPI 0.162.2, Starlette 1.10.0, Pydantic 2.14.3, Uvicorn 0.54.0.
- **Frontend Framework:** React 19, TypeScript 5.7, Tailwind CSS v4, Vite 8, TanStack Router, Biome 2.6.6.
- **Packaging & Tooling:** Astral `uv` (locked deterministic Python virtualenv), `npm` (clean install locked `package-lock.json`).

### 12.5 Physical Dataset & Benchmark Catalog
1. **UCI MetroPT-3 APU Dataset (#791, 2020):** Continuous 1-second telemetry from an APU compressor on an electric passenger train in Lisbon, Portugal. Contains real physical sensor failure modes (air leaks, oil leaks, pressure switch failures).
2. **UCI AI4I 2020 Predictive Maintenance Dataset:** Synthetic dataset reflecting real milling machine tool wear, heat dissipation failure, power failure, and overstrain failure.
3. **Synthetic Industrial Production Planning Benchmarks:**
   - `planning_cases.v0.jsonl` & `planning_cases.v1.jsonl`: Cairo Food Processing (bottling & packaging), Alexandria Cable Manufacturing (wire drawing & annealing).
   - `rca_cases.v0.jsonl` & `rca_cases.v1.jsonl`: Grounded physical failure scenarios for root-cause analysis evaluation.
   - `retrieval_cases.v0.jsonl` & `retrieval_cases.v1.jsonl`: Multi-aspect technical manual search queries.

---

## 13. First Industrial Vertical & Applied Use Cases

### 13.1 Use Case 1: Industrial APU Compressor Health & Anomaly Management (`EVALUATED BENCHMARK ON REAL HISTORICAL TELEMETRY`)
- **Benchmark Dataset:** UCI MetroPT-3 APU Dataset (#791, 2020), consisting of continuous 1-second physical sensor telemetry from an electric passenger train APU compressor in Lisbon, Portugal.
- **Classification:** `EVALUATED BENCHMARK ON REAL HISTORICAL TELEMETRY`.
- **Industrial Setting:** Rail transportation, pneumatic automation, factory compressed-air utilities.
- **Problem:** Unpredicted compressor failures cause emergency braking or line depressurization, halting factory operations.
- **QORVEXA Solution:** Grounded sensor telemetry ingests into the 4-regime classifier; `duty_rule` and `pca_spe` detect early valve or line leakage $50\text{ minutes}$ prior to catastrophic failure; RCA engine attributes failure to air dryer vs. valve seal. Evaluated via nested Leave-One-Event-Out (LOEO) cross-validation.

### 13.2 Use Case 2: Multi-Line Food & Beverage Bottling Optimization (`SYNTHETIC PLANNING BENCHMARK & PROPOSED COMMERCIAL SCENARIO`)
- **Benchmark Dataset:** `planning_cases.v0.jsonl` & `planning_cases.v1.jsonl` (Cairo Food Processing Plant #1).
- **Classification:** `SYNTHETIC PLANNING BENCHMARK & PROPOSED COMMERCIAL SCENARIO`.
- **Grounding Note:** High-fidelity synthetic planning scenario designed to model Egyptian food packaging operations; represents a proposed commercial market application and synthetic evaluation benchmark, not an active connected customer plant.
- **Industrial Setting:** High-speed consumer goods packaging (Cairo Food Processing Plant #1).
- **Problem:** Production planners receive colloquial shift handoff notes ("الخط التاني فيه مشكلة في البكر والإنتاج واقف بقاله ساعتين"). Planning manually in spreadsheets leads to stockouts and late shipments.
- **QORVEXA Solution:** Egyptian Arabic shift note is ingested; entity resolution binds Line 2; Fact Ledger records the 2-hour downtime; canonical formulation compiler generates shift-bounded optimization; HiGHS solves line quantities; Digital Twin DES verifies that packaging buffer capacities are not exceeded; Pareto decision brief is delivered to the plant manager for approval.

### 13.3 Use Case 3: Energy-Constrained Cable Extrusion Scheduling (`SYNTHETIC PLANNING BENCHMARK & RESEARCH HYPOTHESIS`)
- **Benchmark Dataset:** Alexandria Cable Manufacturing continuous extrusion model (`planning_cases.v1.jsonl`).
- **Classification:** `SYNTHETIC PLANNING BENCHMARK & RESEARCH HYPOTHESIS`.
- **Grounding Note:** High-fidelity synthetic scheduling model designed to test energy-tariff constraints and continuous setup scrap in heavy manufacturing; represents an evaluated research hypothesis and benchmark scenario, not a live connected factory deployment.
- **Industrial Setting:** Continuous heavy manufacturing (Alexandria Cable Manufacturing).
- **Problem:** Annealing ovens and continuous extrusion lines face heavy penalty tariffs during peak grid energy hours. Scrap increases exponentially if a continuous run is halted prematurely.
- **QORVEXA Solution:** Multi-period canonical formulation models energy peak tariffs and minimum continuous run lengths; OSCO loop simulates scrap accumulation during machine setup phases; refined decision package balances overtime labor against electrical peak tariffs.

---

## 14. Mohamed Osama's Architecture, Technical Direction & Attribution

To maintain rigorous historical integrity and clear attribution across the engineering team, contributions are divided into five distinct categories:

### 14.1 Architecture Personally Designed or Directed by Mohamed Osama
Technical Lead Mohamed Osama (`@Mohamed-Osama-ai7`) conceived, architected, and led the development of QORVEXA from its inception as FactoryMind CDT through its enterprise evolution. Architectural direction personally authored and directed includes:
1. **Core Architectural Specifications:** Authored the foundational Architecture Decision Records (ADRs), the canonical Research Lab to Solver Fabric contract ([PROBLEM_SOLVER_CONTRACT_ARCHITECTURE.md](PROBLEM_SOLVER_CONTRACT_ARCHITECTURE.md)), the Options A & B architecture contracts, and the 22-subsystem maturity matrix.
2. **Four Core Platform Engines Architecture:** Structured the platform around the four canonical engines (Ontology & Context, Workflow Orchestration, Decision Intelligence, Governed Agent Engine) and defined the 14-stage conceptual workflow.
3. **Solver Fabric & Verification Contracts:** Conceived the decoupled Independent Mathematical Verifier architecture, establishing the epistemic principle that solver status flags must never be trusted without double-precision residual auditing.
4. **Safety & Security Invariants:** Established the non-negotiable physical OT read-only safety boundary (zero actuation invariant) and guaranteed that all operational decisions require authenticated human sign-off.
5. **GraphRAG Reasoning Architecture:** Authored the multi-hop relational GraphRAG and equipment traversal specifications ([INDUSTRIAL_ONTOLOGY.md](INDUSTRIAL_ONTOLOGY.md)).

### 14.2 Code Directly Attributable Through Repository Evidence
Direct code authorship attributable to Mohamed Osama in repository version control includes:
1. **Baseline Milestones (M0–M5):** Core foundational modules, data schemas, mathematical abstractions, and initial solver interfaces committed across early development milestones.
2. **Strict Benchmark Evaluation Harness:** Authored `backend/src/qorvexa/evaluation/`, establishing frozen split manifests, evaluation leakage audits, contamination detection, and tamper-evident run tracking.
3. **Core Platform Contracts & Interfaces:** Authored shared contracts under `backend/src/qorvexa/contracts/` governing problem representations, solver payloads, and evaluation records.

### 14.3 AI-Assisted Implementation Under Architectural Prompts
Subsystem implementation scaling was executed by advanced AI coding agents (Claude Code, Antigravity) operating under Mohamed Osama's explicit prompt specifications, ADR contracts, and interface schemas:
1. **Industrial Language Understanding Pipeline:** 14 modules in `backend/src/qorvexa/industrial/understanding/` covering 13-stage deterministic extraction, dialect/Arabizi normalization, quote offsets, and 43 tests.
2. **Event-Sourced Industrial Ontology Store:** Schema definitions, event-sourced SQLite WAL property graph, and 70 tests in `backend/src/qorvexa/industrial/ontology/`.
3. **Discrete-Event Digital Twin (DES):** 1-second integer tick clock, machine micro-states, scrap tracking, and 55 tests in `backend/src/qorvexa/industrial/digital_twin/`.
4. **Optimization–Simulation Co-Orchestration (OSCO):** 24 tests and state machines in `backend/src/qorvexa/industrial/co_orchestration/`.
5. **Production Planning Studio Frontend:** React 19 / TypeScript 5.7 UI components, Vitest test suite (75 tests), and Vite setup in `frontend/src/features/planning/`.
6. **Quality Gate Stabilization & Test Suite Expansion:** Scaling the backend regression suite to 1,924 hermetic tests and resolving CI lint/type regressions.

### 14.4 Work Implemented by Human Collaborators
1. **Ahmed Essam (`@a7medessam196`):** Contributed hybrid RAG search enhancements, dense vector + BM25 Reciprocal Rank Fusion (RRF), document chunking, and search retrieval evaluation (commit `59c88d01`). Designated forward ownership: Member 2 (Advanced RAG & Retrieval Engineering).
2. **Forward Human Ownership Workstreams (Members 3, 4, 5, and 6):** As documented in Section 16, Members 3 through 6 are beginning their designated engineering workstreams. Their assigned areas represent forward-looking ownership and development boundaries:
   - **Mohamed Adel (Member 3):** Advanced optimization, MILP/CP-SAT formulations, and scheduling adapters.
   - **Beshoy (Member 4):** Causal SCMs, industrial RCA, and sensor degradation models.
   - **Ziad (Member 5):** Multi-agent runtime, task graphs, execution budgets, and red-team guardrails.
   - **Ali Motassem (Member 6):** Digital twin scaling, read-only OT connectors, CI/CD pipelines, and observability.

### 14.5 Technical Review, Gatekeeper Oversight & Final Acceptance
1. **Architectural Review Authority:** Mohamed Osama serves as the sole architectural gatekeeper and code review authority for all modifications to core contracts, Solver Fabric protocols, REST APIs, and safety invariants.
2. **Quality Gate Enforcement:** Enforced strict testing standards across all PRs—mandating hermetic, offline CPU execution, zero skipped tests, zero weakened assertions, and full Mypy/Biome compliance prior to merge.
3. **Scientific & Legal Honesty Oversight:** Audited repository documentation to eliminate ungrounded commercial claims, distinguish real telemetry benchmarks from synthetic planning cases, and clarify licensing across Git branches.

---

## 15. Comprehensive 22-Subsystem Implementation Status

> [!IMPORTANT]
> **Dated Implementation Snapshot (November 23, 2026):**  
> The maturity classifications, source paths, and test counts in the matrix below reflect a verified snapshot of `origin/collaboration/qorvexa-current` at baseline commit `21f6ebdbc1bc6a80004056fe6988e3dbdc4dbba8` (and governance baseline `73872cc37f9a1f5a1a87a7ea8a5a0c1ef21a8d7b`).  
> **Unmerged Work Notice:** Commit `632d626` (`feat(solver): add bounded multi-worker execution pool`), located on active feature branch `feat/mohamed/multi-worker-solver-pool`, is an isolated workstream under Member 1's forward roadmap. It is **not** included in these test totals or maturity claims, and is neither integrated nor merged into `collaboration/qorvexa-current`.

Every subsystem is audited against live code and hermetic tests in `collaboration/qorvexa-current`:

| # | Subsystem Name | Maturity Classification | Primary Source Paths | Active Tests | Capabilities & Evidence |
|---|---|:---:|---|---|---|
| **1** | **Solver Fabric Core** | **`IMPLEMENTED`** | `backend/src/qorvexa/solver_fabric/` | `test_sf_highs_adapter.py` (21) | Isolated subprocess worker, Win32 Job Object enclosure, JSON-IPC framing, 4GB memory watchdog, exact timeout recovery. Note: Multi-worker pool (`632d626`) is on an unmerged feature branch and excluded from this baseline. |
| **2** | **Continuous LP Solvers** | **`IMPLEMENTED`** | `backend/src/qorvexa/solver_fabric/highs_*.py` | `test_sf_highs_adapter.py` | HiGHS 1.16.0 continuous LP solving, exact primal/dual solution parsing, dual ray / Farkas ray extraction. |
| **3** | **Native MILP Solvers** | **`PLANNED / NOT IMPLEMENTED`** | `backend/src/qorvexa/solver_fabric/` | N/A | Heuristic rounding of LP solutions exists; native branch-and-cut MILP adapter is assigned to Member 3 (Mohamed Adel). |
| **4** | **Constraint Programming (CP-SAT)**| **`PLANNED`** | `backend/src/qorvexa/solver_fabric/` | N/A | Discrete machine scheduling and combinatorial optimization via OR-Tools CP-SAT planned for Workstream 3. |
| **5** | **Solver Independent Verifier** | **`IMPLEMENTED`** | `backend/src/qorvexa/solver_fabric/verifier.py` | `test_sf_verifier.py` (12) | Independent double-precision verification of primal/dual residuals, duality gap, Farkas rays, condition number checks. |
| **6** | **Industrial Language Understanding**| **`IMPLEMENTED`** | `backend/src/qorvexa/industrial/understanding/` | `test_industrial_understanding.py` (43) | 13-stage deterministic extraction pipeline, Egyptian colloquial (`عامية مصري`), Arabizi, English, quote grounding. |
| **7** | **Ontology & Context Engine** | **`IMPLEMENTED`** | `backend/src/qorvexa/industrial/ontology/` | `test_industrial_ontology.py` (70) | Factory entity resolution, equipment hierarchy, candidate scoring, event-sourced SQLite graph state. |
| **8** | **Retrieval & Hybrid RAG** | **`IMPLEMENTED`** | `backend/src/qorvexa/knowledge/` | `test_knowledge_rag_kg.py` (12) | BM25 sparse search + dense vector retrieval, Reciprocal Rank Fusion (RRF), document chunking, CAG cache. |
| **9** | **Advanced GraphRAG** | **`RESEARCH / DESIGN ONLY`** | `docs/qorvexa/INDUSTRIAL_ONTOLOGY.md` | None | Multi-hop graph traversal architecture and agentic routing specified by Architect; code implementation pending. |
| **10** | **Causal Intelligence** | **`PROTOTYPE / EXPERIMENTAL`** | `backend/src/qorvexa/causal/` & `anomaly/` | `test_causal_*.py`, `test_anomaly_*.py` | Simple DAGs, observational conditioning, failure propagation analysis; Pearl do-calculus planned for Workstream 4. |
| **11** | **Multi-Agent Orchestration** | **`PARTIALLY IMPLEMENTED`** | `backend/src/qorvexa/copilot/` & `agents/` | `test_agent_*.py`, `test_copilot_*.py` | Supervisor/worker execution, timeout limits, red-team guardrails; distributed queue planned for Workstream 5. |
| **12** | **Discrete-Event Digital Twin** | **`IMPLEMENTED`** | `backend/src/qorvexa/industrial/digital_twin/` | `test_industrial_digital_twin.py` (55) | 1-second integer tick clock, machine micro-states (`PROCESSING`, `STARVED`, `BLOCKED`, `DOWN`), scrap accounting. |
| **13** | **Workflow Orchestration & OSCO** | **`IMPLEMENTED`** | `backend/src/qorvexa/industrial/co_orchestration/`| `test_optimization_simulation_orchestration.py` (24) | Overarching pipeline state machine; specialized OSCO solve $\leftrightarrow$ simulate loop, bottleneck detection, capacity refinements. |
| **14** | **Stochastic Optimization** | **`PARTIALLY IMPLEMENTED`** | `backend/src/qorvexa/industrial/digital_twin/stochastic.py` | `test_industrial_digital_twin.py` | Deterministic SHA-256 pseudo-random streams per replication, Triangular/Exponential distributions. |
| **15** | **Industrial Scenario Library** | **`IMPLEMENTED`** | `backend/benchmarks/qorvexa/` | `test_evaluation_splits.py` | Real MetroPT-3 compressor physical data (2020) + synthetic operational planning benchmarks (Cairo, Alexandria). |
| **16** | **Read-Only OT Integration** | **`PARTIALLY IMPLEMENTED`** | `backend/src/qorvexa/connectors/` | `test_edge_connectors.py` (37) | Source descriptors, URL syntax checks, batch preflight for OPC UA / MQTT / Historian; live socket I/O deferred. |
| **17** | **Fact Ledger & Governance** | **`IMPLEMENTED`** | `backend/src/qorvexa/industrial/governance/` | `test_governance.py` (13) | Append-only SQLite WAL store, SHA-256 hash chains, untrusted proposal lifecycle, admission gate. |
| **18** | **Decision Intelligence** | **`IMPLEMENTED`** | `backend/src/qorvexa/industrial/decision_intelligence/`| `test_decision_intelligence*.py` (84) | Pareto frontier calculation, multi-scenario ranking, trade-off matrix, executive briefs, 50-digit `Decimal` comparisons. |
| **19** | **Advisory Industrial REST API** | **`IMPLEMENTED`** | `backend/src/qorvexa/api/industrial.py` | `test_industrial_api_e2e.py` | FastAPI endpoints for intake, terminology, proposals, facts, solve, simulate, approve; zero actuation. |
| **20** | **Production Planning Studio UI**| **`IMPLEMENTED`** | `frontend/src/features/planning/` | Vitest (75 tests across 20 files) | React 19 UI: Egyptian Arabic intake presets, terminology manager, fact board, formulation view, solver progress, approvals. |
| **21** | **Testing & Evaluation Harness** | **`IMPLEMENTED`** | `backend/tests/`, `backend/benchmarks/` | Full regression (1,924 tests) | Hermetic unit, integration, and E2E test suites; frozen benchmark splits; contamination audits. |
| **22** | **Deployment & Observability** | **`IMPLEMENTED`** | `.github/workflows/ci.yml`, `vercel.json` | `test_ci_actions_pinned_and_root_license_present` | Vercel SPA preview deployment, GitHub Actions CI workflow with pinned action SHAs, supply chain attestation. |

---

## 16. Complete Six-Person Team Ownership & Boundaries

The QORVEXA engineering organization is structured into six human workstreams. **Members 2 through 6 are beginning their assigned workstreams; their designated responsibilities represent forward-looking engineering ownership and boundaries, not previously completed personal work.**

```mermaid
flowchart TD
    M1["Member 1: Technical Lead & Core Intelligence Architect (Mohamed Osama)\n@Mohamed-Osama-ai7\nCore Architecture · Solver Fabric · GraphRAG · Verification · Governance"]
    M2["Member 2: Advanced RAG & Retrieval Engineering (Ahmed Essam)\n@a7medessam196\nIngestion · Chunking · Embeddings · BM25/Dense · RRF · Reranking · CAG"]
    M3["Member 3: Advanced Optimization Engineering (Mohamed Adel)\nFormulations · LP/MILP/MIQP · CP-SAT · Scheduling · Solver Adapters"]
    M4["Member 4: Causal Intelligence & Industrial Analytics (Beshoy)\nSCMs · Industrial RCA · Fault Diagnosis · Counterfactuals · Degradation Models"]
    M5["Member 5: Advanced Multi-Agent Orchestration & Governance (Ziad)\nSupervisor Runtime · Task Graph · Context Handoffs · Execution Budgets · Audit"]
    M6["Member 6: Digital Twin, Simulation, OT & Observability (Ali Motassem)\nDES Twin · Stochastic Streams · Read-Only OT · CI/CD · Metrics & Tracing"]

    M2 -->|Retrieval evidence feeds into| M1
    M3 -->|Formulations conform to Solver Fabric| M1
    M4 -->|Causal evidence feeds into| M1
    M5 -->|Agent task execution conforms to governance| M1
    M6 -->|DES simulation validates schedules with| M3
    M6 -->|Telemetry feeds into| M4
```

### Member 1 — Technical Lead & Core Intelligence Architect: Mohamed Osama (`@Mohamed-Osama-ai7`)
- **Owns:** Overall system architecture, ADRs, technical roadmap, cross-engine integration standards, Core Solver Fabric architecture, solver worker execution protocols, advanced optimization architecture, GraphRAG reasoning architecture, adaptive solver routing, Fact Ledger governance, platform security, and final PR approval on shared contracts.
- **Boundaries:** Does not own specific domain formulations (Member 3) or retrieval engine internals (Member 2).

### Member 2 — Advanced RAG & Retrieval Engineering: Ahmed Essam (`@a7medessam196`)
- **Owns:** Document ingestion, technical PDF parsing, multilingual normalization, embedding model adapters, BM25 sparse + dense search, Reciprocal Rank Fusion, cross-encoder reranking, multi-query execution, CAG caching, and retrieval benchmarks (`retrieval_cases.*`).
- **Boundaries:** Does not own central GraphRAG architecture, ontology reasoning, multi-hop reasoning topology, or platform adaptive routing (owned by Member 1).

### Member 3 — Advanced Optimization Engineering: Mohamed Adel (`[GitHub Handle Pending]`)
- **Owns:** Industrial mathematical formulations (production planning, shift scheduling, cutting-stock), LP/MILP/MIQP models, constraint programming (OR-Tools CP-SAT), multi-objective optimization, stochastic optimization models, problem-specific solver adapters, and optimization benchmarks (Cairo, Alexandria).
- **Boundaries:** Does not own central Solver Fabric architecture, worker execution protocols, or solver registry (owned by Member 1).

### Member 4 — Causal Intelligence & Industrial Analytics: Beshoy (`[GitHub Handle Pending]`)
- **Owns:** Structural Causal Models (SCMs), causal DAGs, industrial Root-Cause Analysis (RCA), fault diagnosis, failure cascading models, counterfactual what-if analysis, predictive maintenance, sensor degradation curves, RUL estimation, and industrial analytics benchmarks (`rca_cases.*`).
- **Boundaries:** All causal assertions must remain evidence-grounded. Statistical correlation must never be reported as causation. Does not own Digital Twin simulation internals (Member 6) or decision packaging (Member 1).

### Member 5 — Advanced Multi-Agent Orchestration & Governance: Ziad (`[GitHub Handle Pending]`)
- **Owns:** Supervisor and specialized-agent runtime architecture, industrial task decomposition, dependency-aware task graphs, agent context handoffs, per-task execution budgets, timeouts, cancellation tokens, idempotent retry policies, and agent audit trails.
- **Boundaries:** **Strict Zero Actuation Invariant:** Agents cannot approve industrial decisions autonomously or control physical equipment. Does not own extraction NLP (Member 1) or retrieval internals (Member 2).

### Member 6 — Digital Twin, Simulation, Industrial Integration, Deployment & Observability: Ali Motassem (`[GitHub Handle Pending]`)
- **Owns:** Digital Twin foundations, discrete-event simulation (DES) kernel, stochastic Monte Carlo simulations, production KPI analysis, simulation evaluation of optimization schedules (OSCO), read-only industrial connectors (OPC UA, MQTT, Historians), CI/CD pipelines (`.github/workflows/`), containerization, structured logging, OpenTelemetry tracing, and metrics.
- **Boundaries:** **Strict Read-Only OT Boundary:** Absolutely zero write commands, setpoint changes, or actuation calls to physical plant machinery. Does not own mathematical optimization models (Member 3).

---

## 17. Integration Dependencies & Collaboration Rules

### 17.1 Cross-Workstream Contract Dependencies
To prevent integration collisions and architectural divergence:
1. **Contract-First Approval:** Any modification to shared data models under `backend/src/qorvexa/contracts/`, public REST schemas under `backend/src/qorvexa/api/`, or Solver Fabric protocols **requires explicit review and written sign-off from Technical Lead Mohamed Osama (`@Mohamed-Osama-ai7`) before business logic is written**.
2. **CI/CD Exclusivity:** Files under `.github/workflows/` are exclusively owned and maintained by Member 6 (Ali Motassem). Other team members may not alter CI workflow configurations without direct coordination.
3. **Hermetic Test Discipline:** Every pull request must include deterministic, offline, CPU-executable automated tests. Mocking out verification checks or weakening assertions is strictly grounds for immediate PR rejection.
4. **Physical OT Safety Affirmation:** Every PR template submission requires affirmative confirmation that the submitted code introduces zero socket write operations or physical actuation pathways.

### 17.2 Git Collaboration Workflow
Contributors must strictly adhere to the 9-step collaboration lifecycle defined in [DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md):
- Branch exclusively from `origin/collaboration/qorvexa-current`.
- Use conventional branch names: `feat/<member>/<feature>`, `fix/<member>/<issue>`, `research/<member>/<topic>`, `test/<member>/<scope>`, `docs/<member>/<topic>`.
- Never commit directly to `main` or `collaboration/qorvexa-current`.
- Open a PR using `.github/pull_request_template.md` with complete verification evidence.
- Review turnaround expectation: 24 to 48 business hours.

---

## 18. Historical Gaps & Epistemic Boundaries

In accordance with strict scientific and engineering honesty, the following historical information gaps are explicitly declared:

1. **Pre-Git Historical Records:** The earliest Git commit is `f5b908c` on October 19, 2026. Informal design conversations, academic coursework requirements, or oral agreements preceding this commit are not recorded in repository version control and cannot be reconstructed.
2. **Untracked Verbal Deliberations:** Technical trade-offs discussed in private meetings or personal communications that were not committed to Architecture Decision Records (ADRs) or Git commit messages are acknowledged as information gaps.
3. **Public Fork Association:** Unauthenticated GitHub forks (`beshoysaad468-sys/QORVEXA` and `AliMotassem164/FactoryMind-CDT`) exist publicly. Because formal account confirmation from the repository owner has not occurred, these accounts are not assumed to be verified collaborator identities in CODEOWNERS.
4. **Commercial Target Data Grounding:** Synthetic benchmarks (Cairo Food Processing, Alexandria Cable Manufacturing) represent mathematically rigorous simulation cases. They are not represented as live, active factory connections. The sole real physical telemetry dataset in the repository is the peer-reviewed **UCI MetroPT-3 APU air compressor dataset (#791)**.
