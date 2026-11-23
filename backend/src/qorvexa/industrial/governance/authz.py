"""Principals and roles. A Principal is created ONLY by the trusted authentication layer.

Never build one from request bodies. Human roles require a human principal; the extractor role
requires a non-human service principal, so an LLM service can never confirm or approve anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from qorvexa.industrial.formulation import ActorKind


class AuthorizationError(PermissionError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class Role(StrEnum):
    VIEWER = "VIEWER"
    PLANNER = "PLANNER"  # state/confirm/assume/retract/resolve facts, run decisions
    APPROVER = "APPROVER"  # approve/reject decision briefs
    EXTRACTOR = "EXTRACTOR"  # non-human service: may only submit untrusted proposals
    TERMINOLOGY_REVIEWER = "TERMINOLOGY_REVIEWER"  # human: confirm/reject/deprecate factory terms


@dataclass(frozen=True)
class Principal:
    id: str
    kind: ActorKind
    roles: frozenset[Role]
    # factories this principal may act for (set by the authentication layer); None = not scoped
    factories: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise AuthorizationError("INVALID_PRINCIPAL", "principal id is required")
        human_roles = {Role.PLANNER, Role.APPROVER, Role.TERMINOLOGY_REVIEWER}
        if self.kind is not ActorKind.HUMAN and self.roles & human_roles:
            raise AuthorizationError("INVALID_PRINCIPAL", "only humans may hold PLANNER/APPROVER")
        if self.kind is ActorKind.HUMAN and Role.EXTRACTOR in self.roles:
            raise AuthorizationError("INVALID_PRINCIPAL", "EXTRACTOR is a service role")

    def require(self, role: Role) -> None:
        if role not in self.roles:
            raise AuthorizationError("FORBIDDEN", f"{self.id} lacks role {role.value}")
