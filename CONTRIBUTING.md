# Contributing to QORVEXA

Welcome to the **QORVEXA** engineering team.

QORVEXA is an Industrial Decision Intelligence Platform built for high-stakes manufacturing and process optimization. We maintain rigorous standards for mathematical correctness, type safety, operational reproducibility, and industrial safety.

This guide defines the engineering workflow, branch conventions, review standards, and architectural control gates for our six-person engineering team.

---

## 1. Team Structure & Workstream Ownership

Engineering ownership is divided into six distinct, non-overlapping workstreams:

- **Workstream 1 — Technical Lead & Core Intelligence Architect (Mohamed Osama, `@Mohamed-Osama-ai7`):** Overall system architecture, Core Solver Fabric architecture, mathematical solver adapters, independent mathematical verifier, solver selection/routing, Adaptive Multi-Paradigm Problem Solving (AMPPS), advanced GraphRAG architecture, multi-hop retrieval architecture, agentic retrieval planning, cross-engine contracts, and final integration review.
- **Workstream 2 — Advanced RAG & Retrieval Engineering (Ahmed Essam, `@a7medessam196`):** Document ingestion, structure-aware parsing, chunking, multilingual normalization, embedding adapters, BM25/dense search, hybrid fusion (RRF), reranking, multi-query execution, CAG caching, and retrieval benchmarks.
- **Workstream 3 — Advanced Optimization Engineering (Mohamed Adel):** Industrial mathematical formulations, LP/MILP/MIQP models, production planning and scheduling models, constraint programming (CP-SAT), multi-objective optimization, stochastic and robust optimization implementations, problem-specific solver adapters, and mathematical validation benchmarks.
- **Workstream 4 — Causal Intelligence & Industrial Analytics (Beshoy):** Structural Causal Models (SCMs), causal graphs, industrial Root-Cause Analysis (RCA), fault diagnosis, failure propagation analysis, counterfactual reasoning, predictive maintenance, degradation curves, and RUL analytics. All causal claims must remain evidence-based.
- **Workstream 5 — Advanced Multi-Agent Orchestration & Governance (Ziad):** Supervisor and specialized-agent execution architecture, task decomposition, workflow coordination, dependency-aware execution, agent communication, context handoffs, execution budgets, timeouts, retry, permissions, and audit trails. Agents cannot approve industrial decisions autonomously or control physical equipment.
- **Workstream 6 — Digital Twin, Simulation, Industrial Integration, Deployment & Observability (Ali Motassem):** Digital Twin foundations, discrete-event simulation (DES), stochastic Monte Carlo simulations, production KPI and capacity analysis, simulation-based evaluation of optimization schedules, read-only industrial connectivity (OPC UA, MQTT, Historian), deployment execution, CI/CD, and platform observability. Absolutely zero PLC writes or equipment actuation.

For full details on boundaries, interfaces, and module matrices, consult [TEAM_WORKSTREAMS.md](docs/qorvexa/TEAM_WORKSTREAMS.md).

---

## 2. Git Branch Strategy & Workflow

### Shared Integration Branch
The single source of truth and team integration branch is:

```
collaboration/qorvexa-current
```

- **`main` Branch:** Reserved for tagged, customer-facing releases. No direct commits or daily feature PRs are merged into `main`.
- **`collaboration/qorvexa-current`:** Day-to-day shared collaboration branch. All feature branches branch from and target this branch.

### Feature Branch Naming Conventions
All development must occur on dedicated, single-purpose branches:

| Branch Prefix | Purpose | Example |
|---|---|---|
| `feat/<member>/<feature>` | New capability or subsystem extension | `feat/ahmed/hybrid-reranker` |
| `fix/<member>/<issue>` | Targeted bug fix or regression remediation | `fix/mohamed/highs-timeout-recovery` |
| `research/<member>/<topic>` | Mathematical modeling or algorithmic prototype | `research/user/ampps-router` |
| `test/<member>/<scope>` | New integration test suites or evaluation cases | `test/user/osco-stress-suite` |
| `docs/<member>/<topic>` | Documentation updates or architecture specs | `docs/user/causal-graphs-spec` |

**Rule:** Never push directly to `collaboration/qorvexa-current`. Always branch, verify locally, and open a Pull Request.

---

## 3. Pull Request (PR) Requirements

Every Pull Request must provide evidence of rigorous verification. PRs that omit required sections or claim unexecuted tests will not be reviewed.

### Pull Request Checklist
1. **Title:** Conventional commit format (`feat(...)`, `fix(...)`, `docs(...)`, `chore(...)`, `test(...)`).
2. **Purpose & Scope:** Clear description of the problem solved and changes introduced.
3. **Workstream Alignment:** Explicitly state which of the five workstreams owns the code.
4. **Modified Modules:** Enumerate changed modules and files.
5. **Architectural Impact:** Explicitly declare whether any shared contract, schema, or API was touched.
6. **Verification Evidence:**
   - Exact pytest commands executed and test pass counts.
   - Ruff linting and formatting results.
   - Mypy type-checking results.
   - Frontend test and build results (if frontend touched).
7. **Advisory Safety Affirmation:** Confirm that no code introduces write commands to physical OT equipment (PLCs, SCADA, DCS).
8. **Known Limitations:** Disclose any edge cases, performance constraints, or missing pieces honestly.

---

## 4. Engineering Standards & Code Quality Gates

Before opening a PR, all changes must pass the following local quality gates:

### Backend Quality Gates
```bash
cd backend

# 1. Linting & Formatting
uv run ruff check .
uv run ruff format --check .

# 2. Strict Static Typing
uv run mypy src/qorvexa/

# 3. Hermetic Test Execution (Never skip tests or weaken assertions)
uv run pytest --basetemp=../tmp_pytest -q
```

### Frontend Quality Gates
```bash
cd frontend

# 1. Type Checking
npm run typecheck

# 2. Test Execution
npm test

# 3. Production Compilation
npm run build
```

---

## 5. Shared Contracts & Architecture Control

Certain central modules represent the shared spine of QORVEXA. To prevent contract drift, breaking changes, or silent divergence:

**Mandatory Architect Review (Mohamed Osama) is required for changes affecting:**
- Core Solver Fabric contracts (`backend/src/qorvexa/solver_fabric/`)
- Solver routing and independent verification logic
- Factory ontology and context contracts (`backend/src/qorvexa/industrial/ontology/`)
- Dimensional units verification engine (`backend/src/qorvexa/industrial/units/`)
- Governed session persistence and Fact Ledger hash chaining (`backend/src/qorvexa/industrial/governance/`)
- Decision Intelligence Pareto frontier interfaces (`backend/src/qorvexa/industrial/decision_intelligence/`)
- Cross-engine event and API contracts (`backend/src/qorvexa/api/`)
- Advanced GraphRAG and agentic retrieval routing architecture

**Golden Rule:** No two contributors should independently modify the same shared module or contract without prior architecture coordination.

---

## 6. Intellectual Property & Contributor Policy

QORVEXA is a proprietary commercial product:

1. Contributors must only submit original work that they have full rights to contribute.
2. Contributions are accepted on the condition that they are proprietary and owned by QORVEXA under a formal Contributor License Agreement (CLA) approved by Mohamed Osama.
3. Submission of a Pull Request does not grant any third party a public open-source license.
4. For complete legal context and historical transition details, consult [LICENSING_TRANSITION.md](docs/qorvexa/LICENSING_TRANSITION.md).
