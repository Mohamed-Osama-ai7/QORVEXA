"""Model validation and compilation into exact integer ticks.

Units are handled ONLY by the Industrial Units Engine: every duration is converted explicitly to
the simulation resolution (seconds) and must land exactly on it; nothing is rounded silently. Any
missing, unknown or incompatible input yields a blocking issue and no compiled model.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Final

from pydantic import BaseModel, ConfigDict

from qorvexa.industrial.digital_twin.contracts import (
    MAX_MACHINES,
    MAX_OPERATIONS,
    MAX_ORDERS,
    MODEL_SCHEMA,
    RESOLUTION_UNIT,
    Calendar,
    Dist,
    DistKind,
    FactoryModel,
    Policies,
    Qty,
    SetupPolicy,
)
from qorvexa.industrial.units import Quantity, UnitError, exact_number, parse_unit

F = ConfigDict(frozen=True, extra="forbid")
DAY: Final = 86_400
MAX_HORIZON: Final = 400 * DAY
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")
_HHMM = re.compile(r"^(\d{2}):(\d{2})$")


class Issue(BaseModel):
    model_config = F
    code: str
    path: str
    message: str
    blocking: bool = True


class Conversion(BaseModel):
    model_config = F
    path: str
    value: str
    from_unit: str
    to_unit: str
    factor: str
    result: str


class UnitEvidence(BaseModel):
    model_config = F
    resolution_unit: str = RESOLUTION_UNIT
    conversions: tuple[Conversion, ...]
    sha256: str


class ValidationReport(BaseModel):
    model_config = F
    model_sha256: str
    ok: bool
    issues: tuple[Issue, ...]
    unit_evidence: UnitEvidence | None = None

    @property
    def blocking(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.blocking)


# ----------------------------------------------------------------------------- compiled model
@dataclass(frozen=True)
class CDist:
    kind: DistKind
    ticks: int | None = None  # DETERMINISTIC
    low: Fraction | None = None  # seconds, stochastic kinds
    mode: Fraction | None = None
    high: Fraction | None = None
    mean: Fraction | None = None


@dataclass(frozen=True)
class COp:
    op_id: str
    in_buf: str
    machines: tuple[str, ...]
    proc: CDist
    setup: CDist | None
    setup_policy: SetupPolicy
    materials: tuple[tuple[str, Fraction], ...]
    pools: tuple[tuple[str, int], ...]
    scrap: Fraction


@dataclass(frozen=True)
class CMachine:
    machine_id: str
    line_id: str | None
    calendar_id: str
    planned: tuple[tuple[int, int], ...]
    failure: tuple[CDist, CDist] | None


@dataclass(frozen=True)
class CMaterial:
    material_id: str
    unit: str
    initial: Fraction
    deliveries: tuple[tuple[int, Fraction], ...]


@dataclass(frozen=True)
class COrder:
    order_id: str
    product_id: str
    quantity: int
    release: int
    due: int | None
    priority: int


@dataclass(frozen=True)
class CompiledModel:
    model_sha256: str
    model_id: str
    factory_id: str
    horizon: int
    machines: dict[str, CMachine]
    buffers: dict[str, int | None]  # None = explicitly unbounded
    pools: dict[str, int]
    materials: dict[str, CMaterial]
    routes: dict[str, tuple[COp, ...]]  # product id -> operations
    batch: dict[str, int]
    orders: tuple[COrder, ...]
    breakdowns: tuple[tuple[str, int, int], ...]
    calendars: dict[str, tuple[tuple[int, int], ...]]
    policies: Policies
    stochastic: bool
    unit_evidence: UnitEvidence
    assumptions: tuple[str, ...] = field(default=())


def model_sha256(model: FactoryModel) -> str:
    return hashlib.sha256(
        json.dumps(model.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()


class _Ctx:
    def __init__(self) -> None:
        self.issues: list[Issue] = []
        self.conv: list[Conversion] = []

    def bad(self, code: str, path: str, msg: str, *, blocking: bool = True) -> None:
        self.issues.append(Issue(code=code, path=path, message=msg, blocking=blocking))

    def exact(self, text: str, path: str) -> Fraction | None:
        try:
            return exact_number(text)
        except (UnitError, ValueError) as exc:
            self.bad("INVALID_NUMBER", path, f"{text!r}: {exc}")
            return None

    def seconds(self, value: str, unit: str, path: str) -> Fraction | None:
        """Exact seconds from (value, time unit) via the Units Engine; never rounded."""
        try:
            u = parse_unit(unit)
            if u.dimension != parse_unit(RESOLUTION_UNIT).dimension:
                self.bad("UNIT_MISMATCH", path, f"{unit!r} is not a time unit")
                return None
            res = Quantity.of(value, u).to(RESOLUTION_UNIT)
        except UnitError as exc:
            self.bad("UNIT_REJECTED", path, f"{value} {unit}: {exc}")
            return None
        v = res.result.value
        if v < 0:
            self.bad("NEGATIVE_DURATION", path, f"{value} {unit} is negative")
            return None
        self.conv.append(
            Conversion(
                path=path, value=value, from_unit=unit, to_unit=RESOLUTION_UNIT,
                factor=f"{res.factor.numerator}/{res.factor.denominator}",
                result=f"{v.numerator}/{v.denominator}",
            )
        )  # fmt: skip
        return v

    def ticks(self, q: Qty, path: str) -> int | None:
        s = self.seconds(q.value, q.unit, path)
        if s is None:
            return None
        if s.denominator != 1:
            self.bad(
                "DURATION_NOT_ON_RESOLUTION",
                path,
                f"{q.value} {q.unit} = {s} s is not a whole number of ticks; no silent rounding",
            )
            return None
        return int(s)

    def dist(self, d: Dist, path: str) -> CDist | None:
        if d.kind is DistKind.DETERMINISTIC:
            if d.value is None or any(x is not None for x in (d.low, d.mode, d.high, d.mean)):
                self.bad("INVALID_DISTRIBUTION", path, "DETERMINISTIC needs exactly 'value'")
                return None
            t = self.ticks(Qty(value=d.value, unit=d.unit), f"{path}.value")
            return None if t is None else CDist(kind=d.kind, ticks=t)
        if d.kind is DistKind.TRIANGULAR:
            if None in (d.low, d.mode, d.high) or d.value is not None or d.mean is not None:
                self.bad("INVALID_DISTRIBUTION", path, "TRIANGULAR needs exactly low/mode/high")
                return None
            lo = self.seconds(str(d.low), d.unit, f"{path}.low")
            mo = self.seconds(str(d.mode), d.unit, f"{path}.mode")
            hi = self.seconds(str(d.high), d.unit, f"{path}.high")
            if lo is None or mo is None or hi is None:
                return None
            if not (lo <= mo <= hi) or hi <= 0:
                self.bad("INVALID_DISTRIBUTION", path, "need 0 <= low <= mode <= high, high > 0")
                return None
            return CDist(kind=d.kind, low=lo, mode=mo, high=hi)
        if d.mean is None or any(x is not None for x in (d.value, d.low, d.mode, d.high)):
            self.bad("INVALID_DISTRIBUTION", path, "EXPONENTIAL needs exactly 'mean'")
            return None
        m = self.seconds(d.mean, d.unit, f"{path}.mean")
        if m is None:
            return None
        if m <= 0:
            self.bad("INVALID_DISTRIBUTION", path, "EXPONENTIAL mean must be > 0")
            return None
        return CDist(kind=d.kind, mean=m)


def _hhmm(text: str, ctx: _Ctx, path: str) -> int | None:
    m = _HHMM.match(text)
    if not m:
        ctx.bad("INVALID_TIME_OF_DAY", path, f"{text!r} is not HH:MM")
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if mi > 59 or h > 24 or (h == 24 and mi != 0):
        ctx.bad("INVALID_TIME_OF_DAY", path, f"{text!r} is out of range")
        return None
    return h * 3600 + mi * 60


def calendar_windows(cal: Calendar, horizon: int, ctx: _Ctx) -> tuple[tuple[int, int], ...]:
    path = f"calendars[{cal.calendar_id}]"
    if cal.continuous:
        if cal.shifts:
            ctx.bad("INVALID_CALENDAR", path, "a continuous calendar cannot also list shifts")
        return ((0, horizon),)
    if not cal.shifts:
        ctx.bad("INVALID_CALENDAR", path, "no shifts and not continuous: availability unknown")
        return ()
    day: list[tuple[int, int]] = []
    for s in cal.shifts:
        a, b = (
            _hhmm(s.start, ctx, f"{path}.{s.name}.start"),
            _hhmm(s.end, ctx, f"{path}.{s.name}.end"),
        )
        if a is None or b is None:
            continue
        if b <= a:
            ctx.bad(
                "INVALID_CALENDAR",
                path,
                f"shift {s.name}: end must be after start (split overnight shifts)",
            )
            continue
        pieces = [(a, b)]
        for bs, be in s.breaks:
            x, y = (
                _hhmm(bs, ctx, f"{path}.{s.name}.break"),
                _hhmm(be, ctx, f"{path}.{s.name}.break"),
            )
            if x is None or y is None:
                continue
            if not (a <= x < y <= b):
                ctx.bad("INVALID_CALENDAR", path, f"break {bs}-{be} is not inside shift {s.name}")
                continue
            nxt: list[tuple[int, int]] = []
            for p, q in pieces:
                if y <= p or x >= q:
                    nxt.append((p, q))
                    continue
                if p < x:
                    nxt.append((p, x))
                if y < q:
                    nxt.append((y, q))
            pieces = nxt
        day += pieces
    day.sort()
    for (_, e1), (s2, _) in itertools.pairwise(day):
        if s2 < e1:
            ctx.bad("INVALID_CALENDAR", path, "shifts overlap")
    out: list[tuple[int, int]] = []
    k = 0
    while k * DAY < horizon:
        for start_sec, end_sec in day:
            a, b = k * DAY + start_sec, min(k * DAY + end_sec, horizon)
            if a >= horizon:
                break
            if out and out[-1][1] == a:
                out[-1] = (out[-1][0], b)
            else:
                out.append((a, b))
        k += 1
    return tuple(out)


def compile_model(model: FactoryModel) -> tuple[CompiledModel | None, ValidationReport]:
    ctx = _Ctx()
    sha = model_sha256(model)
    if model.schema_version != MODEL_SCHEMA:
        ctx.bad("SCHEMA_VERSION", "schema_version", f"expected {MODEL_SCHEMA}")
    n_ops = sum(len(r.operations) for r in model.routings)
    if (
        len(model.machines) > MAX_MACHINES
        or n_ops > MAX_OPERATIONS
        or len(model.orders) > MAX_ORDERS
    ):
        ctx.bad("MODEL_TOO_LARGE", "model", "model exceeds machine/operation/order limits")
        return None, _report(sha, ctx)

    def ids(kind: str, values: list[str]) -> set[str]:
        seen: set[str] = set()
        for v in values:
            if not _ID.match(v):
                ctx.bad("INVALID_ID", kind, f"{v!r} is not a valid identifier")
            if v in seen:
                ctx.bad("DUPLICATE_ID", kind, f"{v} is defined twice")
            seen.add(v)
        return seen

    m_ids = ids("machines", [m.machine_id for m in model.machines])
    b_ids = ids("buffers", [b.buffer_id for b in model.buffers])
    c_ids = ids("calendars", [c.calendar_id for c in model.calendars])
    p_ids = ids("pools", [p.pool_id for p in model.pools])
    ids("materials", [m.material_id for m in model.materials])
    r_ids = ids("routings", [r.routing_id for r in model.routings])
    prod_ids = ids("products", [p.product_id for p in model.products])
    ids("orders", [o.order_id for o in model.orders])
    ids("operations", [op.op_id for r in model.routings for op in r.operations])

    horizon = ctx.ticks(model.horizon, "horizon")
    if horizon is not None and not (0 < horizon <= MAX_HORIZON):
        ctx.bad("INVALID_HORIZON", "horizon", "horizon must be > 0 and at most 400 days")
        horizon = None
    h = horizon or 1

    calendars = {c.calendar_id: calendar_windows(c, h, ctx) for c in model.calendars}
    buffers: dict[str, int | None] = {}
    for b in model.buffers:
        if b.capacity is None:
            ctx.bad("MISSING_BUFFER_CAPACITY", f"buffers[{b.buffer_id}]", "capacity is unknown")
        elif b.capacity == "UNBOUNDED":
            buffers[b.buffer_id] = None
        elif isinstance(b.capacity, int) and b.capacity >= 1:
            buffers[b.buffer_id] = b.capacity
        else:
            ctx.bad("INVALID_BUFFER_CAPACITY", f"buffers[{b.buffer_id}]", "capacity must be >= 1")
    pools: dict[str, int] = {}
    for p in model.pools:
        if p.size < 1:
            ctx.bad("INVALID_POOL", f"pools[{p.pool_id}]", "size must be >= 1")
        pools[p.pool_id] = p.size

    used_materials = {
        u.material_id for r in model.routings for op in r.operations for u in op.materials
    }
    materials: dict[str, CMaterial] = {}
    for mat in model.materials:
        path = f"materials[{mat.material_id}]"
        try:
            parse_unit(mat.unit)
        except UnitError as exc:
            ctx.bad("UNIT_REJECTED", path, str(exc))
            continue
        if mat.initial_stock is None:
            if mat.material_id in used_materials:
                ctx.bad("MISSING_MATERIAL_STOCK", path, "initial stock is unknown")
            continue
        init = _same_unit_qty(ctx, mat.initial_stock, mat.unit, f"{path}.initial_stock")
        dels: list[tuple[int, Fraction]] = []
        for i, (at, q) in enumerate(mat.deliveries):
            t = ctx.ticks(at, f"{path}.deliveries[{i}].at")
            v = _same_unit_qty(ctx, q, mat.unit, f"{path}.deliveries[{i}].quantity")
            if t is not None and v is not None:
                dels.append((t, v))
        if init is not None:
            materials[mat.material_id] = CMaterial(
                mat.material_id, mat.unit, init, tuple(sorted(dels))
            )

    machines: dict[str, CMachine] = {}
    stochastic = False
    for m in model.machines:
        path = f"machines[{m.machine_id}]"
        if m.calendar_id is None:
            ctx.bad("MISSING_CALENDAR", path, "machine availability (calendar) is unknown")
            continue
        if m.calendar_id not in c_ids:
            ctx.bad("UNKNOWN_CALENDAR", path, m.calendar_id)
            continue
        planned: list[tuple[int, int]] = []
        for i, iv in enumerate(m.planned_downtime):
            t_start, t_end = (
                ctx.ticks(iv.start, f"{path}.planned[{i}].start"),
                ctx.ticks(iv.end, f"{path}.planned[{i}].end"),
            )
            if t_start is not None and t_end is not None:
                if t_end <= t_start:
                    ctx.bad("INVALID_INTERVAL", f"{path}.planned[{i}]", "end must be after start")
                else:
                    planned.append((t_start, t_end))
        planned.sort()
        for (_, e1), (s2, _) in itertools.pairwise(planned):
            if s2 < e1:
                ctx.bad("INVALID_INTERVAL", path, "planned downtime intervals overlap")
        fail = None
        if m.failures is not None:
            ttf = ctx.dist(m.failures.time_to_failure, f"{path}.failures.time_to_failure")
            rep = ctx.dist(m.failures.repair, f"{path}.failures.repair")
            if ttf is not None and rep is not None:
                if (ttf.ticks == 0) or (rep.ticks == 0):
                    ctx.bad("INVALID_DISTRIBUTION", f"{path}.failures", "failure times must be > 0")
                fail = (ttf, rep)
                stochastic = True
        machines[m.machine_id] = CMachine(
            m.machine_id, m.line_id, m.calendar_id, tuple(planned), fail
        )

    route_of = {r.routing_id: r for r in model.routings}
    compiled_ops: dict[str, tuple[COp, ...]] = {}
    for r in model.routings:
        if not r.operations:
            ctx.bad("EMPTY_ROUTING", f"routings[{r.routing_id}]", "a routing needs operations")
            continue
        ops: list[COp] = []
        for op in r.operations:
            path = f"routings[{r.routing_id}].{op.op_id}"
            if op.input_buffer not in b_ids:
                ctx.bad("UNKNOWN_BUFFER", path, op.input_buffer)
            if not op.machines:
                ctx.bad("NO_ELIGIBLE_MACHINE", path, "operation has no eligible machine")
            for mid in op.machines:
                if mid not in m_ids:
                    ctx.bad("UNKNOWN_MACHINE", path, mid)
            if op.processing is None:
                ctx.bad("MISSING_PROCESSING_TIME", path, "processing time is unknown")
                proc = None
            else:
                proc = ctx.dist(op.processing, f"{path}.processing")
                if proc is not None and proc.kind is DistKind.DETERMINISTIC and proc.ticks == 0:
                    ctx.bad("INVALID_DISTRIBUTION", path, "processing time must be > 0")
                if proc is not None and proc.kind is not DistKind.DETERMINISTIC:
                    stochastic = True
            setup = None
            if op.setup_policy is not SetupPolicy.NONE:
                if op.setup is None:
                    ctx.bad(
                        "MISSING_SETUP_TIME",
                        path,
                        f"setup policy {op.setup_policy} needs a setup time",
                    )
                else:
                    setup = ctx.dist(op.setup, f"{path}.setup")
                    if setup is not None and setup.kind is not DistKind.DETERMINISTIC:
                        stochastic = True
            elif op.setup is not None:
                ctx.bad(
                    "SETUP_IGNORED",
                    path,
                    "setup time given but setup policy is NONE",
                    blocking=False,
                )
            mats: list[tuple[str, Fraction]] = []
            for u in op.materials:
                target_mat = next(
                    (x for x in model.materials if x.material_id == u.material_id), None
                )
                if target_mat is None:
                    ctx.bad("UNKNOWN_MATERIAL", path, u.material_id)
                    continue
                v = _same_unit_qty(
                    ctx, u.per_piece, target_mat.unit, f"{path}.materials[{u.material_id}]"
                )
                if v is not None:
                    mats.append((u.material_id, v))
            for pid, n in op.pools:
                if pid not in p_ids:
                    ctx.bad("UNKNOWN_POOL", path, pid)
                elif n < 1 or n > pools.get(pid, 0):
                    ctx.bad(
                        "POOL_REQUEST_INVALID", path, f"needs {n} of {pid} (size {pools.get(pid)})"
                    )
            scrap = Fraction(0)
            if op.scrap_rate is not None:
                s = ctx.exact(op.scrap_rate, f"{path}.scrap_rate")
                if s is not None and not (0 <= s < 1):
                    ctx.bad("INVALID_SCRAP_RATE", path, "scrap rate must be in [0, 1)")
                elif s is not None:
                    scrap = s
            if proc is not None:
                ops.append(
                    COp(op.op_id, op.input_buffer, tuple(op.machines), proc, setup, op.setup_policy,
                        tuple(mats), tuple(op.pools), scrap)
                )  # fmt: skip
        if len(ops) == len(r.operations):
            compiled_ops[r.routing_id] = tuple(ops)

    routes: dict[str, tuple[COp, ...]] = {}
    batch: dict[str, int] = {}
    for prd in model.products:
        path = f"products[{prd.product_id}]"
        if prd.routing_id not in r_ids:
            ctx.bad("UNKNOWN_ROUTING", path, prd.routing_id)
            continue
        if prd.batch_size < 1:
            ctx.bad("INVALID_BATCH", path, "batch size must be >= 1")
            continue
        for op in route_of[prd.routing_id].operations:
            cap = buffers.get(op.input_buffer)
            if cap is not None and prd.batch_size > cap:
                ctx.bad(
                    "BATCH_EXCEEDS_BUFFER",
                    path,
                    f"batch {prd.batch_size} > {op.input_buffer} capacity {cap}",
                )
        if prd.routing_id in compiled_ops:
            routes[prd.product_id] = compiled_ops[prd.routing_id]
        batch[prd.product_id] = prd.batch_size

    orders = []
    for o in model.orders:
        path = f"orders[{o.order_id}]"
        if o.product_id not in prod_ids:
            ctx.bad("UNKNOWN_PRODUCT", path, o.product_id)
            continue
        if o.quantity < 1:
            ctx.bad("INVALID_QUANTITY", path, "quantity must be a positive whole number of pieces")
            continue
        rel = ctx.ticks(o.release_at, f"{path}.release_at")
        due = ctx.ticks(o.due_at, f"{path}.due_at") if o.due_at is not None else None
        if rel is not None:
            orders.append(COrder(o.order_id, o.product_id, o.quantity, rel, due, o.priority))
    n_jobs = sum(-(-o.quantity // max(batch.get(o.product_id, 1), 1)) for o in orders)
    if n_jobs > 100_000:
        ctx.bad("MODEL_TOO_LARGE", "orders", f"{n_jobs} jobs exceed the job limit")

    bds = []
    for i, bd in enumerate(model.breakdowns):
        path = f"breakdowns[{i}]"
        if bd.machine_id not in m_ids:
            ctx.bad("UNKNOWN_MACHINE", path, bd.machine_id)
            continue
        a, d = ctx.ticks(bd.at, f"{path}.at"), ctx.ticks(bd.duration, f"{path}.duration")
        if a is not None and d is not None:
            if d <= 0:
                ctx.bad("INVALID_INTERVAL", path, "breakdown duration must be > 0")
            else:
                bds.append((bd.machine_id, a, d))

    if not model.policies.pools_held_during_interruption:
        ctx.bad(
            "UNSUPPORTED_POLICY", "policies.pools_held_during_interruption",
            "releasing shared resources during an interruption is not implemented",
        )  # fmt: skip

    v_rep = _report(sha, ctx)
    if not v_rep.ok or horizon is None:
        return None, v_rep
    assert v_rep.unit_evidence is not None  # noqa: S101 - set by _report when ok
    cm = CompiledModel(
        model_sha256=sha, model_id=model.model_id, factory_id=model.factory_id, horizon=horizon,
        machines=machines, buffers=buffers, pools=pools, materials=materials, routes=routes,
        batch=batch, orders=tuple(orders), breakdowns=tuple(sorted(bds)), calendars=calendars,
        policies=model.policies, stochastic=stochastic, unit_evidence=v_rep.unit_evidence,
        assumptions=model.assumptions,
    )  # fmt: skip
    return cm, v_rep


def _same_unit_qty(ctx: _Ctx, q: Qty, unit: str, path: str) -> Fraction | None:
    """Material quantities must already be in the material's unit: no implicit conversion."""
    try:
        a, b = parse_unit(q.unit), parse_unit(unit)
    except UnitError as exc:
        ctx.bad("UNIT_REJECTED", path, str(exc))
        return None
    if a.dimension != b.dimension:
        ctx.bad("UNIT_MISMATCH", path, f"{q.unit!r} is incompatible with {unit!r}")
        return None
    if a.symbol != b.symbol:
        ctx.bad(
            "UNIT_CONVERSION_REQUIRED", path,
            f"{q.unit!r} must be converted to {unit!r} explicitly before simulation",
        )  # fmt: skip
        return None
    v = ctx.exact(q.value, path)
    if v is not None and v < 0:
        ctx.bad("NEGATIVE_QUANTITY", path, "quantity must be >= 0")
        return None
    return v


def _report(sha: str, ctx: _Ctx) -> ValidationReport:
    ok = not any(i.blocking for i in ctx.issues)
    conv = tuple(ctx.conv)
    ev_sha = hashlib.sha256(
        json.dumps([c.model_dump() for c in conv], sort_keys=True).encode()
    ).hexdigest()
    return ValidationReport(
        model_sha256=sha, ok=ok, issues=tuple(ctx.issues),
        unit_evidence=UnitEvidence(conversions=conv, sha256=ev_sha),
    )  # fmt: skip
