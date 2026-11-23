"""Isolated HiGHS continuous-LP worker process entry point.

Reads a serialized `WorkerRequest` from standard input, solves it using native HiGHS,
and writes the serialized `WorkerResponse` to standard output.
Exits with code 0 on both successful solves and structured solver failures.
Non-zero exit codes represent process crashes, unhandled aborts, or system faults.
"""

from __future__ import annotations

import os
import sys

from qorvexa.solver_fabric.highs_adapter import solve_with_highs
from qorvexa.solver_fabric.worker_contract import WorkerRequest


def main() -> None:
    """Worker process main routine."""
    try:
        raw_bytes = sys.stdin.buffer.read()
        if not raw_bytes:
            sys.stderr.write("Worker received empty input on stdin\n")
            sys.exit(1)

        try:
            request = WorkerRequest.from_json_bytes(raw_bytes)
        except Exception as exc:
            sys.stderr.write(f"Worker failed to parse WorkerRequest: {exc}\n")
            sys.exit(2)

        # Suppress any C-level stdout banners (e.g. from HiGHS constructor) during solve
        # so stdout only carries the clean JSON response payload.
        saved_stdout_fd = os.dup(1)
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull_fd, 1)
            response = solve_with_highs(request)
        finally:
            os.dup2(saved_stdout_fd, 1)
            os.close(saved_stdout_fd)
            os.close(devnull_fd)

        sys.stdout.buffer.write(response.to_json_bytes())
        sys.stdout.buffer.flush()
        sys.exit(0)

    except Exception as exc:
        sys.stderr.write(f"Worker unhandled top-level exception: {exc}\n")
        sys.exit(3)


if __name__ == "__main__":
    main()
