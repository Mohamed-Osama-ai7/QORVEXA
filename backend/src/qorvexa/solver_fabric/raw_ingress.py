"""Strict raw-JSON ingress for QORVEXA Solver Fabric (S3).

This scanner runs on the RAW JSON text *before* the permissive frozen M1 Pydantic models
see any data. It is the only place where the following guarantees can be made, because
the M1 models ignore unknown keys, accept camelCase and snake_case aliases interchangeably
and coerce values:

- size, nesting depth, node-count, array-length, object-width and string-length limits;
- duplicate object keys are rejected (also after JSON escape decoding);
- NaN / Infinity / -Infinity constants and numeric overflow are rejected;
  a nonzero literal that underflows to 0.0 is rejected;
- integers are bounded in digit count; integers that M1 would silently round because the
  M1 field is a float (|n| > 2**53) are rejected;
- unpaired UTF-16 surrogates and non-UTF-8 input are rejected;
- unknown fields are rejected at every object level of the M1 schema;
- a field supplied under both its snake_case name and its camelCase alias is rejected;
- discriminated expression nodes must carry a known `type`;
- the client flag `mathematicallyVerified` must be the JSON literal `false` when present. The
  frozen M1 model silently coerces every non-`true` value (strings, 0, 1, null) to False, which
  would hide a client verification claim, so the scanner checks the raw value itself.

Guarantee scope: these checks hold only for documents that enter through
`scan_problem_spec_json` / `parse_problem_spec_json`. A GeneralizedProblemSpec that was
constructed elsewhere (Python dict, other parser) has already passed through the permissive M1
models and carries NO such guarantee. `expressionSummary` is plain text and is never parsed or
executed.
"""

from __future__ import annotations

import json
import math
import re
import types
import typing
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo

from qorvexa.contracts.solver_fabric import GeneralizedProblemSpec

MAX_EXACT_FLOAT_INT: Final[int] = 2**53

# Error codes
INVALID_ENCODING: Final = "INVALID_ENCODING"
INVALID_JSON: Final = "INVALID_JSON"
INPUT_TOO_LARGE: Final = "INPUT_TOO_LARGE"
NESTING_TOO_DEEP: Final = "NESTING_TOO_DEEP"
TOO_MANY_NODES: Final = "TOO_MANY_NODES"
ARRAY_TOO_LONG: Final = "ARRAY_TOO_LONG"
OBJECT_TOO_WIDE: Final = "OBJECT_TOO_WIDE"
STRING_TOO_LONG: Final = "STRING_TOO_LONG"
DUPLICATE_KEY: Final = "DUPLICATE_KEY"
UNKNOWN_FIELD: Final = "UNKNOWN_FIELD"
ALIAS_COLLISION: Final = "ALIAS_COLLISION"
NON_FINITE_NUMBER: Final = "NON_FINITE_NUMBER"
NUMBER_OVERFLOW: Final = "NUMBER_OVERFLOW"
NUMBER_UNDERFLOW: Final = "NUMBER_UNDERFLOW"
INTEGER_TOO_LARGE: Final = "INTEGER_TOO_LARGE"
NUMBER_NOT_EXACT_IN_M1: Final = "NUMBER_NOT_EXACT_IN_M1"
UNPAIRED_SURROGATE: Final = "UNPAIRED_SURROGATE"
NON_OBJECT_ROOT: Final = "NON_OBJECT_ROOT"
TYPE_MISMATCH: Final = "TYPE_MISMATCH"
UNKNOWN_NODE_TYPE: Final = "UNKNOWN_NODE_TYPE"
M1_VALIDATION_FAILED: Final = "M1_VALIDATION_FAILED"
VERIFICATION_FLAG_NOT_TRUSTED: Final = "VERIFICATION_FLAG_NOT_TRUSTED"


class RawIngressError(ValueError):
    """Typed rejection of raw input. `code` is stable; `path` is a JSON path when known."""

    def __init__(self, code: str, path: str, message: str) -> None:
        super().__init__(f"{code} at '{path}': {message}")
        self.code = code
        self.path = path
        self.message = message


@dataclass(frozen=True)
class RawIngressLimits:
    """Deterministic ingress limits."""

    max_bytes: int = 1_048_576
    max_depth: int = 64
    max_nodes: int = 200_000
    max_array_len: int = 100_000
    max_object_keys: int = 1_000
    max_string_chars: int = 65_536
    max_int_digits: int = 1_300
    max_float_chars: int = 400

    def __post_init__(self) -> None:
        if not (1 <= self.max_depth <= 200):
            raise ValueError("max_depth must be within 1..200")


DEFAULT_INGRESS_LIMITS = RawIngressLimits()

_STRUCT_TOKEN = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"|[\[\]{}]', re.DOTALL)


# ---------------------------------------------------------------------------
# Stage 1: text, depth, parse with strict hooks
# ---------------------------------------------------------------------------


def _decode(raw: bytes | str, limits: RawIngressLimits) -> str:
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > limits.max_bytes:
            raise RawIngressError(
                INPUT_TOO_LARGE, "$", f"input has {len(raw)} bytes (limit {limits.max_bytes})."
            )
        try:
            text = bytes(raw).decode("utf-8")
        except UnicodeDecodeError as err:
            raise RawIngressError(INVALID_ENCODING, "$", "input is not valid UTF-8.") from err
    elif isinstance(raw, str):
        text = raw
        # Bound by encoded size without allocating for pathological inputs.
        if len(text) > limits.max_bytes:
            raise RawIngressError(
                INPUT_TOO_LARGE,
                "$",
                f"input has {len(text)} characters (limit {limits.max_bytes}).",
            )
        try:
            encoded_len = len(text.encode("utf-8"))
        except UnicodeEncodeError as err:
            raise RawIngressError(
                UNPAIRED_SURROGATE, "$", "input contains a lone surrogate."
            ) from err
        if encoded_len > limits.max_bytes:
            raise RawIngressError(
                INPUT_TOO_LARGE, "$", f"input has {encoded_len} bytes (limit {limits.max_bytes})."
            )
    else:
        raise RawIngressError(INVALID_JSON, "$", f"unsupported input type {type(raw).__name__}.")
    return text


def _check_depth(text: str, limits: RawIngressLimits) -> None:
    depth = 0
    for match in _STRUCT_TOKEN.finditer(text):
        tok = match.group()
        if tok in ("{", "["):
            depth += 1
            if depth > limits.max_depth:
                raise RawIngressError(
                    NESTING_TOO_DEEP, "$", f"nesting depth exceeds {limits.max_depth}."
                )
        elif tok in ("}", "]"):
            depth = max(0, depth - 1)


def _loads(text: str, limits: RawIngressLimits) -> Any:
    def on_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise RawIngressError(DUPLICATE_KEY, "$", f"duplicate object key {key[:60]!r}.")
            out[key] = value
        if len(out) > limits.max_object_keys:
            raise RawIngressError(
                OBJECT_TOO_WIDE, "$", f"object has more than {limits.max_object_keys} keys."
            )
        return out

    def on_constant(token: str) -> Any:
        raise RawIngressError(NON_FINITE_NUMBER, "$", f"non-finite constant {token} is rejected.")

    def on_float(token: str) -> float:
        if len(token) > limits.max_float_chars:
            raise RawIngressError(
                INVALID_JSON, "$", f"numeric token longer than {limits.max_float_chars} chars."
            )
        value = float(token)
        if math.isinf(value):
            raise RawIngressError(NUMBER_OVERFLOW, "$", f"numeric literal {token[:40]} overflows.")
        if value == 0.0 and Decimal(token) != 0:
            raise RawIngressError(
                NUMBER_UNDERFLOW, "$", f"nonzero literal {token[:40]} underflows to zero."
            )
        return value

    def on_int(token: str) -> int:
        digits = token.lstrip("-")
        if len(digits) > limits.max_int_digits:
            raise RawIngressError(
                INTEGER_TOO_LARGE, "$", f"integer has more than {limits.max_int_digits} digits."
            )
        return int(token)

    try:
        return json.loads(
            text,
            object_pairs_hook=on_pairs,
            parse_constant=on_constant,
            parse_float=on_float,
            parse_int=on_int,
        )
    except RawIngressError:
        raise
    except RecursionError as err:
        raise RawIngressError(NESTING_TOO_DEEP, "$", "nesting exhausted the parser.") from err
    except json.JSONDecodeError as err:
        raise RawIngressError(
            INVALID_JSON, "$", f"{err.msg} (line {err.lineno}, col {err.colno})."
        ) from err


def _has_surrogate(s: str) -> bool:
    return any("\ud800" <= ch <= "\udfff" for ch in s)


def _check_shape(root: Any, limits: RawIngressLimits) -> None:
    """Iterative walk: node count, array length, string length, surrogates."""
    nodes = 0
    stack: list[tuple[Any, str]] = [(root, "$")]
    while stack:
        value, path = stack.pop()
        nodes += 1
        if nodes > limits.max_nodes:
            raise RawIngressError(
                TOO_MANY_NODES, path, f"more than {limits.max_nodes} JSON values."
            )
        if isinstance(value, str):
            if len(value) > limits.max_string_chars:
                raise RawIngressError(STRING_TOO_LONG, path, "string value too long.")
            if _has_surrogate(value):
                raise RawIngressError(UNPAIRED_SURROGATE, path, "string contains a lone surrogate.")
        elif isinstance(value, list):
            if len(value) > limits.max_array_len:
                raise RawIngressError(
                    ARRAY_TOO_LONG, path, f"array longer than {limits.max_array_len}."
                )
            stack.extend((item, f"{path}[{i}]") for i, item in enumerate(value))
        elif isinstance(value, dict):
            for key, item in value.items():
                if len(key) > limits.max_string_chars:
                    raise RawIngressError(STRING_TOO_LONG, path, "object key too long.")
                if _has_surrogate(key):
                    raise RawIngressError(
                        UNPAIRED_SURROGATE, path, "key contains a lone surrogate."
                    )
                stack.append((item, f"{path}.{key}"))


# ---------------------------------------------------------------------------
# Stage 2: schema-directed key checks against the frozen M1 models
# ---------------------------------------------------------------------------

_Kind = tuple[Any, ...]


def _classify(annotation: Any, discriminated: bool = False) -> _Kind:
    """Reduce a type annotation to ('model', cls) | ('tagged', {tag: cls}) | ('list', kind)
    | ('dict',) | ('scalar', floatish, intish)."""
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin is typing.Annotated:
        disc = any(isinstance(m, FieldInfo) and m.discriminator for m in args[1:])
        return _classify(args[0], disc or discriminated)

    if origin in (typing.Union, types.UnionType):
        members = [a for a in args if a is not type(None)]
        if members and all(isinstance(m, type) and issubclass(m, BaseModel) for m in members):
            if len(members) == 1:
                return ("model", members[0])
            tags = {}
            for m in members:
                tag = m.model_fields["type"].default
                tags[tag] = m
            return ("tagged", tags)
        kinds = [_classify(m) for m in members]
        if len(kinds) == 1:
            return kinds[0]
        return (
            "scalar",
            any(k[0] == "scalar" and k[1] for k in kinds),
            any(k[0] == "scalar" and k[2] for k in kinds),
        )

    if origin in (list, tuple, set, frozenset):
        return ("list", _classify(args[0]) if args else ("scalar", False, False))
    if origin is dict or annotation is dict:
        return ("dict",)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return ("model", annotation)
    if annotation is float:
        return ("scalar", True, False)
    if annotation is int:
        return ("scalar", False, True)
    return ("scalar", False, False)


def _field_kind(info: FieldInfo) -> _Kind:
    return _classify(info.annotation)


def _walk_schema(value: Any, kind: _Kind, path: str) -> None:
    tag = kind[0]
    if tag == "scalar":
        _, floatish, intish = kind
        if (
            floatish
            and not intish
            and isinstance(value, int)
            and not isinstance(value, bool)
            and abs(value) > MAX_EXACT_FLOAT_INT
        ):
            raise RawIngressError(
                NUMBER_NOT_EXACT_IN_M1,
                path,
                "integer exceeds 2**53 but the M1 field is a binary64 float; it would be "
                "silently rounded.",
            )
        return
    if tag == "dict":
        return
    if tag == "list":
        if value is None:
            return
        if not isinstance(value, list):
            raise RawIngressError(TYPE_MISMATCH, path, "expected an array.")
        for i, item in enumerate(value):
            _walk_schema(item, kind[1], f"{path}[{i}]")
        return

    if value is None:
        return
    if not isinstance(value, dict):
        raise RawIngressError(TYPE_MISMATCH, path, "expected an object.")

    if tag == "tagged":
        node_type = value.get("type")
        if not isinstance(node_type, str) or node_type not in kind[1]:
            raise RawIngressError(
                UNKNOWN_NODE_TYPE, path, f"unknown or missing node type {str(node_type)[:40]!r}."
            )
        model: type[BaseModel] = kind[1][node_type]
    else:
        model = kind[1]

    _walk_model(value, model, path)


def _walk_model(obj: dict[str, Any], model: type[BaseModel], path: str) -> None:
    key_to_field: dict[str, str] = {}
    for name, info in model.model_fields.items():
        key_to_field[name] = name
        if info.alias:
            key_to_field[info.alias] = name

    seen_fields: dict[str, str] = {}
    for key in obj:
        field = key_to_field.get(key)
        if field is None:
            raise RawIngressError(
                UNKNOWN_FIELD, path, f"unknown field {key[:60]!r} for {model.__name__}."
            )
        if field in seen_fields:
            raise RawIngressError(
                ALIAS_COLLISION,
                path,
                f"field {field!r} supplied as both {seen_fields[field]!r} and {key!r}.",
            )
        seen_fields[field] = key

    for field, key in seen_fields.items():
        _walk_schema(obj[key], _field_kind(model.model_fields[field]), f"{path}.{key}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_problem_spec_json(
    raw: bytes | str, limits: RawIngressLimits | None = None
) -> dict[str, Any]:
    """Strictly scan raw JSON text and return the parsed document (a plain dict).

    Raises RawIngressError with a stable `code` on any violation.
    """
    lim = limits or DEFAULT_INGRESS_LIMITS
    text = _decode(raw, lim)
    _check_depth(text, lim)
    document = _loads(text, lim)
    if not isinstance(document, dict):
        raise RawIngressError(NON_OBJECT_ROOT, "$", "root JSON value must be an object.")
    _check_shape(document, lim)
    _walk_model(document, GeneralizedProblemSpec, "$")
    for key in ("mathematicallyVerified", "mathematically_verified"):
        if key in document and document[key] is not False:
            raise RawIngressError(
                VERIFICATION_FLAG_NOT_TRUSTED,
                f"$.{key}",
                "client verification flags are never trusted; the value must be JSON false.",
            )
    return document


def parse_problem_spec_json(
    raw: bytes | str, limits: RawIngressLimits | None = None
) -> GeneralizedProblemSpec:
    """Scan strictly, then validate with the frozen M1 model (strict mode, JSON parser).

    The M1 model only ever sees text that already passed the scanner.
    """
    lim = limits or DEFAULT_INGRESS_LIMITS
    scan_problem_spec_json(raw, lim)
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
    try:
        return GeneralizedProblemSpec.model_validate_json(text, strict=True)
    except ValidationError as err:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
            for e in err.errors(include_input=False, include_url=False, include_context=False)[:5]
        )
        raise RawIngressError(M1_VALIDATION_FAILED, "$", details) from err
