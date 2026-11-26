"""OPC UA StatusCode helpers (severity = top two bits; the next 14 bits identify the code).

Only a handful of common codes are named; everything else is classified by severity alone.
Verify the named values against the OPC UA reference before relying on them for a real server.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

GOOD: Final = 0x00000000
BAD_UNEXPECTED_ERROR: Final = 0x80010000
BAD_COMMUNICATION_ERROR: Final = 0x80050000
BAD_TIMEOUT: Final = 0x800A0000
BAD_USER_ACCESS_DENIED: Final = 0x801F0000
BAD_NODE_ID_INVALID: Final = 0x80330000
BAD_NODE_ID_UNKNOWN: Final = 0x80340000
BAD_NOT_READABLE: Final = 0x803A0000
BAD_NO_DATA: Final = 0x809B0000
UNCERTAIN_LAST_USABLE_VALUE: Final = 0x40900000


class NodeErrorCategory(StrEnum):
    NODE_UNKNOWN = "node_unknown"
    ACCESS_DENIED = "access_denied"
    NO_DATA = "no_data"
    COMMUNICATION = "communication"
    OTHER_BAD = "other_bad"


_CATEGORY: Final[dict[int, NodeErrorCategory]] = {
    BAD_NODE_ID_UNKNOWN: NodeErrorCategory.NODE_UNKNOWN,
    BAD_NODE_ID_INVALID: NodeErrorCategory.NODE_UNKNOWN,
    BAD_USER_ACCESS_DENIED: NodeErrorCategory.ACCESS_DENIED,
    BAD_NOT_READABLE: NodeErrorCategory.ACCESS_DENIED,
    BAD_NO_DATA: NodeErrorCategory.NO_DATA,
    BAD_COMMUNICATION_ERROR: NodeErrorCategory.COMMUNICATION,
    BAD_TIMEOUT: NodeErrorCategory.COMMUNICATION,
}


def severity(code: int) -> int:
    """0 good, 1 uncertain, 2 bad, 3 reserved/invalid."""
    return (code >> 30) & 0b11


def is_good(code: int) -> bool:
    return severity(code) == 0


def error_category(code: int) -> NodeErrorCategory | None:
    """Category for a BAD code, else ``None`` (good/uncertain are not node errors)."""
    if severity(code) < 2:
        return None
    return _CATEGORY.get(code & 0xFFFF0000, NodeErrorCategory.OTHER_BAD)
