"""OPC UA connection configuration. Secrets are referenced by environment-variable *name* only."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

from qorvexa.connectors.edge import validate_edge_endpoint

SecurityMode = Literal["sign_and_encrypt", "sign"]
_ENV_NAME: Final = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


@dataclass(frozen=True)
class OpcUaConnectionConfig:
    """Never holds a password, key or certificate content; ``*_env`` fields name env variables.

    ``security_mode`` has no ``none`` option on purpose: unencrypted sessions are not supported.
    """

    endpoint_url: str
    allowed_hosts: Sequence[str]
    security_mode: SecurityMode = "sign_and_encrypt"
    username_env: str | None = None
    password_env: str | None = None
    client_certificate_path_env: str | None = None
    client_private_key_path_env: str | None = None
    server_certificate_path_env: str | None = None
    connect_timeout_s: float = 10.0
    request_timeout_s: float = 5.0
    session_timeout_s: float = 60.0
    _validated: tuple[str, int | None] = field(default=("", None), init=False, repr=False)

    def __post_init__(self) -> None:
        if not 0 < self.connect_timeout_s <= 60 or not 0 < self.request_timeout_s <= 60:
            raise ValueError("timeouts must be within (0, 60] seconds")
        if (self.username_env is None) != (self.password_env is None):
            raise ValueError("username_env and password_env must be set together")
        for name in (
            self.username_env,
            self.password_env,
            self.client_certificate_path_env,
            self.client_private_key_path_env,
            self.server_certificate_path_env,
        ):
            if name is not None and not (name and set(name) <= _ENV_NAME):
                raise ValueError("environment variable names must be UPPER_SNAKE_CASE")
        host_port = validate_edge_endpoint(
            self.endpoint_url, protocol="opcua_readonly", allowed_hosts=self.allowed_hosts
        )
        object.__setattr__(self, "_validated", (host_port[0], host_port[1]))

    def resolve_credentials(self, environ: dict[str, str] | None = None) -> tuple[str, str] | None:
        """Read the credentials from the environment at connect time (``None`` = anonymous)."""
        if self.username_env is None or self.password_env is None:
            return None
        env = os.environ if environ is None else environ
        user, password = env.get(self.username_env), env.get(self.password_env)
        if not user or not password:
            raise ValueError("credential environment variables are not set")
        return user, password
