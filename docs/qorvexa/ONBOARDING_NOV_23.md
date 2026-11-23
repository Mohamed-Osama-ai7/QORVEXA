# QORVEXA — Engineering Onboarding & Technical Reference Guide

**Document Status:** Official Contributor Onboarding Specification  
**Cycle Baseline:** November 23, 2026  
**Repository Branch:** `collaboration/qorvexa-current`  
**License:** Proprietary / Closed-Source / All Rights Reserved  
**Operating Mode:** Advisory Only · Read-Only (Safety Invariant Protected)

---

## 1. What QORVEXA Is

**QORVEXA** is an **Industrial Decision Intelligence Platform** developed by the QORVEXA Research Lab.

Modern industrial operations face a fundamental breakdown between operational reality on the factory floor and mathematical optimization in management software:
- Shop-floor knowledge is communicated in colloquial, dialectal, and informal natural language (e.g., Egyptian Arabic `عامية مصري`, Arabizi, shift notes, supervisor logs).
- Operational constraints involve buffer capacities, machine starvations, scrap generation, stochastic breakdown distributions, and strict physical unit dimensions.
- Standard Large Language Models (LLMs) hallucinate unit factors, confuse correlation with causation, and cannot be trusted to solve mathematical optimization models directly.
- Commercial mathematical solvers (such as HiGHS, Gurobi, CPLEX) require formal, dimensionally validated canonical formulations and cannot ingest colloquial shop-floor text.

QORVEXA bridges this gap through a unified, 12-stage decision intelligence pipeline:

$$\begin{aligned}
\text{Factory Language Intake} &\longrightarrow \text{Language Understanding \& Grounding} \longrightarrow \text{Factory Terminology (Isolated)} \\
&\longrightarrow \text{Untrusted Proposals} \longrightarrow \text{Fact Ledger (Hash-Chained)} \longrightarrow \text{Strict Dimensional Units Gate} \\
&\longrightarrow \text{Canonical Formulation} \longrightarrow \text{Solver Fabric (Isolated HiGHS Subprocess)} \\
&\longrightarrow \text{Independent Mathematical Verifier} \longrightarrow \text{Discrete-Event Digital Twin (DES)} \\
&\longrightarrow \text{Optimization–Simulation Co-Orchestration (OSCO)} \longrightarrow \text{Decision Intelligence (Pareto Package)} \\
&\longrightarrow \text{Authenticated Human Sign-off} \longrightarrow \text{Tamper-Evident Audit Trail}
\end{aligned}$$

---

## 2. Real Architecture & Subsystem Boundaries

QORVEXA is organized into modular Python backend subsystems and a modern React/TypeScript frontend. To prevent confusion, the table below documents the actual implementation status of every module:

| Subsystem | Source Path | Implementation State | Verification Evidence |
|---|---|---|---|
| **Solver Fabric** | `backend/src/qorvexa/solver_fabric/` | **Implemented**: Isolated subprocess HiGHS 1.16.0 worker with JSON-IPC, memory watchdogs, and exact timeout cancellation | `tests/test_sf_highs_adapter.py` (21 passed) |
| **Independent Verifier** | `backend/src/qorvexa/solver_fabric/verifier.py` | **Implemented**: Primal/dual feasibility, duality gap, Farkas rays, condition number checks | `tests/test_sf_verifier.py` (12 passed) |
| **Industrial Understanding** | `backend/src/qorvexa/industrial/understanding/` | **Implemented**: 13-stage deterministic extraction pipeline; Egyptian colloquial, Arabizi, English; quote grounding, contradiction detection, gap analysis | `tests/test_industrial_understanding.py` (43 passed) |
| **Factory Terminology** | `backend/src/qorvexa/industrial/understanding/terminology.py` | **Implemented**: Factory-scoped SQLite dictionaries, versioned terms, reviewer approval gate | `tests/test_industrial_understanding_api.py` (18 passed) |
| **Factory Knowledge Ontology** | `backend/src/qorvexa/industrial/ontology/` | **Implemented**: Graph-based entity resolution, asset linking, mention linking, candidate scoring | `tests/test_industrial_understanding_api.py` |
| **Dimensional Units Engine** | `backend/src/qorvexa/industrial/units/` | **Implemented**: Exact dimensional analysis ($[M]^a [L]^b [T]^c [\Theta]^d$), custom unit definitions, explicit conversions | `tests/test_industrial_units.py` (64 passed) |
| **Fact Ledger & Governance** | `backend/src/qorvexa/industrial/governance/` | **Implemented**: Append-only SQLite WAL store, SHA-256 hash chains, untrusted proposal lifecycle, admission gate | `tests/test_governance.py` (13 passed) |
| **Decision Intelligence** | `backend/src/qorvexa/industrial/decision_intelligence/` | **Implemented**: Multi-scenario Pareto frontier, multi-criteria ranking, trade-off matrix, executive briefs | `tests/test_decision_intelligence*.py` (84 passed) |
| **Discrete-Event Digital Twin** | `backend/src/qorvexa/industrial/digital_twin/` | **Implemented**: Integer tick event scheduler, micro-state tracking (`PROCESSING`, `STARVED`, `BLOCKED`, `DOWN`), scrap accounting, Monte Carlo stochastic streams | `tests/test_industrial_digital_twin.py` (55 passed) |
| **Optimization–Simulation Co-Orchestration (OSCO)** | `backend/src/qorvexa/industrial/co_orchestration/` | **Implemented**: Iterative solve $\leftrightarrow$ simulation refinement loop, operational violation detection, calendar-bounded adjustments | `tests/test_optimization_simulation_orchestration.py` (24 passed) |
| **Advisory Industrial REST API** | `backend/src/qorvexa/api/industrial.py` | **Implemented**: FastAPI endpoints for intake, sessions, claims, facts, solve, scenarios, decisions, approval | `tests/test_industrial_api_e2e.py` |
| **Production Planning Studio** | `frontend/src/features/planning/` | **Implemented**: React 19 UI for shift intake, terminology manager, fact board, formulation view, scenario workbench, approval | 75 passed across 20 Vitest files |
| **Knowledge & Hybrid RAG** | `backend/src/qorvexa/knowledge/` | **Implemented**: BM25 sparse + dense retrieval, Reciprocal Rank Fusion, document chunking | `tests/test_knowledge_rag_kg.py` (12 passed) |
| **Advanced GraphRAG** | `backend/src/qorvexa/knowledge/` | **Research / Design Only**: Multi-hop graph traversal, agentic retrieval routing (Architect owned) | Pending implementation |
| **Read-Only OT Connectors** | `backend/src/qorvexa/connectors/` | **Partially Implemented**: Descriptors and batch preflight for OPC UA / MQTT / Historian; live I/O deferred | `tests/test_edge_connectors.py` |

---

## 3. Development Prerequisites

Contributors must run the strictly validated development stack:

- **Python**: `3.12` (strictly tested on 3.12.10; Python 3.13 is not yet supported by all solver wheels)
- **Python Package Manager**: `uv >= 0.5.0` (Astral)
- **Node.js**: `v24+` (LTS recommended)
- **Node Package Manager**: `npm >= 10.0`
- **Git**: `2.40+`
- **Operating System**: Supported on Windows 10/11, Linux (Ubuntu 22.04+), and macOS (Apple Silicon / Intel).

---

## 4. Clone & Setup Instructions

### A. Clone the Integration Branch
```bash
# Clone the repository and checkout the collaboration branch
git clone -b collaboration/qorvexa-current https://github.com/Mohamed-Osama-ai7/QORVEXA.git
cd QORVEXA
```

### B. Backend Environment Setup

#### Windows PowerShell:
```powershell
cd backend

# 1. Sync locked virtual environment
uv sync

# 2. Run code quality checks
uv run ruff check .
uv run ruff format --check .
uv run mypy src/qorvexa/

# 3. Run full hermetic test suite (1,924 tests)
uv run pytest --basetemp=..\tmp_pytest -q

# 4. Launch local API server
uv run uvicorn qorvexa.api.app:app --host 127.0.0.1 --port 8000 --reload
```

#### Linux / macOS / WSL:
```bash
cd backend

# 1. Sync locked virtual environment
uv sync

# 2. Run code quality checks
uv run ruff check .
uv run ruff format --check .
uv run mypy src/qorvexa/

# 3. Run full hermetic test suite (1,924 tests)
uv run pytest --basetemp=../tmp_pytest -q

# 4. Launch local API server
uv run uvicorn qorvexa.api.app:app --host 127.0.0.1 --port 8000 --reload
```

### C. Frontend Environment Setup
```bash
cd ../frontend

# 1. Install locked dependencies
npm ci

# 2. Run static typing checks
npm run typecheck

# 3. Run Vitest test suite (75 tests across 20 files)
npm test

# 4. Verify production compilation
npm run build

# 5. Launch local Vite development server
npm run dev
# The Planning Studio is available at http://localhost:5173/planning
```

---

## 5. Environment Configuration & Security Rules

1. **Reference Template**: `.env.example` at the repository root contains all configuration parameters with safe local defaults.
2. **Local Configuration**: Copy `.env.example` to `backend/.env` for local backend development:
   ```bash
   cp .env.example backend/.env
   ```
3. **Absolute Prohibition on Secrets**: Never commit `.env`, `.env.local`, API keys, private tokens, or credentials. Version control is monitored by automated scanning.
4. **Advisory OT Boundary**: The system is designed strictly for read-only decision support. Do not attempt to configure writes to physical PLCs or industrial automation equipment.

---

## 6. Team Governance & Development Workflow

Consult the following authoritative specifications before writing code:
- **Workstream Ownership & Module Matrix:** [TEAM_WORKSTREAMS.md](TEAM_WORKSTREAMS.md)
- **Nine-Step Engineering Lifecycle & PR Policy:** [DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md)
- **Contribution Standards & Branch Conventions:** [CONTRIBUTING.md](../../CONTRIBUTING.md)
