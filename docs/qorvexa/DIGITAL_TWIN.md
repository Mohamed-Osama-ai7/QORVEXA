# Digital Twin & Industrial Simulation Engine

Package `backend/src/qorvexa/industrial/digital_twin/` · tests `backend/tests/test_industrial_digital_twin.py` (48).
(`backend/tests/test_digital_twin.py` belongs to the earlier physics-informed twin and is unrelated.)

Discrete-event simulation (DES) of production lines. Advisory and read-only: it never commands equipment.
Simulation evidence is **not** a measurement of the real factory and **not** an optimisation guarantee.

`Ontology snapshot → FactoryModel → validation + Units → DES kernel → metrics → scenarios → hash-bound report → DI evidence`

## Modules
| Module | Responsibility |
|---|---|
| `contracts.py` | versioned typed model (`qorvexa-twin-model/1`): machines, buffers, calendars/shifts/breaks, planned downtime, failures, breakdowns, resource pools, materials + deliveries, linear routings, setups, scrap, products/batches, orders, explicit policies; typed scenario changes |
| `validation.py` | blocking issues for every missing/unknown/incompatible input; exact conversion of every duration to integer ticks (1 tick = 1 s) via the Units Engine, recorded as `UnitEvidence`; calendar window construction |
| `kernel.py` | DES kernel: heap of `(time, rank, seq)` events, dispatch fix-point, state accounting, blocking, starvation, interruptions, failures/repair, materials, pools, deadlock detection, event/time/cancel budgets |
| `metrics.py` | throughput, WIP, lead/cycle/wait time, lateness, machine time states and utilization, buffer occupancy, pool utilization, material balance, piece conservation, bottleneck candidates |
| `stochastic.py` | DETERMINISTIC / TRIANGULAR / EXPONENTIAL with explicit parameters; SHA-256-derived independent streams per (seed, replication, stream); replication statistics |
| `report.py` | `simulate()` orchestration and the versioned `SimulationReport` (`content_sha256`, `report_sha256`, `verify_report`) |
| `scenario.py` | immutable baselines, typed changes, `apply_scenario`, `simulate_scenario`, lineage-checked `compare` |
| `plan.py` | verified Production Planning quantities → orders via an explicit `QuantityPolicy`; exact solver quantity preserved |
| `ontology_adapter.py` | confirmed Ontology snapshot + explicit `OperationalInputs` → `FactoryModel` |
| `evidence.py` | `SimulationEvidence` for Decision Intelligence (verified report, comparisons, assumptions, statement of what it is not) |
| `store.py` | `SimulationStore` protocol; in-memory and isolated SQLite (`twin_runs`) with hash re-verification on load; `CheckpointStore` extension point |

## Simulation semantics
* **Clock:** integer seconds. Deterministic durations must convert *exactly* (e.g. `1.5 min` → 90 s is accepted, `0.5 s` is
  `DURATION_NOT_ON_RESOLUTION`). Sampled durations use the declared `CEIL_TO_TICK` policy (recorded as an assumption).
* **Event order at one instant:** PHASE_END → REPAIR → SHIFT_ON → PLANNED_END → SHIFT_OFF → PLANNED_START → FAIL → DELIVERY →
  RELEASE → HORIZON, then sequence number; afterwards a dispatch fix-point admits released jobs, unloads blocked machines and starts work
  on idle available machines (machine id order; jobs by FIFO / PRIORITY / EDD).
* **Jobs:** orders are split into transfer batches; per-job processing = per-piece time × pieces. Buffers count pieces; a batch larger
  than any buffer on its route is rejected (`BATCH_EXCEEDS_BUFFER`).
* **Blocking after service:** a finished job waits on its machine until the next buffer has room. **Starvation:** available and idle with no
  eligible job. **WAITING_RESOURCE:** jobs exist but material or pool units are missing.
* **Availability:** shift windows minus breaks, minus planned downtime, minus failures. Interrupted setups/operations follow the explicit
  `InterruptPolicy` (`RESUME_REMAINING` or `RESTART`). Repairs run in wall-clock time. Pools stay held during interruptions (the other
  choice is rejected as `UNSUPPORTED_POLICY`, not silently ignored). No preemption between jobs.
* **Materials** are consumed at operation start and must be in the material's own unit (`UNIT_CONVERSION_REQUIRED` otherwise).
* **Scrap** only when `scrap_rate` is given: exact accumulation in deterministic runs, per-piece Bernoulli in stochastic runs.
* **Deadlock:** a closed set of blocked machines whose target buffers can only be drained by members of the set stops the run (`DEADLOCK`).
  Other guards: `max_events`, wall-clock `timeout_s`, a cancellation token, model size limits.
* **Each machine instant is in exactly one state:** PROCESSING, SETUP, BLOCKED, STARVED, WAITING_RESOURCE, DOWN, PLANNED_DOWNTIME, OFF_SHIFT
  (tests assert they sum to the window).

## Metric definitions (window = [0, end of run])
utilization = (processing + setup) / (window − off-shift − planned downtime) · throughput = completed pieces per hour of window ·
WIP = time-average pieces in buffers and on machines · lead time = completion − order release · cycle time = completion − first operation
start · waiting = time in buffers. Averages are per transfer batch. Bottlenecks are *candidates* with reasons (utilization ≥ 0.85,
highest utilization, largest input queue, longest upstream blocking). No OEE, availability % or efficiency is reported.

## Stochastic layer
Seeds are derived per (seed, replication, stream), so replays are bit-identical and streams do not interfere. Summaries give n, mean, median,
nearest-rank p10/p90, min/max, sample variance and standard error; a 95 % Student-t interval on the mean is given only for ≥ 10 independent
replications of a stochastic model. Deterministic models report "no interval applies".

## Measured results (synthetic models; not real-plant validation)
Tandem line (cutter 60 s, welder 100 s, buffer 1, 10 pieces) — expected values come from an independent recurrence, not from the simulator:

| Run | Window | Throughput /h | Avg WIP | Avg lead time | Welder util. | Cutter blocked |
|---|---|---|---|---|---|---|
| Baseline | 1060 s (reference 1060) | 33.96 | 5.75 | 610 s | 0.943 | 260 s |
| Welder at 75 s | 810 s (reference 810) | 44.44 | 5.83 | 472.5 s | 0.926 | 60 s |
| Welder planned downtime 300 s | 1360 s (= baseline + 300) | 26.47 | 6.25 | 850 s | 0.943 | 560 s |

Five parameter sets (including buffer 2/3 and cutter-bound lines) match the recurrence exactly for completion times, blocked and starved time.

Synthetic two-product line (5 machines, shared cutters/welder/packer, 1 operator for weld+pack, finite buffers, two shifts with breaks, steel
with a delivery, setups, planned PRESS downtime; 350 pieces): baseline window 53 825 s, 23.41 pieces/h, bottleneck candidate WELD
(utilization 0.671), 1 late order, 261 events in ≈ 19 ms. Adding a WELD breakdown 10:00–12:00 extends the run by 5 400 s (7 200 s minus the
30-minute break it overlaps), throughput 21.27/h, 2 late orders. Adding a second press: 51 225 s, 24.60/h.

Stochastic tandem (triangular cutter 50/60/90 s, exponential welder failures, mean 30 min, triangular repairs; 40 pieces; 30 replications,
seed 5, ≈ 132 ms): throughput mean 26.30/h (median 27.33, p10 19.04, p90 31.39, SE 0.84, 95 % CI 24.57–28.02).

## Integration contracts (for Antigravity)
* Build models only from `build_from_ontology(view, ontology, line_id, OperationalInputs)` (confirmed, undisputed relations; proposed or
  disputed required relations → `UNCONFIRMED_INPUT`; factory mismatch → `ONTOLOGY_FACTORY_MISMATCH`) or from explicitly supplied models.
* Show `ValidationReport.issues` (blocking first) instead of simulating; never fill unknown processing times, calendars, buffers or stock.
* Plans: `import_plan(report, policy=..., batch_sizes=..., release_at=...)` — the user must choose the quantity policy; show
  `solver_quantity`, `simulated_quantity` and `difference`.
* Run with explicit `seed`, `replications` (≤ 1000), `max_events`, `timeout_s`; pass a cancellation token for user cancel.
* Display `report.kind` (DETERMINISTIC_SIMULATION vs STOCHASTIC_ESTIMATE), `status`, `assumptions`, `limitations`, `disclaimer`; never present
  results as measurements or solver proof. Verify with `verify_report` after loading.
* Persist with `SqliteSimulationStore(path)` (own table); hand `to_evidence(report, comparisons)` to Decision Intelligence.

## Limitations (genuine)
* Linear routings only; single-server machines (parallel capacity = more machines); one processing rate per operation (heterogeneous
  machine rates block the ontology build).
* No preemption, no sequence-dependent setup matrices, no labour skills/calendars beyond pools, no transport times, no assembly/disassembly
  (bills of materials with sub-assemblies), no rework loops other than through routing.
* Material is consumed at start; no partial consumption, shelf life or lot tracking. Failures are time-based on wall-clock time, not usage.
* Calendars repeat daily; no weekly patterns, holidays or time zones. One tick = one second.
* Statistics assume independent replications; no warm-up deletion or steady-state analysis (runs are finite-horizon).
* Runs are replayed from model + seed; mid-run checkpoints are an interface only.
* All validation data is synthetic.
