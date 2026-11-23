"""Persistent HiGHS worker process for ``SolverPool``.

Protocol (see ``pool_ipc``): announce HELLO, then loop: read a SOLVE frame, validate the payload as
a strict ``WorkerRequest``, solve with a FRESH ``highspy.Highs()`` (``solve_with_highs`` builds one
per call, so no solver state survives between jobs), answer with a RESULT frame carrying the same
sequence number. SHUTDOWN or EOF on stdin ends the process (EOF also covers a dead parent, so a
worker can never outlive its pool).

stdout is reserved for frames: the original stdout fd is duplicated to a private IPC descriptor
and fd 1 / fd 2 are pointed at the null device, so stray C-level output (HiGHS banners, library
warnings) can neither corrupt a frame nor fill a pipe nobody drains. Fatal conditions send one
short FATAL frame (<= 4000 bytes) and exit non-zero; the pool starts workers with stderr on the
null device.

The worker never executes anything it receives: the only inputs are validated JSON models.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from typing import BinaryIO

from qorvexa.solver_fabric.pool_ipc import (
    IPC_VERSION,
    FrameClosedError,
    FrameCorruptError,
    FrameKind,
    WorkerHello,
    read_frame,
    write_frame,
)
from qorvexa.solver_fabric.worker_contract import CONTRACT_VERSION, WorkerRequest

EXIT_FATAL = 3
EXIT_PROTOCOL = 4


def _fatal(out: BinaryIO, code: int, message: str) -> None:
    # the channel itself may be gone; the exit code still tells the pool
    with contextlib.suppress(Exception):
        write_frame(out, FrameKind.FATAL, 0, message.encode("utf-8", errors="replace")[:4000])
    os._exit(code)  # no cleanup that could block


def _apply_memory_limit(limit: int) -> None:
    """POSIX only: bound the address space. Windows uses a Job Object assigned by the pool."""
    if limit <= 0 or sys.platform == "win32":
        return
    import resource  # POSIX-only module

    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--memory-limit-bytes", type=int, default=0)
    args = ap.parse_args(argv)

    in_stream: BinaryIO = sys.stdin.buffer
    ipc_fd = os.dup(1)
    null_fd = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null_fd, 1)
    os.dup2(null_fd, 2)
    os.close(null_fd)
    out_stream = os.fdopen(ipc_fd, "wb")  # buffered: loops until every byte is written
    try:
        _apply_memory_limit(args.memory_limit_bytes)
        import highspy

        from qorvexa.solver_fabric.highs_adapter import solve_with_highs

        probe = highspy.Highs()  # type: ignore[no-untyped-call]
        hello = WorkerHello(
            ipc_version=IPC_VERSION, contract_version=CONTRACT_VERSION, pid=os.getpid(),
            solver_name="HiGHS", solver_version=str(probe.version()),
        )  # fmt: skip
        del probe
        write_frame(out_stream, FrameKind.HELLO, 0, hello.model_dump_json().encode())
    except Exception as exc:
        _fatal(out_stream, EXIT_FATAL, f"worker startup failed: {type(exc).__name__}: {exc}")
        return

    while True:
        try:
            kind, seq, payload = read_frame(in_stream)
        except FrameClosedError:
            os._exit(0)  # parent closed the pipe or died
        except FrameCorruptError as exc:
            _fatal(out_stream, EXIT_PROTOCOL, f"corrupt frame from pool: {exc}")
            return
        if kind is FrameKind.SHUTDOWN:
            write_frame(out_stream, FrameKind.BYE, 0)
            os._exit(0)
        if kind is not FrameKind.SOLVE:
            _fatal(out_stream, EXIT_PROTOCOL, f"unexpected frame kind {kind.name}")
            return
        try:
            request = WorkerRequest.from_json_bytes(payload)  # strict contract validation
        except Exception as exc:
            _fatal(out_stream, EXIT_PROTOCOL, f"invalid WorkerRequest: {exc}")
            return
        try:
            response = solve_with_highs(request)
            write_frame(out_stream, FrameKind.RESULT, seq, response.to_json_bytes())
        except Exception as exc:
            _fatal(
                out_stream, EXIT_FATAL, f"worker unhandled exception: {type(exc).__name__}: {exc}"
            )
            return


if __name__ == "__main__":
    main()
