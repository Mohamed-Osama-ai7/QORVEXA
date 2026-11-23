"""Framed IPC between the solver pool and its worker processes.

Wire format (all integers big-endian)::

    magic  4 bytes  b"QSP1"
    kind   1 byte   FrameKind
    seq    8 bytes  execution sequence (0 for HELLO / SHUTDOWN / BYE)
    length 4 bytes  payload length, at most MAX_FRAME_BYTES
    body   length bytes

Payloads are JSON validated against explicit pydantic contracts (``WorkerRequest`` /
``WorkerResponse`` / ``WorkerHello``): there is no pickle, no eval and no executable payload. A
frame with a wrong magic, kind, sequence or an oversized length is an IPC failure; the pool
treats the worker as failed and replaces it. A response is only ever accepted when its sequence
matches the single outstanding request of that worker.
"""

from __future__ import annotations

import struct
from enum import IntEnum
from typing import IO, Final

from pydantic import BaseModel, ConfigDict, Field

IPC_VERSION: Final = 1
MAGIC: Final = b"QSP1"
HEADER: Final = struct.Struct(">4sBQI")
MAX_FRAME_BYTES: Final = 50 * 1024 * 1024


class FrameKind(IntEnum):
    HELLO = 1  # worker -> pool, once, after startup
    SOLVE = 2  # pool -> worker, payload = WorkerRequest JSON
    RESULT = 3  # worker -> pool, payload = WorkerResponse JSON
    SHUTDOWN = 4  # pool -> worker: finish and exit
    BYE = 5  # worker -> pool: acknowledges SHUTDOWN
    FATAL = 6  # worker -> pool: one short diagnostic (<= 4000 bytes) before a non-zero exit


class IpcError(RuntimeError):
    """Base class: the byte stream does not follow the framing contract."""


class FrameClosedError(IpcError):
    """EOF before a complete frame: the peer exited or closed its pipe."""


class FrameCorruptError(IpcError):
    """Bad magic, unknown kind or a length above the cap."""


class WorkerHello(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ipc_version: int
    contract_version: int
    pid: int = Field(ge=1)
    solver_name: str = Field(max_length=64)
    solver_version: str = Field(max_length=64)


def write_frame(out: IO[bytes], kind: FrameKind, seq: int, payload: bytes = b"") -> None:
    if len(payload) > MAX_FRAME_BYTES:
        raise FrameCorruptError(
            f"frame of {len(payload)} bytes exceeds the {MAX_FRAME_BYTES} byte cap"
        )
    out.write(HEADER.pack(MAGIC, int(kind), seq, len(payload)))
    if payload:
        out.write(payload)
    out.flush()


def _read_exact(src: IO[bytes], n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = src.read(n - len(buf))
        if not chunk:
            raise FrameClosedError(f"stream ended after {len(buf)} of {n} bytes")
        buf += chunk
    return bytes(buf)


def read_frame(src: IO[bytes]) -> tuple[FrameKind, int, bytes]:
    """Read one frame or raise ``FrameClosedError`` / ``FrameCorruptError``. The length is validated
    BEFORE any payload is read, so a hostile length cannot force a large allocation."""
    magic, kind, seq, length = HEADER.unpack(_read_exact(src, HEADER.size))
    if magic != MAGIC:
        raise FrameCorruptError("bad frame magic")
    try:
        k = FrameKind(kind)
    except ValueError as exc:
        raise FrameCorruptError(f"unknown frame kind {kind}") from exc
    if length > MAX_FRAME_BYTES:
        raise FrameCorruptError(f"declared frame length {length} exceeds the cap")
    return k, seq, _read_exact(src, length) if length else b""
