# QORVEXA starter benchmarks (v0)

Small, partly synthetic starter sets for the offline evaluation harness
(`qorvexa.evaluation`). They exercise the harness and the Task 02A metrics. **They are
not a statistically powered benchmark and their numbers are not product claims.**

| File | Records | What it is |
|---|---|---|
| `rca_cases.v0.jsonl` | 16 | RCA ground truth: 4 `metropt3_observed`, 2 `synthetic_scenario`, 10 `adversarial_contract` |
| `rca_predictions.fixture.v0.jsonl` | 12 lines | Hand-written system outputs for the adversarial and synthetic cases, including one broken JSON line, one invalid record and one prediction for an unknown case. Not RCA engine output. |
| `retrieval_cases.v0.jsonl` | 13 | 9 `project_document_retrieval`, 1 `dataset_document_retrieval`, 3 `out_of_scope_probe` |

## Provenance rules

- **`metropt3_observed`**: real MetroPT-3 telemetry windows for operator reports F1–F4. The only
  label is the operator's day-level "Air leak" report (`operator_report_coarse`). They are `evaluation_role: development` cases (the RCA engine was built with them in view), never held-out. Evidence keys,
  abstention and critical contradictions are left `null` (unlabelled), never guessed.
- **`synthetic_scenario`**: scenario specifications labelled from physical reasoning
  (`physics_authored`), not from RCA thresholds. No evidence-package generator exists yet, so
  they can only be scored against supplied predictions. They are not incidents.
- **`adversarial_contract`**: fixtures that check the scoring contract (critical contradictions,
  missing evidence, abstention, malformed output, unsupported conclusions). They are not incidents.
- **`project_document_retrieval`**: 9 of the 11 queries in `knowledge/eval.py:DEFAULT_EVAL_CASES`,
  copied verbatim. `tp3 pressure limits` is left out, and the sensor query moved to
  `dataset_document_retrieval`. The relevant documents are the team's own passport and design
  docs, written by the same people who wrote the queries, so there is a circularity risk. They are
  not industrial manuals.
- **`dataset_document_retrieval`**: needs the MetroPT-3 PDF (`--raw-dir`). Without it the case
  reports `no_relevant_in_corpus`, not a score.

## Commands (from `backend/`)

```bash
uv run python -m qorvexa.evaluation validate benchmarks/qorvexa/rca_cases.v0.jsonl
uv run python -m qorvexa.evaluation rca --cases benchmarks/qorvexa/rca_cases.v0.jsonl --predictions benchmarks/qorvexa/rca_predictions.fixture.v0.jsonl --out D:/QORVEXA/artifacts/evaluations/rca/fixture
uv run python -m qorvexa.evaluation rca-run --cases benchmarks/qorvexa/rca_cases.v0.jsonl --out D:/QORVEXA/artifacts/evaluations/rca-real/<label>
uv run python -m qorvexa.evaluation verify D:/QORVEXA/artifacts/evaluations/rca-real/<label> --check-code
uv run python -m qorvexa.evaluation retrieval --cases benchmarks/qorvexa/retrieval_cases.v0.jsonl --out D:/QORVEXA/artifacts/evaluations/retrieval/v0 --raw-dir D:/QORVEXA/data/raw
```

## Adding a case

1. Append one JSON object per line using the schema in `docs/qorvexa/EVALUATION_STRATEGY.md` §D.
   `case_id` must be unique, lowercase, `[a-z0-9][a-z0-9_.-]*`.
2. Use `null` for anything you cannot label from evidence; use `ground_truth_status:
   "insufficient"` when no category or abstention label is justified.
3. Do not derive labels from the RCA engine's own thresholds or outputs.
4. Run `validate`; it must print `OK`. CI runs the same command.

## v1 files and splits (Task 03B)

`rca_cases.v1.jsonl` and `retrieval_cases.v1.jsonl` add `split`, `origin`, `cluster_id` (and, for retrieval, `content_origin`, `relevance_source`, explicit chunk ids). The v0 files stay valid and unchanged. `splits.v1.json` is the split manifest; check it with `splits check`. There are **no validation or test cases**; see `AUTHORING.md` for how independent cases must be created, and `docs/qorvexa/EVALUATION_STRATEGY.md` §E for the split rules, final mode, statistics and limits.

```bash
uv run python -m qorvexa.evaluation validate benchmarks/qorvexa/rca_cases.v1.jsonl
uv run python -m qorvexa.evaluation splits check --manifest benchmarks/qorvexa/splits.v1.json
uv run python -m qorvexa.evaluation audit --rca-cases benchmarks/qorvexa/rca_cases.v1.jsonl --retrieval-cases benchmarks/qorvexa/retrieval_cases.v1.jsonl --strict
uv run python -m qorvexa.evaluation report D:/QORVEXA/artifacts/evaluations/rca-real/<label> --with-env
```
