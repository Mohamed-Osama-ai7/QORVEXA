# Member 4 — RUL Feasibility Assessment

## Decision

**Remaining Useful Life (RUL) is not supported by the currently approved MetroPT-3 dataset.**
No RUL labels, baseline, or performance metrics were created. The available evidence does not
identify a per-asset failure endpoint or a run-to-failure history from which labels could be
derived without invention.

## Dataset evidence inspected

- The approved MetroPT-3 adapter documents real APU compressor telemetry at approximately
  10-second intervals, downsampled from a 1 Hz source. It allowlists a timestamp and sensor
  columns; it does not define an asset-life/cycle target or an RUL field:
  [`backend/src/factorymind/data/metropt3.py`](../backend/src/factorymind/data/metropt3.py).
- The adapter explicitly describes the dataset as unlabelled. Its four F1–F4 air-leak reports
  are weak, coarse event intervals with uncertain maintenance alignment and timezone. They are
  not per-sample failure onset labels, terminal-life observations, or repeated run-to-failure
  trajectories.
- The repository contains no MetroPT-3 CSV, ZIP, or Parquet dataset file to independently
  re-inspect in this workspace. This assessment is grounded in the adapter contract and
  repository documentation, not a fresh scan of raw observations.
- AI4I 2020 is a separate synthetic classification benchmark. Its `Machine failure` target is
  row-level, while `UDI` is only a row-order proxy; the CSV has no explicit timestamp or machine
  identifier. It cannot provide machine-specific elapsed life or RUL labels. See
  [`docs/m3-wp1-multimodel-ml.md`](m3-wp1-multimodel-ml.md).
- The Digital Twin's `continued_operation` leak escalation is a configured simulation scenario,
  not observed degradation data or a validated RUL target. Its projections must not be used as
  ground-truth remaining-life labels. See
  [`backend/src/factorymind/twin/physics.py`](../backend/src/factorymind/twin/physics.py).

## Data required before an RUL baseline

1. A defined asset population with stable per-machine/equipment IDs and independent operating
   trajectories.
2. A documented end-of-life target per asset: verified failure time/cycle or an approved
   physical degradation threshold, including failure mode.
3. Time-in-service or cycle counts aligned to every sensor observation, with consistent time
   basis and sampling metadata.
4. Maintenance, repair, replacement, censoring, and preventive-removal records so a trajectory
   ending without failure is not mislabeled as a failure.
5. Sufficient run-to-failure or degradation trajectories and the operational context needed to
   distinguish degradation from load, regime, environment, and sensor faults.
6. Architecture-owner approval for the dataset, target definition, and handling of censored
   units.

If these requirements are met, define RUL from the approved failure endpoint and elapsed
operating time/cycles, then evaluate on held-out assets (not random windows from the same
trajectory). Report at least MAE and RMSE in the target's declared unit; do not report metrics
until those labels and splits are validated.

## Dataset proposal — approval required

**Candidate only:** NASA Ames Prognostics Center of Excellence's
[Turbofan Engine Degradation Simulation (C-MAPSS) dataset](https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/).
NASA describes multiple simulated degradation sets and sensor channels intended to characterize
fault evolution. It is structurally relevant to generic prognostics/RUL methodology, but its
turbofan engine domain is not the MetroPT-3 compressor/APU. It would be a separate benchmark,
not evidence of MetroPT-3 or QORVEXA asset performance. Dataset approval, label construction,
licensing/attribution, and domain scope must be confirmed before acquisition or implementation.

## Scope boundary

Until approval and suitable run-to-failure data are available, retain the existing MetroPT-3
anomaly, weak-event, and predictive-classification tasks as distinct outputs. Do not convert
failure windows, anomaly scores, classifier probabilities, or Digital Twin projections into RUL.
