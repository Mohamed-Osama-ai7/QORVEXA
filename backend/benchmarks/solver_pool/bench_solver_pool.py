"""Reproducible benchmark: sequential solver execution vs the bounded SolverPool.

Usage (from ``backend/``)::

    .venv/Scripts/python.exe benchmarks/solver_pool/bench_solver_pool.py --workload medium \
        --jobs 16 --reps 5 --workers 1,2,4,8 --out benchmarks/solver_pool/results/medium.json

Configurations
  inprocess   HighsAdapter(in_process=True), sequential. NO isolation; pure-solve reference only.
  spawn       HighsAdapter(), sequential: one fresh worker process per job (pre-pool baseline).
  pool-wN     SolverPool(max_workers=N): all jobs submitted at once to a warm pool.

Workloads are generated deterministically from fixed seeds (feasible, bounded sparse LPs). Every
response is checked against the in-process reference objective; mismatches are counted, never
hidden. Memory and CPU are read with Win32 calls (no third-party dependency); on other platforms
those fields are reported as null.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from qorvexa.solver_fabric.canonical_lp import LoweredLP
from qorvexa.solver_fabric.highs_adapter import HighsAdapter
from qorvexa.solver_fabric.pool import PoolConfig, SolverPool
from qorvexa.solver_fabric.worker_contract import (
    LoweredLPPayload,
    SolverOptions,
    WorkerRequest,
    WorkerStatus,
)

WORKLOADS = {"small": (200, 100, 8), "medium": (1200, 600, 15)}


def make_request(seed: int, n: int, m: int, nnz_row: int) -> WorkerRequest:
    """max c^T x (as min -c^T x) s.t. A x <= A x0 + 1, 0 <= x <= 10, A >= 0.

    Feasible (x0) and bounded (box) by construction."""
    rng = np.random.default_rng(seed)
    starts, cols, vals, rows = [0], [], [], []
    x0 = rng.integers(0, 5, size=n).astype(float)
    for _ in range(m):
        c = np.sort(rng.choice(n, size=nnz_row, replace=False))
        v = rng.integers(1, 10, size=nnz_row).astype(float)
        cols += c.tolist()
        vals += v.tolist()
        starts.append(len(cols))
        rows.append(float(np.dot(v, x0[c]) + 1.0))
    cost = (-rng.integers(1, 20, size=n)).astype(float)
    ir = LoweredLP(
        n, m, tuple(cost.tolist()), (0.0,) * n, (10.0,) * n, (-float("inf"),) * m, tuple(rows),
        tuple(starts), tuple(cols), tuple(vals), 0.0,
    )  # fmt: skip
    return WorkerRequest(
        request_id=f"bench-{seed}", model_sha256="0" * 64, ir_sha256=ir.ir_sha256,
        compiler_version="bench", problem=LoweredLPPayload.from_lowered(ir),
        options=SolverOptions(time_limit_seconds=300, threads=1),
    )  # fmt: skip


# ----------------------------------------------------------------------------- Win32 probes
class _PMC(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint32 if i < 2 else ctypes.c_size_t) for i, n in enumerate((
        "cb", "PageFaultCount", "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
        "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
        "PagefileUsage", "PeakPagefileUsage", "PrivateUsage"))]  # fmt: skip


def _win_handle(pid: int | None) -> Any:
    k32 = ctypes.WinDLL("kernel32")
    if pid is None:
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        return k32.GetCurrentProcess(), False
    k32.OpenProcess.restype = ctypes.c_void_p
    return k32.OpenProcess(0x1000 | 0x0010, False, pid), True


def peak_ws_mb(pid: int | None = None) -> float | None:
    if sys.platform != "win32":
        return None
    h, close = _win_handle(pid)
    if not h:
        return None
    try:
        m = _PMC()
        m.cb = ctypes.sizeof(m)
        if not ctypes.WinDLL("psapi").GetProcessMemoryInfo(
            ctypes.c_void_p(h), ctypes.byref(m), m.cb
        ):
            return None
        return round(m.PeakWorkingSetSize / 2**20, 1)
    finally:
        if close:
            ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(h))


def cpu_seconds(pid: int | None = None) -> float | None:
    if sys.platform != "win32":
        return None
    h, close = _win_handle(pid)
    if not h:
        return None
    try:
        ft = [ctypes.c_ulonglong() for _ in range(4)]
        if not ctypes.WinDLL("kernel32").GetProcessTimes(
            ctypes.c_void_p(h), *[ctypes.byref(f) for f in ft]
        ):
            return None
        return (ft[2].value + ft[3].value) / 1e7  # kernel + user, 100 ns units
    finally:
        if close:
            ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(h))


def pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    return s[max(0, min(len(s) - 1, round(p * (len(s) - 1))))]


# ----------------------------------------------------------------------------- runs
def check(resp: Any, ref: float | None) -> bool:
    return (
        resp.status is WorkerStatus.FINISHED
        and resp.objective_value is not None
        and (ref is None or abs(resp.objective_value - ref) <= 1e-9 * max(1.0, abs(ref)))
    )


def run_sequential(
    reqs: list[WorkerRequest], adapter: HighsAdapter, refs: list[float]
) -> dict[str, Any]:
    cpu0, lat, bad = cpu_seconds(), [], 0
    t0 = time.perf_counter()
    for r, ref in zip(reqs, refs, strict=True):
        s = time.perf_counter()
        bad += not check(adapter.solve(r), ref)
        lat.append(time.perf_counter() - s)
    wall = time.perf_counter() - t0
    cpu1 = cpu_seconds()
    return {
        "wall_s": wall, "throughput_jobs_s": len(reqs) / wall, "latency_p50_s": pct(lat, 0.5),
        "latency_p95_s": pct(lat, 0.95), "latency_max_s": max(lat), "queue_wait_p50_s": 0.0,
        "queue_wait_max_s": 0.0,
        "parent_cpu_s": (cpu1 - cpu0) if cpu0 is not None and cpu1 is not None else None,
        "worker_cpu_s": None, "worker_peak_ws_mb": None, "parent_peak_ws_mb": peak_ws_mb(),
        "incorrect": bad,
    }  # fmt: skip


def run_pool(reqs: list[WorkerRequest], workers: int, refs: list[float]) -> dict[str, Any]:
    t_cold = time.perf_counter()
    pool = SolverPool(PoolConfig(max_workers=workers, max_queue=len(reqs)))
    try:
        if not pool.wait_ready(120):
            raise RuntimeError("pool did not become ready")
        startup_wall = time.perf_counter() - t_cold
        startups = list(pool.stats().startup_s)
        pids = [w.child_pid for w in pool.worker_snapshots() if w.child_pid]
        cpu_w0 = sum(cpu_seconds(p) or 0.0 for p in pids)
        t0 = time.perf_counter()
        handles = [pool.submit(r) for r in reqs]
        resps = [h.result(600) for h in handles]
        wall = time.perf_counter() - t0
        infos = [h.info() for h in handles]
        cpu_w1 = sum(cpu_seconds(p) or 0.0 for p in pids)
        bad = sum(not check(r, ref) for r, ref in zip(resps, refs, strict=True))
        lat = [i.total_s for i in infos if i.total_s is not None]
        waits = [i.queue_wait_s for i in infos if i.queue_wait_s is not None]
        peaks = [peak_ws_mb(p) for p in pids]
        result = {
            "wall_s": wall, "throughput_jobs_s": len(reqs) / wall, "latency_p50_s": pct(lat, 0.5),
            "latency_p95_s": pct(lat, 0.95), "latency_max_s": max(lat),
            "queue_wait_p50_s": pct(waits, 0.5), "queue_wait_max_s": max(waits),
            "worker_startup_s_each": startups, "pool_ready_wall_s": startup_wall,
            "cold_wall_s": startup_wall + wall,
            "worker_cpu_s": (cpu_w1 - cpu_w0) if sys.platform == "win32" else None,
            "worker_peak_ws_mb": max((p for p in peaks if p is not None), default=None),
            "parent_peak_ws_mb": peak_ws_mb(), "incorrect": bad,
            "jobs_per_worker": {
                w: sum(1 for i in infos if i.worker_id == w)
                for w in sorted({i.worker_id for i in infos if i.worker_id})
            },
        }  # fmt: skip
    finally:
        rep = pool.shutdown(drain=False)
    result["orphans_after_shutdown"] = list(rep.unterminated_pids)
    return result


def recovery_probe() -> dict[str, Any]:
    """Kill a warm worker externally; time detection + replacement, and the next job's latency."""
    pool = SolverPool(PoolConfig(max_workers=2, max_queue=4))
    try:
        pool.wait_ready(120)
        victim = pool.worker_snapshots()[0]
        req = make_request(99, *WORKLOADS["small"])
        t0 = time.perf_counter()
        for pid in (victim.child_pid, victim.pid):
            if pid:
                os.kill(pid, 15)
        while pool.stats().counters.get("crashed_idle", 0) < 1 and time.perf_counter() - t0 < 30:
            time.sleep(0.005)
        detected = time.perf_counter() - t0
        while not pool.wait_ready(0.01) and time.perf_counter() - t0 < 60:
            pass
        replaced = time.perf_counter() - t0
        t1 = time.perf_counter()
        ok = pool.submit(req).result(60).status is WorkerStatus.FINISHED
        return {
            "detect_s": detected,
            "replacement_ready_s": replaced,
            "next_job_s": time.perf_counter() - t1,
            "next_job_ok": ok,
        }
    finally:
        pool.shutdown(drain=False)


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"reps": len(runs)}
    for k in (
        "wall_s",
        "throughput_jobs_s",
        "latency_p50_s",
        "latency_p95_s",
        "queue_wait_max_s",
        "cold_wall_s",
        "worker_cpu_s",
        "worker_peak_ws_mb",
        "parent_peak_ws_mb",
    ):
        xs = [r[k] for r in runs if r.get(k) is not None]
        if xs:
            out[k] = {"median": statistics.median(xs), "min": min(xs), "max": max(xs)}
    out["incorrect_total"] = sum(r["incorrect"] for r in runs)
    out["orphans_total"] = sum(len(r.get("orphans_after_shutdown", [])) for r in runs)
    if "worker_startup_s_each" in runs[0]:
        s = [x for r in runs for x in r["worker_startup_s_each"]]
        out["worker_startup_s"] = {"median": statistics.median(s), "min": min(s), "max": max(s)}
        out["jobs_per_worker_last_rep"] = runs[-1]["jobs_per_worker"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workload", choices=sorted(WORKLOADS), default="medium")
    ap.add_argument("--jobs", type=int, default=16)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--workers", default="1,2,4,8")
    ap.add_argument("--skip-spawn", action="store_true")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    n, m, k = WORKLOADS[args.workload]
    reqs = [make_request(1000 + i, n, m, k) for i in range(args.jobs)]
    ref_adapter = HighsAdapter(in_process=True)
    refs = []
    for r in reqs:
        resp = ref_adapter.solve(r)
        if resp.objective_value is None:
            raise RuntimeError(f"reference solve failed for {r.request_id}")
        refs.append(resp.objective_value)
    report: dict[str, Any] = {
        "environment": {
            "os": platform.platform(), "python": platform.python_version(),
            "cpu": platform.processor(), "logical_cpus": os.cpu_count(),
            "highs_threads_per_job": 1,
        },
        "workload": {"name": args.workload, "variables": n, "rows": m, "nnz_per_row": k,
                     "jobs": args.jobs, "seeds": f"1000..{1000 + args.jobs - 1}"},
        "method": "each configuration repeated --reps times; medians with min/max; warm pool "
                  "(startup reported separately and as cold_wall_s); objectives checked against an "
                  "in-process reference solve",
        "configs": {},
    }  # fmt: skip
    plan: list[tuple[str, Any]] = [
        ("inprocess", lambda: run_sequential(reqs, HighsAdapter(in_process=True), refs))
    ]
    if not args.skip_spawn:
        plan.append(("spawn", lambda: run_sequential(reqs, HighsAdapter(), refs)))
    for w in (int(x) for x in args.workers.split(",")):
        plan.append((f"pool-w{w}", lambda w=w: run_pool(reqs, w, refs)))
    for name, fn in plan:
        runs = []
        for rep in range(args.reps):
            runs.append(fn())
            print(f"{name} rep {rep + 1}/{args.reps}: wall {runs[-1]['wall_s']:.2f}s", flush=True)
        report["configs"][name] = summarize(runs)
    report["recovery"] = [recovery_probe() for _ in range(3)]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))
    print(
        json.dumps(
            {
                k: {
                    kk: vv
                    for kk, vv in v.items()
                    if kk in ("wall_s", "throughput_jobs_s", "incorrect_total")
                }
                for k, v in report["configs"].items()
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
