# Member 4 — Causal Intelligence Foundation

The first causal foundation is implemented in `backend/src/factorymind/causal/`. It defines
typed variables, directed edges, and a directed acyclic graph using Pydantic and the existing
`validate_dag` helper. It does not run causal discovery, estimate effects, perform interventions,
or assert that a graph edge is true.

## Schema and validation

- `CausalVariable` records a stable identifier, description, role (`measured`, `derived`, `latent`,
  or `outcome`), optional unit, source references, and a time reference.
- `CausalEdge` records stable directed endpoints, relation, the existing knowledge-graph
  `epistemic_type`, an explicit evidence status, provenance references, and assumptions.
- `CausalGraph` rejects duplicate variable/edge IDs, unknown endpoints, and directed cycles. A
  structurally valid DAG is not evidence that the relationships are causally valid.
- Models reject undeclared fields and require non-empty provenance and assumptions on every edge.

## MetroPT-3 hypothesis seed

`METROPT3_AIR_LEAKAGE_GRAPH` records domain hypotheses for air leakage increasing compressor load
duty and making unloaded TP3 pressure slope more negative. It also records unobserved downstream
air demand as a competing explanation for increased load duty. The operator-report outcome is
explicitly a weak interval label; its edge is marked `correlational_indicator` and
`weak_observational_support`.

The seed graph uses only existing domain references: the UCI #791 sensor schema and F1-F4 reports,
the asset passport’s failure-mode descriptions/unobserved quantities, and the existing window
feature definitions. Assumptions make the dependence on unloaded operating conditions, sensor
quality, reporting alignment, and competing air-demand explanations explicit. No statistical
association or automatic graph algorithm is interpreted as proof of causation.

No shared contracts or graph framework were added; the existing epistemic knowledge-graph types
and DAG validator are reused.

## Minimal SCM feature slice

`METROPT3_FEATURE_SCM` is a deterministic feature-computation slice over observed MetroPT-3
signals, not a complete physical SCM. Its equations reproduce the existing implementations:

- `load_duty = mean(Motor_current > 5.5 A)` for a telemetry window.
- `unloaded_tp3_slope` is the median TP3 per-minute difference for adjacent samples with
  `DV_eletric` inactive in both samples and an accepted timestamp gap.

The evaluator delegates to `block_features`; no coefficients are fitted and no operator labels
are used. Exogenous signals are merely boundary inputs to this calculation. Equation dependencies
must match the registered mechanism, resolve to variables in the referenced causal graph, and
form an acyclic dependency graph. This check validates the model specification, not causal
identification.

A focused reproducible example uses the test-only `simulate_apu(..., seed=19)` fixture, clearly
labelled synthetic; its outputs are checked against `block_features` directly. No causal effects
or interventions are estimated: the available data has no measured downstream air-demand signal,
and operator labels are weak intervals without timestamp certainty.

## Effect, intervention, and counterfactual identification

`assess_current_metropt3_analysis()` now returns a typed identification assessment for
effect-estimation, intervention, and individual-counterfactual questions. All three currently
return `insufficient_evidence`; effect estimates and confidence intervals are structurally
unavailable in this result type, and counterfactuals are never represented as observed facts.
Each assessment reports its question, treatment, outcome, confounder, required assumptions,
uncertainty, sensitivity limitations, unrun robustness/refutation checks, and evidence needed.

This is a deliberate identification gate, not a causal estimator. MetroPT-3 has no sample-level
air-leak treatment labels, downstream air demand is unobserved, and the operator reports are weak
intervals with uncertain timing. The current SCM only reproduces derived feature definitions and
does not describe plant response under an intervention. No adjustment, effect estimate, or
individual counterfactual is defensible from those ingredients.

The existing calibrated Digital Twin can generate bounded scenario rollouts, but its contract marks
them `simulated_projection`; it also documents unidentifiable demand-versus-leak contributions and
horizon limits. Those outputs and its parameter-sensitivity checks are not causal-effect estimates
or event-specific factual counterfactuals. They remain separate from this causal assessment.

## RCA integration

The existing incident workflow accepts a defined half-open event interval and assembles an
`EventEvidencePackage` with pre-event baseline, onset/evolution/peak, recovery, provenance, quality,
missing measurements, contradictions, and a timeline. The WP-B evidence-weighted RCA remains the
ranking engine; Member 4 adds traceability rather than a second scorer:

- Air-leak and downstream-demand candidates link to the corresponding existing graph edge IDs.
- Derived feature evidence links to the registered SCM equation IDs. These equations describe
  feature calculations only and are not plant-causality evidence.
- Every hypothesis reports `causal_conclusion_status="not_established"` and every assessment sets
  `root_cause_confirmed=false`. “Primary” means highest heuristic support only.
- Reports expose the deterministic ranking method, event-phase ordering, graph/SCM scope, and
  evidence references. Supporting, contradicting, and missing evidence remain separate.
- The current engine preserves the unmeasured downstream-air-demand alternative and abstains when
  evidence quality or diagnostic support falls below its existing gates. Unknown asset/dataset
  pairs do not receive MetroPT-3 graph or SCM references.

The incident and ad-hoc event RCA routes continue to use the same validated evidence-package
builder. No actuator, PLC, maintenance execution, or additional inference dependency was added.
