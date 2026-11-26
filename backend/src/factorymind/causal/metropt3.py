"""Documented MetroPT-3 causal hypotheses grounded in existing domain references."""

from __future__ import annotations

from factorymind.causal.schema import CausalEdge, CausalGraph, CausalVariable

METROPT3_AIR_LEAKAGE_GRAPH = CausalGraph(
    graph_id="metropt3.air-leakage-hypotheses.v1",
    asset_id="metropt3-apu-1",
    dataset_id="metropt3",
    variables=[
        CausalVariable(
            variable_id="metropt3.tp3_pressure",
            description="Pneumatic panel pressure measured by the TP3 sensor.",
            role="measured",
            unit="bar",
            source_refs=["UCI #791 CSV column TP3", "backend/src/factorymind/data/metropt3.py"],
            time_reference=(
                "Per-sample timestamp; timezone-naive local train time, timezone unspecified."
            ),
        ),
        CausalVariable(
            variable_id="metropt3.motor_current",
            description="Compressor motor phase current measured by the current sensor.",
            role="measured",
            unit="A",
            source_refs=[
                "UCI #791 CSV column Motor_current",
                "backend/src/factorymind/data/metropt3.py",
            ],
            time_reference=(
                "Per-sample timestamp; timezone-naive local train time, timezone unspecified."
            ),
        ),
        CausalVariable(
            variable_id="metropt3.dv_eletric",
            description=(
                "Digital outlet-valve signal; source spelling is retained. The feature pipeline "
                "uses it to identify loaded intervals."
            ),
            role="measured",
            unit="state (0/1)",
            source_refs=[
                "UCI #791 CSV column DV_eletric",
                "backend/src/factorymind/data/metropt3.py",
            ],
            time_reference=(
                "Per-sample timestamp; timezone-naive local train time, timezone unspecified."
            ),
        ),
        CausalVariable(
            variable_id="metropt3.load_duty",
            description=(
                "Fraction of samples in a window with Motor_current above the loaded threshold."
            ),
            role="derived",
            unit="fraction (0-1)",
            source_refs=[
                "backend/src/factorymind/anomaly/features.py:LOADED_CURRENT_A",
                "backend/src/factorymind/anomaly/features.py:window_features",
            ],
            time_reference="Clock-aligned 10-minute telemetry window.",
        ),
        CausalVariable(
            variable_id="metropt3.unloaded_tp3_slope",
            description=(
                "Median TP3 pressure slope while the compressor is unloaded; a more negative "
                "value indicates faster observed pressure decay."
            ),
            role="derived",
            unit="bar/min",
            source_refs=[
                "backend/src/factorymind/anomaly/features.py:unloaded_tp3_slope",
                "backend/src/factorymind/assets/passport.py:FailureModeSpec.air_leakage",
            ],
            time_reference="Clock-aligned 10-minute telemetry window.",
        ),
        CausalVariable(
            variable_id="metropt3.air_leakage",
            description=(
                "Latent candidate air-leak condition; not directly labelled at sample level "
                "in the MetroPT-3 dataset."
            ),
            role="latent",
            unit=None,
            source_refs=[
                "backend/src/factorymind/assets/passport.py:FailureModeSpec.air_leakage",
                "UCI #791 operator failure reports (weak interval labels only)",
            ],
            time_reference=(
                "Underlying condition time is not directly observed; operator report intervals "
                "are coarse and timezone-ambiguous."
            ),
        ),
        CausalVariable(
            variable_id="metropt3.downstream_air_demand",
            description=(
                "Unobserved train-side pneumatic demand (for example braking or door activity), "
                "a competing explanation for elevated compressor duty."
            ),
            role="latent",
            unit=None,
            source_refs=[
                "backend/src/factorymind/assets/passport.py:AssetPassport.unobserved_quantities",
                "backend/src/factorymind/anomaly/rca.py:abnormal_high_demand",
            ],
            time_reference="Relevant operating interval; not recorded in the supplied telemetry.",
        ),
        CausalVariable(
            variable_id="metropt3.operator_reported_air_leak",
            description=(
                "Operator-reported air-leak interval used only as a weak event-level evaluation "
                "label, not sample-level ground truth."
            ),
            role="outcome",
            unit="weak interval label",
            source_refs=[
                "UCI #791 operator failure reports F1-F4",
                "backend/src/factorymind/data/metropt3.py:FAILURE_REPORTS",
            ],
            time_reference=(
                "Coarse report start/end in local train time; timezone and maintenance alignment "
                "are uncertain."
            ),
        ),
    ],
    edges=[
        CausalEdge(
            edge_id="hyp.air-leakage-to-load-duty",
            source_id="metropt3.air_leakage",
            target_id="metropt3.load_duty",
            relation="may_increase_loaded_compressor_duty",
            epistemic_type="causal_hypothesis",
            evidence_status="domain_hypothesis",
            provenance_refs=[
                "backend/src/factorymind/assets/passport.py:FailureModeSpec.air_leakage",
                "docs/m1-anomaly-detection.md:Why MetroPT-3",
            ],
            assumptions=[
                "The compressor governor responds to pressure loss by increasing loaded operation.",
                "Unobserved downstream air demand is not the sole explanation for higher duty.",
            ],
        ),
        CausalEdge(
            edge_id="hyp.air-leakage-to-unloaded-pressure-decay",
            source_id="metropt3.air_leakage",
            target_id="metropt3.unloaded_tp3_slope",
            relation="may_make_unloaded_pressure_slope_more_negative",
            epistemic_type="causal_hypothesis",
            evidence_status="domain_hypothesis",
            provenance_refs=[
                "backend/src/factorymind/assets/passport.py:FailureModeSpec.air_leakage",
                "backend/src/factorymind/anomaly/features.py:unloaded_tp3_slope",
            ],
            assumptions=[
                "The compressor is in an observable unloaded hold during the slope interval.",
                "TP3 is functioning and its timing/measurement are representative.",
                "No concurrent demand change explains the observed pressure decay.",
            ],
        ),
        CausalEdge(
            edge_id="hyp.downstream-demand-to-load-duty",
            source_id="metropt3.downstream_air_demand",
            target_id="metropt3.load_duty",
            relation="may_increase_loaded_compressor_duty",
            epistemic_type="causal_hypothesis",
            evidence_status="domain_hypothesis",
            provenance_refs=[
                "backend/src/factorymind/assets/passport.py:FailureModeSpec.abnormal_high_demand",
                "backend/src/factorymind/anomaly/rca.py:abnormal_high_demand",
            ],
            assumptions=[
                (
                    "Increased train-side pneumatic consumption causes more frequent compressor "
                    "loading."
                ),
            ],
        ),
        CausalEdge(
            edge_id="assoc.air-leakage-to-operator-report",
            source_id="metropt3.air_leakage",
            target_id="metropt3.operator_reported_air_leak",
            relation="weakly_aligned_with_operator_report_interval",
            epistemic_type="correlational_indicator",
            evidence_status="weak_observational_support",
            provenance_refs=[
                "UCI #791 operator failure reports F1-F4",
                "backend/src/factorymind/data/metropt3.py:FAILURE_REPORTS",
            ],
            assumptions=[
                "The operator report interval approximately overlaps the underlying condition.",
                (
                    "Report timing is coarse; timezone and maintenance-date ambiguity remain "
                    "unresolved."
                ),
            ],
        ),
    ],
)

__all__ = ["METROPT3_AIR_LEAKAGE_GRAPH"]
