"""Fault-injecting worker for SolverPool tests (test support only; never used in production).

Speaks the real framed protocol of ``qorvexa.solver_fabric.pool_ipc`` and misbehaves on purpose.
Usage: ``python sf_pool_fault_worker.py MODE[:ARG]``. ``real`` runs the production worker.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

MODE, _, ARG = (sys.argv[1] if len(sys.argv) > 1 else "real").partition(":")

if MODE == "real":
    from qorvexa.solver_fabric.pool_worker import main as real_main

    real_main([])
    raise SystemExit(0)
if MODE == "exit_at_start":
    os._exit(5)
if MODE == "no_hello":
    time.sleep(3600)

from qorvexa.solver_fabric.pool_ipc import (  # noqa: E402
    IPC_VERSION,
    FrameClosedError,
    FrameKind,
    WorkerHello,
    read_frame,
    write_frame,
)
from qorvexa.solver_fabric.worker_contract import CONTRACT_VERSION, WorkerRequest  # noqa: E402

inp = sys.stdin.buffer
ipc_fd = os.dup(1)
null = os.open(os.devnull, os.O_WRONLY)
os.dup2(null, 1)
os.dup2(null, 2)
out = os.fdopen(ipc_fd, "wb")

hello = WorkerHello(
    ipc_version=IPC_VERSION + (1 if MODE == "bad_hello" else 0),
    contract_version=CONTRACT_VERSION, pid=os.getpid(), solver_name="HiGHS", solver_version="fault",
)  # fmt: skip
write_frame(out, FrameKind.HELLO, 0, hello.model_dump_json().encode())


def real_solve(payload: bytes) -> bytes:
    from qorvexa.solver_fabric.highs_adapter import solve_with_highs

    return solve_with_highs(WorkerRequest.from_json_bytes(payload)).to_json_bytes()


done = 0
while True:
    try:
        kind, seq, payload = read_frame(inp)
    except FrameClosedError:
        os._exit(0)
    if kind is FrameKind.SHUTDOWN:
        write_frame(out, FrameKind.BYE, 0)
        os._exit(0)
    req = WorkerRequest.from_json_bytes(payload)
    if MODE == "crash_on_solve":
        os._exit(7)
    if MODE == "crash_after" and done >= int(ARG):
        os._exit(9)
    if MODE == "crash_on_id" and ARG in req.request_id:
        os._exit(11)
    if MODE == "crash_once":  # crash on the first attempt only (marker file in ARG)
        marker = Path(ARG)
        if not marker.exists():
            marker.write_text("crashed")
            os._exit(13)
    if MODE == "hang_on_solve":
        time.sleep(3600)
    if MODE == "slow":
        time.sleep(float(ARG))
    if MODE == "memhog":  # allocate ARG MiB and touch it; the OS limit must refuse it
        try:
            block = bytearray(int(ARG) * 1024 * 1024)
            block[::4096] = b"\x01" * len(block[::4096])
        except MemoryError:
            write_frame(out, FrameKind.FATAL, 0, b"MemoryError: allocation refused (memory limit)")
            os._exit(3)
        del block
    if MODE == "garbage":
        out.write(b"this is not a frame at all" * 10)
        out.flush()
        time.sleep(3600)
    if MODE == "oversize":
        out.write(b"QSP1" + bytes([3]) + seq.to_bytes(8, "big") + (2**31).to_bytes(4, "big"))
        out.flush()
        time.sleep(3600)
    if MODE == "fatal":
        write_frame(out, FrameKind.FATAL, 0, b"boom: injected fatal error")
        os._exit(3)
    if MODE == "bad_json":
        write_frame(out, FrameKind.RESULT, seq, b"{not json")
        continue
    body = real_solve(payload)
    if MODE == "wrong_seq":
        write_frame(out, FrameKind.RESULT, seq + 1, body)
        continue
    if MODE == "wrong_binding":
        body = body.replace(req.request_id.encode(), b"someone-else", 1)
    if MODE == "lie":  # fabricate an "optimal" answer with a wrong solution
        import json

        doc = json.loads(body)
        doc["primalSolution"] = [0.0] * len(doc.get("primalSolution") or [])
        doc["objectiveValue"] = -12345.0
        body = json.dumps(doc).encode()
    write_frame(out, FrameKind.RESULT, seq, body)
    done += 1
