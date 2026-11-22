import { useState } from 'react'

export interface PublicLandingProps {
  status: 'unconfigured' | 'signed-out' | 'loading' | 'signed-in' | 'error'
  error?: string | null
  onSignIn?: () => void
}

export function PublicLanding({ status, error, onSignIn }: PublicLandingProps) {
  const [activeTab, setActiveTab] = useState<'architecture' | 'solvers' | 'engines' | 'workflow' | 'planning' | 'status'>('architecture')

  return (
    <div className="min-h-screen bg-surface text-ink selection:bg-accent/30 selection:text-ink">
      {/* Top Navigation */}
      <header className="sticky top-0 z-40 border-b border-border bg-panel/90 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-panel border border-border">
              <svg className="h-5 w-5 text-accent" viewBox="0 0 32 32" fill="none">
                <circle cx="16" cy="15" r="9" stroke="currentColor" strokeWidth="2.5" />
                <circle cx="16" cy="15" r="4" fill="currentColor" />
                <path d="M21 20l5 5" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
              </svg>
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-base font-bold tracking-tight text-ink">QORVEXA</span>
                <span className="rounded bg-accent/15 px-1.5 py-0.5 text-[10px] font-semibold text-accent uppercase tracking-wider">
                  Lab Preview
                </span>
              </div>
              <p className="text-[11px] text-muted">Industrial AI Research Lab</p>
            </div>
          </div>

          <nav className="hidden md:flex items-center gap-1 text-xs">
            <button
              type="button"
              onClick={() => setActiveTab('architecture')}
              className={`rounded px-3 py-1.5 font-medium transition ${activeTab === 'architecture' ? 'bg-border text-ink' : 'text-muted hover:text-ink hover:bg-border/50'}`}
            >
              Architecture
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('solvers')}
              className={`rounded px-3 py-1.5 font-medium transition ${activeTab === 'solvers' ? 'bg-border text-ink' : 'text-muted hover:text-ink hover:bg-border/50'}`}
            >
              Solver Fabric
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('engines')}
              className={`rounded px-3 py-1.5 font-medium transition ${activeTab === 'engines' ? 'bg-border text-ink' : 'text-muted hover:text-ink hover:bg-border/50'}`}
            >
              4 Engines
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('workflow')}
              className={`rounded px-3 py-1.5 font-medium transition ${activeTab === 'workflow' ? 'bg-border text-ink' : 'text-muted hover:text-ink hover:bg-border/50'}`}
            >
              14-Stage Workflow
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('planning')}
              className={`rounded px-3 py-1.5 font-medium transition ${activeTab === 'planning' ? 'bg-border text-ink' : 'text-muted hover:text-ink hover:bg-border/50'}`}
            >
              Production Planning
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('status')}
              className={`rounded px-3 py-1.5 font-medium transition ${activeTab === 'status' ? 'bg-border text-ink' : 'text-muted hover:text-ink hover:bg-border/50'}`}
            >
              Status Matrix
            </button>
          </nav>

          <div className="flex items-center gap-3">
            {onSignIn ? (
              <button
                type="button"
                onClick={onSignIn}
                className="rounded bg-accent px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-accent/90 focus:outline-none focus:ring-2 focus:ring-accent"
              >
                Sign in
              </button>
            ) : (
              <span className="rounded border border-border bg-panel px-2.5 py-1 text-[11px] text-muted">
                Enterprise OIDC
              </span>
            )}
          </div>
        </div>
      </header>

      {/* Main Container */}
      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8 space-y-12">
        {/* Notice for authentication status */}
        {status === 'unconfigured' && (
          <div className="rounded-lg border border-border bg-panel/70 p-4 text-xs">
            <div className="flex items-start gap-3">
              <span className="mt-0.5 rounded bg-warn/15 px-2 py-0.5 font-semibold text-warn">
                Notice
              </span>
              <div className="space-y-1">
                <p className="font-semibold text-ink">
                  Authentication is not configured for public preview mode
                </p>
                <p className="text-muted">
                  Live operational telemetry, physical asset write controls, and active incident workflows require an enterprise OIDC Identity Provider (Keycloak / Okta / Azure AD). This deployment serves as a verified architectural, formulation, and decision-intelligence showcase.
                </p>
              </div>
            </div>
          </div>
        )}

        {error && (
          <div role="alert" className="rounded-lg border border-critical bg-critical/10 p-4 text-xs text-critical">
            {error}
          </div>
        )}

        {/* Hero Section */}
        <section className="text-center space-y-4 py-6 border-b border-border/60">
          <div className="inline-flex items-center gap-2 rounded-full border border-accent/30 bg-accent/10 px-3 py-1 text-xs font-medium text-accent">
            <span>Industrial Decision Intelligence Platform</span>
          </div>
          <h1 className="text-3xl sm:text-5xl font-extrabold tracking-tight text-ink">
            From Industrial Complexity to Verified Decisions.
          </h1>
          <p className="mx-auto max-w-3xl text-sm sm:text-base text-muted leading-relaxed">
            QORVEXA transforms complex industrial problems into explainable, mathematically grounded, independently verified decisions through AI-assisted problem understanding, specialized solvers, simulation, and governed decision workflows.
          </p>

          <div className="flex flex-wrap items-center justify-center gap-2 pt-2 text-xs">
            <span className="rounded border border-border bg-panel px-2.5 py-1 text-muted font-mono">
              Advisory &amp; Read-Only
            </span>
            <span className="rounded border border-border bg-panel px-2.5 py-1 text-muted font-mono">
              Independent Verifier
            </span>
            <span className="rounded border border-border bg-panel px-2.5 py-1 text-muted font-mono">
              Multi-Paradigm Solving
            </span>
            <span className="rounded border border-border bg-panel px-2.5 py-1 text-muted font-mono">
              HiGHS Continuous LP Layer
            </span>
            <span className="rounded border border-border bg-panel px-2.5 py-1 text-muted font-mono">
              Cryptographic Audit Chains
            </span>
          </div>
        </section>

        {/* Core Principles Section */}
        <section className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-bold text-ink tracking-tight">Core Architectural Principles</h2>
            <span className="text-xs text-muted">Safety &amp; Verification Foundation</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <div className="rounded-lg border border-border bg-panel p-4 space-y-2">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-accent" />
                <h3 className="text-sm font-semibold text-ink">LLMs Interpret &amp; Coordinate</h3>
              </div>
              <p className="text-xs text-muted leading-relaxed">
                Large Language Models formulate context, structure problem parameters, and coordinate processes. They are never treated as the source of mathematical or physical ground truth.
              </p>
            </div>

            <div className="rounded-lg border border-border bg-panel p-4 space-y-2">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-ok" />
                <h3 className="text-sm font-semibold text-ink">Actual Execution Truth</h3>
              </div>
              <p className="text-xs text-muted leading-relaxed">
                All numerical recommendations derive strictly from real solver execution (e.g. HiGHS continuous LP) inside isolated sandboxed worker processes.
              </p>
            </div>

            <div className="rounded-lg border border-border bg-panel p-4 space-y-2">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-accent" />
                <h3 className="text-sm font-semibold text-ink">Independent Verification</h3>
              </div>
              <p className="text-xs text-muted leading-relaxed">
                Solver candidate vectors are strictly segregated from verified facts. An independent verifier validates feasibility, bound bounds, and duality certificates before any presentation.
              </p>
            </div>

            <div className="rounded-lg border border-border bg-panel p-4 space-y-2">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-warn" />
                <h3 className="text-sm font-semibold text-ink">Advisory &amp; Read-Only</h3>
              </div>
              <p className="text-xs text-muted leading-relaxed">
                QORVEXA never executes PLC writes, SCADA actuations, or direct machine control. Every industrial recommendation requires explicit human engineering sign-off.
              </p>
            </div>

            <div className="rounded-lg border border-border bg-panel p-4 space-y-2">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-ok" />
                <h3 className="text-sm font-semibold text-ink">Distinguishable Epistemics</h3>
              </div>
              <p className="text-xs text-muted leading-relaxed">
                The platform cleanly demarcates confirmed physical telemetry facts, mathematical proofs, calibrated statistical estimates, and pending engineering assumptions.
              </p>
            </div>

            <div className="rounded-lg border border-border bg-panel p-4 space-y-2">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-accent" />
                <h3 className="text-sm font-semibold text-ink">Full Traceability</h3>
              </div>
              <p className="text-xs text-muted leading-relaxed">
                Every optimization output includes model SHA-256 digests, IR matrices, compilation manifests, and append-only tamper-evident audit logs.
              </p>
            </div>
          </div>
        </section>

        {/* Tabbed In-Depth Architecture Showcase */}
        <section className="space-y-6">
          <div className="flex border-b border-border overflow-x-auto gap-2">
            <button
              type="button"
              onClick={() => setActiveTab('architecture')}
              className={`pb-3 text-xs font-semibold whitespace-nowrap transition border-b-2 px-1 ${
                activeTab === 'architecture'
                  ? 'border-accent text-accent'
                  : 'border-transparent text-muted hover:text-ink'
              }`}
            >
              Adaptive Multi-Paradigm Solving
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('solvers')}
              className={`pb-3 text-xs font-semibold whitespace-nowrap transition border-b-2 px-1 ${
                activeTab === 'solvers'
                  ? 'border-accent text-accent'
                  : 'border-transparent text-muted hover:text-ink'
              }`}
            >
              Solver Fabric Ecosystem
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('engines')}
              className={`pb-3 text-xs font-semibold whitespace-nowrap transition border-b-2 px-1 ${
                activeTab === 'engines'
                  ? 'border-accent text-accent'
                  : 'border-transparent text-muted hover:text-ink'
              }`}
            >
              Four Intelligence Engines
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('workflow')}
              className={`pb-3 text-xs font-semibold whitespace-nowrap transition border-b-2 px-1 ${
                activeTab === 'workflow'
                  ? 'border-accent text-accent'
                  : 'border-transparent text-muted hover:text-ink'
              }`}
            >
              14-Stage Advisory Workflow
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('planning')}
              className={`pb-3 text-xs font-semibold whitespace-nowrap transition border-b-2 px-1 ${
                activeTab === 'planning'
                  ? 'border-accent text-accent'
                  : 'border-transparent text-muted hover:text-ink'
              }`}
            >
              Production Planning Vertical
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('status')}
              className={`pb-3 text-xs font-semibold whitespace-nowrap transition border-b-2 px-1 ${
                activeTab === 'status'
                  ? 'border-accent text-accent'
                  : 'border-transparent text-muted hover:text-ink'
              }`}
            >
              Implementation Status Matrix
            </button>
          </div>

          {/* TAB 1: Adaptive Problem-Solving */}
          {activeTab === 'architecture' && (
            <div className="space-y-4">
              <p className="text-xs text-muted">
                QORVEXA dynamically selects or composes problem-solving modes according to problem structure, available telemetry, mathematical tractability, and engineer configuration.
              </p>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <div className="rounded-lg border border-accent/40 bg-panel p-4 space-y-2">
                  <span className="rounded bg-accent/20 px-2 py-0.5 text-[10px] font-bold text-accent uppercase">
                    Default Mode
                  </span>
                  <h4 className="text-sm font-bold text-ink">Automatic</h4>
                  <p className="text-xs text-muted">
                    Autonomous problem intake, operational regime detection, mathematical formulation, solver dispatch, and multi-tier verification. Recommended for standard production operations.
                  </p>
                </div>

                <div className="rounded-lg border border-border bg-panel p-4 space-y-2">
                  <span className="rounded bg-ok/20 px-2 py-0.5 text-[10px] font-bold text-ok uppercase">
                    Cooperative
                  </span>
                  <h4 className="text-sm font-bold text-ink">Hybrid (LLM + Solvers)</h4>
                  <p className="text-xs text-muted">
                    LLM extracts constraints, objectives, and domain rules from unstructured reports and shift logs, compiling them into strict canonical forms for mathematical optimization.
                  </p>
                </div>

                <div className="rounded-lg border border-border bg-panel p-4 space-y-2">
                  <span className="rounded bg-muted/20 px-2 py-0.5 text-[10px] font-bold text-ink uppercase">
                    Deterministic
                  </span>
                  <h4 className="text-sm font-bold text-ink">OR-Only</h4>
                  <p className="text-xs text-muted">
                    Bypasses generative models entirely. Ingests structured telemetry directly into canonical LP / MILP formulations with exact arithmetic and deterministic solution guarantees.
                  </p>
                </div>

                <div className="rounded-lg border border-border bg-panel p-4 space-y-2">
                  <span className="rounded bg-warn/20 px-2 py-0.5 text-[10px] font-bold text-warn uppercase">
                    Qualitative
                  </span>
                  <h4 className="text-sm font-bold text-ink">LLM-Only</h4>
                  <p className="text-xs text-muted">
                    Engaged when numerical telemetry is unobservable or problems cannot be mathematically grounded. Provides qualitative synthesis with explicit non-mathematical epistemic warnings.
                  </p>
                </div>

                <div className="rounded-lg border border-border bg-panel p-4 space-y-2">
                  <span className="rounded bg-accent/20 px-2 py-0.5 text-[10px] font-bold text-accent uppercase">
                    Governance
                  </span>
                  <h4 className="text-sm font-bold text-ink">Expert-Controlled</h4>
                  <p className="text-xs text-muted">
                    Senior industrial engineers explicitly dictate solver method, tolerance bounds, objective weights, and verification rules for safety-critical plant workflows.
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* TAB 2: Solver Fabric */}
          {activeTab === 'solvers' && (
            <div className="space-y-4">
              <p className="text-xs text-muted">
                The Solver Fabric is a modular computational layer providing specialized algorithmic, mathematical, and simulation capabilities behind a unified, typed runtime contract.
              </p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="rounded-lg border border-border bg-panel p-4 space-y-2">
                  <h4 className="text-xs font-bold text-accent uppercase">1. Mathematical &amp; Numerical</h4>
                  <p className="text-xs text-muted">
                    Exact fraction arithmetic (`Fraction`), arbitrary-precision bounds, bounded continuous linear programming, and HiGHS simplex/IPM execution via isolated child subprocesses.
                  </p>
                </div>
                <div className="rounded-lg border border-border bg-panel p-4 space-y-2">
                  <h4 className="text-xs font-bold text-accent uppercase">2. Optimization &amp; Operations Research</h4>
                  <p className="text-xs text-muted">
                    Linear programming (LP), mixed-integer linear programming (MILP), capacity-constrained production scheduling, resource allocation, and Pareto trade-off optimization.
                  </p>
                </div>
                <div className="rounded-lg border border-border bg-panel p-4 space-y-2">
                  <h4 className="text-xs font-bold text-accent uppercase">3. Constraint Solving</h4>
                  <p className="text-xs text-muted">
                    Constraint Satisfaction Problems (CSP), Boolean Satisfiability (SAT/SMT), temporal logic intervals, and operational regime safety rules.
                  </p>
                </div>
                <div className="rounded-lg border border-border bg-panel p-4 space-y-2">
                  <h4 className="text-xs font-bold text-accent uppercase">4. Statistical &amp; Data-Driven</h4>
                  <p className="text-xs text-muted">
                    Principal Component Analysis with Squared Prediction Error (PCA-SPE), Out-of-Distribution (OOD) abstention probes, and conformal prediction bands.
                  </p>
                </div>
                <div className="rounded-lg border border-border bg-panel p-4 space-y-2">
                  <h4 className="text-xs font-bold text-accent uppercase">5. Causal &amp; Graph-Based</h4>
                  <p className="text-xs text-muted">
                    Root Cause Analysis (RCA), causal Bayesian graphs, physical topology traversals, fault trees, and propagation delay estimation.
                  </p>
                </div>
                <div className="rounded-lg border border-border bg-panel p-4 space-y-2">
                  <h4 className="text-xs font-bold text-accent uppercase">6. Physics &amp; Simulation</h4>
                  <p className="text-xs text-muted">
                    First-principles pneumatic compressor physics (MetroPT-3), thermodynamic air chamber models, component degradation simulators, and scenario counterfactuals.
                  </p>
                </div>
                <div className="rounded-lg border border-border bg-panel p-4 space-y-2 md:col-span-2">
                  <h4 className="text-xs font-bold text-accent uppercase">7. Rule-Based Solving</h4>
                  <p className="text-xs text-muted">
                    Deterministic engineering thresholds, plant safety envelopes, ISO/IEC maintenance standards, and fail-safe interlocks.
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* TAB 3: Four Intelligence Engines */}
          {activeTab === 'engines' && (
            <div className="space-y-4">
              <p className="text-xs text-muted">
                Platform-level intelligence engines that coordinate, govern, contextualize, and evaluate decisions across the entire industrial asset lifecycle.
              </p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="rounded-lg border border-border bg-panel p-4 space-y-2">
                  <span className="text-[10px] font-semibold text-accent uppercase">Engine 1</span>
                  <h4 className="text-sm font-bold text-ink">Ontology &amp; Context Engine</h4>
                  <p className="text-xs text-muted leading-relaxed">
                    Maintains asset digital passports, equipment sensor topologies, operational regimes, historical maintenance logs, and physical relational graphs across industrial systems.
                  </p>
                </div>

                <div className="rounded-lg border border-border bg-panel p-4 space-y-2">
                  <span className="text-[10px] font-semibold text-accent uppercase">Engine 2</span>
                  <h4 className="text-sm font-bold text-ink">Workflow Orchestration Engine</h4>
                  <p className="text-xs text-muted leading-relaxed">
                    Drives deterministic multi-stage state transitions, solver timeouts, resource budgeting, execution retries, and clean error recovery across the computational fabric.
                  </p>
                </div>

                <div className="rounded-lg border border-border bg-panel p-4 space-y-2">
                  <span className="text-[10px] font-semibold text-accent uppercase">Engine 3</span>
                  <h4 className="text-sm font-bold text-ink">Decision Intelligence Engine</h4>
                  <p className="text-xs text-muted leading-relaxed">
                    Conducts multi-objective trade-off evaluations, computes Pareto frontiers (e.g. maintenance cost vs downtime risk), sensitivity bounds, and uncertainty quantifications.
                  </p>
                </div>

                <div className="rounded-lg border border-border bg-panel p-4 space-y-2">
                  <span className="text-[10px] font-semibold text-accent uppercase">Engine 4</span>
                  <h4 className="text-sm font-bold text-ink">Governed Agent Engine</h4>
                  <p className="text-xs text-muted leading-relaxed">
                    Orchestrates sandboxed AI tool usage, enforces epistemic labeling, requires human approval gates, and commits all agent actions to tamper-evident cryptographic audit logs.
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* TAB 4: 14-Stage Workflow */}
          {activeTab === 'workflow' && (
            <div className="space-y-4">
              <p className="text-xs text-muted">
                The continuous 14-stage decision loop from raw industrial telemetry ingestion to closed-loop learning:
              </p>
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-7 gap-2 text-center text-xs">
                {[
                  { step: '01', name: 'Sense', desc: 'Telemetry Intake' },
                  { step: '02', name: 'Validate', desc: 'Signal Integrity' },
                  { step: '03', name: 'Understand', desc: 'Context Extraction' },
                  { step: '04', name: 'Retrieve', desc: 'Knowledge & RAG' },
                  { step: '05', name: 'Diagnose', desc: 'RCA & Fault Tree' },
                  { step: '06', name: 'Uncertainty', desc: 'OOD & Calibration' },
                  { step: '07', name: 'Formulate', desc: 'Canonical Problem' },
                  { step: '08', name: 'Select', desc: 'Solver Routing' },
                  { step: '09', name: 'Solve', desc: 'HiGHS Execution' },
                  { step: '10', name: 'Simulate', desc: 'Digital Twin Check' },
                  { step: '11', name: 'Verify', desc: 'Independent Verifier' },
                  { step: '12', name: 'Trade-offs', desc: 'Pareto & Impact' },
                  { step: '13', name: 'Human Review', desc: 'Engineer Sign-off' },
                  { step: '14', name: 'Verify & Learn', desc: 'Outcome Ledger' },
                ].map((item) => (
                  <div key={item.step} className="rounded border border-border bg-panel p-2.5 space-y-1">
                    <span className="text-[10px] font-mono text-accent">{item.step}</span>
                    <p className="font-bold text-ink">{item.name}</p>
                    <p className="text-[10px] text-muted">{item.desc}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* TAB 5: Production Planning */}
          {activeTab === 'planning' && (
            <div className="space-y-4">
              <div className="rounded-lg border border-border bg-panel p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-bold text-ink">First Implemented Domain Vertical: Production Planning</h3>
                  <span className="rounded bg-ok/15 px-2 py-0.5 text-[10px] font-semibold text-ok">
                    64 Dedicated Tests Passed
                  </span>
                </div>
                <p className="text-xs text-muted leading-relaxed">
                  A high-fidelity industrial optimization slice modeling continuous manufacturing allocation problems with deterministic linear programming compilation:
                </p>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                  <div className="rounded border border-border/80 bg-surface/50 p-3 space-y-1">
                    <p className="font-semibold text-ink">Continuous Decision Quantities</p>
                    <p className="text-muted">Production volumes (x_j &gt;= 0) across multi-product continuous lines with exact float representations.</p>
                  </div>
                  <div className="rounded border border-border/80 bg-surface/50 p-3 space-y-1">
                    <p className="font-semibold text-ink">Shared Resource Capacities</p>
                    <p className="text-muted">Rigid resource constraints (labor hours, machine runtimes, raw material reserves) modeled as linear matrix bounds.</p>
                  </div>
                  <div className="rounded border border-border/80 bg-surface/50 p-3 space-y-1">
                    <p className="font-semibold text-ink">Dual Business Objectives</p>
                    <p className="text-muted">Gross margin maximization or unit cost minimization under market demand upper and lower contract bounds.</p>
                  </div>
                  <div className="rounded border border-border/80 bg-surface/50 p-3 space-y-1">
                    <p className="font-semibold text-ink">Traceability &amp; Evidence-Gated Reports</p>
                    <p className="text-muted">Full compilation manifests mapping business identifiers to matrix indices; reports are strictly gated by trusted solver verification evidence.</p>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB 6: Implementation Status Matrix */}
          {activeTab === 'status' && (
            <div className="space-y-4">
              <p className="text-xs text-muted">
                Honest, evidence-grounded engineering status. We strictly separate verified code from active work and planned architecture.
              </p>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
                {/* Implemented */}
                <div className="rounded-lg border border-ok/40 bg-panel p-4 space-y-3">
                  <div className="flex items-center gap-2">
                    <span className="h-2.5 w-2.5 rounded-full bg-ok" />
                    <h4 className="font-bold text-ink uppercase tracking-wider text-[11px]">Implemented &amp; Tested</h4>
                  </div>
                  <ul className="space-y-2 text-muted">
                    <li className="flex items-start gap-1.5">
                      <span className="text-ok font-bold">✓</span>
                      <span>Solver Fabric runtime contracts (`CanonicalLP`, exact arithmetic, intake)</span>
                    </li>
                    <li className="flex items-start gap-1.5">
                      <span className="text-ok font-bold">✓</span>
                      <span>HiGHS continuous LP worker &amp; adapter with Win32 Job Object isolation (21/21 tests)</span>
                    </li>
                    <li className="flex items-start gap-1.5">
                      <span className="text-ok font-bold">✓</span>
                      <span>Production Planning formulation slice (64 dedicated tests, 220 combined tests)</span>
                    </li>
                    <li className="flex items-start gap-1.5">
                      <span className="text-ok font-bold">✓</span>
                      <span>Independent mathematical verifier &amp; dual certificate validation</span>
                    </li>
                    <li className="flex items-start gap-1.5">
                      <span className="text-ok font-bold">✓</span>
                      <span>MetroPT-3 air compressor anomaly detection, PCA-SPE &amp; evidence packages</span>
                    </li>
                    <li className="flex items-start gap-1.5">
                      <span className="text-ok font-bold">✓</span>
                      <span>Cryptographic SHA-256 audit logs &amp; safety boundary tests</span>
                    </li>
                  </ul>
                </div>

                {/* In Progress */}
                <div className="rounded-lg border border-warn/40 bg-panel p-4 space-y-3">
                  <div className="flex items-center gap-2">
                    <span className="h-2.5 w-2.5 rounded-full bg-warn" />
                    <h4 className="font-bold text-ink uppercase tracking-wider text-[11px]">In Progress</h4>
                  </div>
                  <ul className="space-y-2 text-muted">
                    <li className="flex items-start gap-1.5">
                      <span className="text-warn font-bold">◷</span>
                      <span>Phase B end-to-end integration (orchestrator worker dispatch &amp; verifier binding)</span>
                    </li>
                    <li className="flex items-start gap-1.5">
                      <span className="text-warn font-bold">◷</span>
                      <span>Production Planning execution loop integration with live HiGHS worker</span>
                    </li>
                    <li className="flex items-start gap-1.5">
                      <span className="text-warn font-bold">◷</span>
                      <span>Multi-scenario Pareto trade-off curve generation UI integration</span>
                    </li>
                    <li className="flex items-start gap-1.5">
                      <span className="text-warn font-bold">◷</span>
                      <span>Comprehensive evaluation benchmark harness validation</span>
                    </li>
                  </ul>
                </div>

                {/* Planned */}
                <div className="rounded-lg border border-border bg-panel p-4 space-y-3">
                  <div className="flex items-center gap-2">
                    <span className="h-2.5 w-2.5 rounded-full bg-muted" />
                    <h4 className="font-bold text-ink uppercase tracking-wider text-[11px]">Approved Architecture / Planned</h4>
                  </div>
                  <ul className="space-y-2 text-muted">
                    <li className="flex items-start gap-1.5">
                      <span className="text-muted font-bold">○</span>
                      <span>Full multi-solver concurrent arbitration (MILP, CSP, physics engines)</span>
                    </li>
                    <li className="flex items-start gap-1.5">
                      <span className="text-muted font-bold">○</span>
                      <span>Deep physics coupling with fluid-thermal Digital Twin simulator</span>
                    </li>
                    <li className="flex items-start gap-1.5">
                      <span className="text-muted font-bold">○</span>
                      <span>Enterprise distributed solver worker pool deployment</span>
                    </li>
                    <li className="flex items-start gap-1.5">
                      <span className="text-muted font-bold">○</span>
                      <span>Industrial OT edge connector protocol implementation (OPC-UA / MQTT)</span>
                    </li>
                  </ul>
                </div>
              </div>
            </div>
          )}
        </section>

        {/* Footer */}
        <footer className="border-t border-border pt-8 text-xs text-muted flex flex-col sm:flex-row items-center justify-between gap-4">
          <div>
            <p className="font-semibold text-ink">QORVEXA — Industrial Decision Intelligence Platform</p>
            <p>Intelligence Beyond Automation. Advisory &amp; Read-Only.</p>
          </div>
          <div className="flex items-center gap-4">
            <a
              href="https://github.com/Mohamed-Osama-ai7/QORVEXA"
              target="_blank"
              rel="noreferrer"
              className="text-accent hover:underline"
            >
              GitHub Repository
            </a>
            <span>•</span>
            <span>Cairo, Egypt</span>
          </div>
        </footer>
      </main>
    </div>
  )
}
