"""Multi-model ML engine, Mahalanobis OOD detection, probability calibration, and abstention."""

from qorvexa.ml.ai4i_benchmark import run_ai4i_benchmark, train_and_evaluate_ai4i
from qorvexa.ml.orchestrator import (
    BenchmarkUnavailableError,
    MultiModelOrchestrator,
    load_ai4i_benchmark_report,
    load_verified_ai4i_bundle,
)
from qorvexa.ml.uncertainty import (
    MahalanobisOodDetector,
    compute_calibration_metrics,
    expected_calibration_error,
)

__all__ = [
    "BenchmarkUnavailableError",
    "MahalanobisOodDetector",
    "MultiModelOrchestrator",
    "compute_calibration_metrics",
    "expected_calibration_error",
    "load_ai4i_benchmark_report",
    "load_verified_ai4i_bundle",
    "run_ai4i_benchmark",
    "train_and_evaluate_ai4i",
]
