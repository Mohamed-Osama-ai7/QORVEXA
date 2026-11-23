# QORVEXA Documentation

Welcome to the public documentation for **QORVEXA**, an industrial decision intelligence platform combining AI-assisted problem understanding, mathematical optimization, digital twin simulation, and human-governed operational decision support.

---

## 1. Overview

QORVEXA helps engineering and operations teams evaluate complex plant trade-offs across maintenance schedules, equipment health, production bottlenecks, and energy costs. The platform bridges contextual engineering data (such as operator shift logs and maintenance manuals) with verified mathematical optimization and simulation models.

### Core Principles
- **Contextual Interpretation:** AI translates unstructured operator logs and machine telemetry into structured operational formulations.
- **Dedicated Solvers:** Mathematical models are solved independently by specialized computational engines.
- **Independent Verification:** Candidate solutions are validated against physical constraints before presentation.
- **Human Governance & Read-Only Safety:** The platform is strictly advisory and read-only. It never writes to PLCs, never alters machine setpoints, and never actuates equipment.

---

## 2. Platform Capabilities

- **Diagnostic Insights:** Synthesizes telemetry anomalies and maintenance history into explainable diagnostic hypotheses.
- **Industrial Optimization:** Analyzes operational trade-offs across production planning, resource constraints, and maintenance scheduling.
- **Scenario Simulation:** Models prospective operational decisions within digital twin simulations without risking live equipment.
- **Decision Intelligence Studio:** Delivers actionable, structured decision briefs detailing trade-offs, confidence levels, and audit trails.

---

## 3. Interactive Platform Demo

A live public preview of the QORVEXA Decision Studio is hosted at:  
👉 **[https://qorvexa-delta.vercel.app](https://qorvexa-delta.vercel.app)**

The preview demonstrates the scenario workbench, decision intelligence briefs, and interactive trade-off visualization.

---

## 4. Getting Started (Local Development)

### Prerequisites
- **Python**: 3.12+
- **Python Package Manager**: [uv](https://docs.astral.sh/uv/) (Astral)
- **Node.js**: v24+ and npm v10+
- **Git**: 2.40+

### Backend Setup
```bash
cd backend

# Install dependencies into hermetic virtual environment
uv sync

# Run automated test suite
uv run pytest

# Launch local advisory API
uv run uvicorn qorvexa.api.app:app --reload
```

### Frontend Setup
```bash
cd frontend

# Install frontend dependencies
npm ci

# Run unit tests
npm test

# Launch Vite development server
npm run dev
# Interface available at http://localhost:5173
```

---

## 5. Public Contribution Guidelines

We welcome community feedback, issue reports, and contributions:
1. **Branch Workflow:** Create focused feature branches off `main` or designated collaboration branches (`feat/<topic>` or `docs/<topic>`).
2. **Quality Gates:** Verify all tests pass (`uv run pytest` and `npm test`) and code is formatted before opening a pull request.
3. **Safety Invariant:** All contributions must preserve the read-only safety boundary. No endpoints, tools, or background processes may actuate physical industrial equipment.

---

## 6. Historical Research Notes

The root `docs/` folder contains historical milestone technical records (`m1` through `m5`) documenting the research evolution from the FactoryMind Cognitive Digital Twin prototype to the modern QORVEXA platform.
