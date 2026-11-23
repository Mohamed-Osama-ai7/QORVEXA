"""Deterministic Markdown report generator for evaluation run directories.

Renders evaluation summaries into structured Markdown tables:
* Run header (suite, system, cases, code fingerprint).
* Overall metrics with sample counts ($N$), means, and 95% confidence intervals.
* Conservative binary rates (treating undecided outcomes as failure).
* Split breakdown (development, validation, test, fixture).
* Provenance breakdown.
* Explicit Limitations & Benchmark Governance section.

Deterministic: contains zero timestamps unless ``include_env=True`` is explicitly requested.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _format_ci(interval: dict[str, Any] | None) -> str:
    if not interval:
        return "—"
    state = interval.get("state")
    if state == "scored":
        low = interval.get("lower")
        high = interval.get("upper")
        if low is not None and high is not None:
            return f"[{low:.4f}, {high:.4f}]"
        return "scored (no bounds)"
    if state == "degenerate":
        low = interval.get("lower")
        return f"[{low:.4f}, {low:.4f}] (degenerate)" if low is not None else "degenerate"
    if state == "unavailable":
        reason = interval.get("reason", "insufficient samples")
        return f"unavailable ({reason})"
    return str(state)


def _format_rate(val: float | None) -> str:
    return f"{val:.4f}" if val is not None else "—"


def render_metrics_table(metrics: dict[str, Any], *, show_ci: bool = True) -> str:
    lines: list[str] = []
    if show_ci:
        lines.append("| Metric | Mean | N (Scored) | 95% CI | Method | States |")
        lines.append("|---|---|---|---|---|---|")
    else:
        lines.append("| Metric | Mean | N (Scored) | States |")
        lines.append("|---|---|---|---|")

    for name, data in sorted(metrics.items()):
        mean_str = _format_rate(data.get("mean"))
        n_scored = data.get("n_scored", 0)
        states = data.get("states", {})
        states_str = ", ".join(f"{k}: {v}" for k, v in sorted(states.items()))

        if show_ci:
            ci = data.get("interval")
            ci_str = _format_ci(ci)
            method = ci.get("method", "—") if ci else "—"
            lines.append(
                f"| `{name}` | {mean_str} | {n_scored} | {ci_str} | {method} | {states_str} |"
            )
        else:
            lines.append(f"| `{name}` | {mean_str} | {n_scored} | {states_str} |")
    return "\n".join(lines)


def render_conservative_table(metrics: dict[str, Any]) -> str | None:
    conservative_items = [
        (name, data["conservative"])
        for name, data in sorted(metrics.items())
        if "conservative" in data and data["conservative"] is not None
    ]
    if not conservative_items:
        return None

    lines: list[str] = [
        "| Metric | Measured Rate | Scored N | Undecided N | Conservative Rate |",
        "|---|---|---|---|---|",
    ]
    for name, c in conservative_items:
        m_rate = _format_rate(c.get("measured_rate"))
        c_rate = _format_rate(c.get("conservative_rate"))
        s_n = c.get("scored_n", 0)
        u_n = c.get("undecided_n", 0)
        lines.append(f"| `{name}` | {m_rate} | {s_n} | {u_n} | **{c_rate}** |")
    return "\n".join(lines)


def generate_markdown_report(run_dir: Path, *, include_env: bool = False) -> str:
    """Generate deterministic Markdown report from an evaluation run directory."""
    summary_path = run_dir / "summary.json"
    manifest_path = run_dir / "manifest.json"
    run_info_path = run_dir / "run_info.json"

    if not summary_path.is_file():
        raise FileNotFoundError(f"Missing summary.json in {run_dir}")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    )
    run_info = (
        json.loads(run_info_path.read_text(encoding="utf-8"))
        if (include_env and run_info_path.is_file())
        else None
    )

    suite = manifest.get("suite", "evaluation")
    system = manifest.get("system", "unknown")
    case_count = manifest.get("case_count", summary.get("overall", {}).get("cases", 0))
    code_fp = manifest.get("code", {}).get("fingerprint", "unknown")

    doc: list[str] = [
        f"# QORVEXA Evaluation Report — {suite.upper()}",
        "",
        "## 1. Run Summary",
        f"- **Suite:** `{suite}`",
        f"- **System Under Test:** `{system}`",
        f"- **Total Cases Evaluated:** `{case_count}`",
        f"- **Harness Version:** `{manifest.get('harness_version', '0.1.0')}`",
        f"- **Candidate Code Fingerprint:** `{code_fp}`",
    ]

    if run_info:
        doc.extend(
            [
                "",
                "### Environment Metadata",
                f"- **Git SHA:** `{run_info.get('git_sha')}` (dirty: `{run_info.get('dirty')}`)",
                f"- **Platform:** `{run_info.get('platform')}`",
                f"- **Python:** `{run_info.get('python_version')}`",
                f"- **Timestamp (UTC):** `{run_info.get('created_utc')}`",
            ]
        )

    # Interpretation statement if present
    interp = manifest.get("interpretation")
    if interp and isinstance(interp, dict):
        statement = interp.get("statement")
        if statement:
            doc.extend(["", "> [!NOTE]", f"> **Benchmark Statement:** {statement}"])

    overall = summary.get("overall", {})
    overall_metrics = overall.get("metrics", {})

    doc.extend(
        [
            "",
            "## 2. Overall Performance Metrics",
            f"Evaluated across {overall.get('cases', 0)} case(s).",
            "",
            render_metrics_table(overall_metrics, show_ci=True),
        ]
    )

    conservative_tbl = render_conservative_table(overall_metrics)
    if conservative_tbl:
        doc.extend(
            [
                "",
                "### 2.1 Conservative Binary Rates",
                "> In conservative accounting, `undecided` outcomes are counted as failures in the "
                "denominator alongside scored cases.",
                "",
                conservative_tbl,
            ]
        )

    by_split = summary.get("by_split", {})
    if by_split:
        doc.extend(["", "## 3. Performance by Split"])
        for split_name, split_data in sorted(by_split.items()):
            split_cases = split_data.get("cases", 0)
            doc.extend(
                [
                    "",
                    f"### Split: `{split_name}` ({split_cases} case(s))",
                    "",
                    render_metrics_table(
                        split_data.get("metrics", {}), show_ci=split_name != "fixture"
                    ),
                ]
            )

    by_prov = summary.get("by_provenance", {})
    if by_prov:
        doc.extend(["", "## 4. Performance by Provenance"])
        for prov_name, prov_data in sorted(by_prov.items()):
            prov_cases = prov_data.get("cases", 0)
            doc.extend(
                [
                    "",
                    f"### Provenance: `{prov_name}` ({prov_cases} case(s))",
                    "",
                    render_metrics_table(prov_data.get("metrics", {}), show_ci=False),
                ]
            )

    # Limitations section
    doc.extend(
        [
            "",
            "## 5. Limitations & Epistemic Boundaries",
            "1. **Sampling Variability vs Factory Generalization:** Confidence intervals measure "
            "sampling variability within the evaluated benchmark set only. They do not constitute "
            "guarantees of generalization to unobserved machines, altered operational regimes, or "
            "unseen industrial facilities.",
            "2. **Held-Out Evidence Status:** Unless explicit independent test splits are "
            "evaluated in `--final` mode, results reflect development iterations and contract "
            "fixtures. Development scores must not be cited as empirical claims of field accuracy.",
            "3. **Statistical Power:** Benchmark sets with N < 30 observations or K < 10 clusters "
            "are not statistically powered for general inference; standard bootstrap intervals are "
            "refused or flagged as exploratory.",
            "4. **Corpus & Language Scope:** Retrieval and diagnostic claims in languages other "
            "than English (e.g. Arabic) remain disabled until bilingual reference documents and "
            "native tokenizers are integrated (Decisions D-1, D-2).",
            "",
        ]
    )

    return "\n".join(doc)
