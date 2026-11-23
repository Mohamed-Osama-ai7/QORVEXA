"""Build a factory model from a CONFIRMED Ontology snapshot plus explicit operational inputs.

Ontology mapping (public contracts only):
* line structure: ``ProductionLine -CONTAINS-> Machine`` (transitively through work centers)
* routing of product P: the single Operation with ``Operation -PRODUCES-> P``, walked backwards over
  ``PRECEDES`` (must be linear)
* machine eligibility and per-piece time: ``Operation -REQUIRES-> Machine`` with ``amount`` in
  time/piece (converted exactly by the Units Engine)
* materials: ``Operation -CONSUMES-> Material`` with ``amount`` in <material unit>/piece

Only CONFIRMED, undisputed records are used. A required relation that is only proposed or is
disputed blocks the build. Buffers, calendars, stock, orders and horizon are not ontology facts:
they must be supplied explicitly in ``OperationalInputs`` or the model stays blocked.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from qorvexa.industrial.digital_twin.contracts import (
    Breakdown,
    Buffer,
    Calendar,
    Dist,
    FactoryModel,
    Interval,
    Machine,
    Material,
    MaterialUse,
    Operation,
    Order,
    Policies,
    Product,
    Qty,
    Routing,
    SetupPolicy,
    SourceRef,
)
from qorvexa.industrial.digital_twin.validation import Issue
from qorvexa.industrial.ontology import Direction, GraphState, Ontology, QuantityValue, Status
from qorvexa.industrial.units import Quantity, UnitError, parse_unit

F = ConfigDict(frozen=True, extra="forbid")
_USED = ("CONTAINS", "PRODUCES", "PRECEDES", "REQUIRES", "CONSUMES")


class OperationalInputs(BaseModel):
    """Explicit operating data the ontology does not hold."""

    model_config = F
    factory_id: str
    model_id: str
    horizon: Qty
    input_buffers: dict[str, Buffer]  # op entity id -> its input buffer
    calendars: tuple[Calendar, ...]
    machine_calendars: dict[str, str]
    planned_downtime: dict[str, tuple[Interval, ...]] = {}
    materials: tuple[Material, ...] = ()
    orders: tuple[Order, ...]
    batch_sizes: dict[str, int]
    setup: dict[str, tuple[SetupPolicy, Dist]] = {}  # op id -> setup
    breakdowns: tuple[Breakdown, ...] = ()
    policies: Policies = Policies()


class TwinBuild(BaseModel):
    model_config = F
    model: FactoryModel | None
    issues: tuple[Issue, ...]
    snapshot: SourceRef

    @property
    def ok(self) -> bool:
        return self.model is not None and not any(i.blocking for i in self.issues)


def build_from_ontology(
    state: GraphState, ontology: Ontology, line_id: str, inputs: OperationalInputs
) -> TwinBuild:
    if inputs.factory_id != state.factory_id:
        raise ValueError(
            "ONTOLOGY_FACTORY_MISMATCH: inputs and ontology belong to different factories"
        )
    snap = SourceRef(
        kind="ontology", ref=state.factory_id, revision=state.revision, sha256=state.head_sha
    )
    issues: list[Issue] = []

    def bad(code: str, path: str, msg: str) -> None:
        issues.append(Issue(code=code, path=path, message=msg))

    def ok_entity(eid: str) -> bool:
        e = state.entities.get(eid)
        return e is not None and e.status is Status.CONFIRMED and not e.disputed

    line = state.entities.get(line_id)
    if line is None or not ontology.is_a(line.data.concept, "ProductionLine"):
        bad("UNKNOWN_LINE", line_id, "not a production line of this factory")
        return TwinBuild(model=None, issues=tuple(issues), snapshot=snap)
    if not ok_entity(line_id):
        bad("UNCONFIRMED_INPUT", line_id, "line is not confirmed or is disputed")

    # any required-type relation that is proposed or disputed blocks the build
    for rid, r in sorted(state.relations.items()):
        if r.data.relation in _USED and (r.status is Status.PROPOSED or r.disputed):
            dispute_msg = "disputed" if r.disputed else "only proposed"
            bad(
                "UNCONFIRMED_INPUT",
                rid,
                f"{r.data.relation} {r.data.source}->{r.data.target} is {dispute_msg}",
            )

    def edges(eid: str, rel: str, direction: Direction) -> list[tuple[str, str, object]]:
        out: list[tuple[str, str, object]] = []
        for r in state.relations_of(eid, direction):
            if r.data.relation != rel or r.status is not Status.CONFIRMED or r.disputed:
                continue
            other = r.data.target if direction is Direction.OUT else r.data.source
            out.append((r.relation_id, other, r.data.attrs.get("amount")))
        return out

    machines: list[str] = []
    stack, seen = [line_id], {line_id}
    while stack:
        cur = stack.pop()
        for _, child, _ in edges(cur, "CONTAINS", Direction.OUT):
            if child in seen:
                continue
            seen.add(child)
            stack.append(child)
            ce = state.entities[child]
            if ontology.is_a(ce.data.concept, "Equipment") and ok_entity(child):
                machines.append(child)
    machines.sort()
    if not machines:
        bad("NO_MACHINES", line_id, "no confirmed machines in this line")

    routings: list[Routing] = []
    products: list[Product] = []
    mat_units = {m.material_id: m.unit for m in inputs.materials}
    product_ids = sorted(
        eid for eid, e in state.entities.items()
        if ontology.is_a(e.data.concept, "Product") and ok_entity(eid)
    )  # fmt: skip
    for pid in product_ids:
        finals = [
            s
            for _, s, _ in edges(pid, "PRODUCES", Direction.IN)
            if ontology.is_a(state.entities[s].data.concept, "Operation")
        ]
        if not finals:
            continue  # not produced by an operation: not part of a routable model
        if len(finals) > 1:
            bad("AMBIGUOUS_ROUTING", pid, f"several final operations: {sorted(finals)}")
            continue
        chain = [finals[0]]
        broken = False
        while True:
            preds = [s for _, s, _ in edges(chain[-1], "PRECEDES", Direction.IN)]
            if not preds:
                break
            if len(preds) > 1:
                bad("NON_LINEAR_ROUTING", chain[-1], f"several predecessors {sorted(preds)}")
                broken = True
                break
            if preds[0] in chain:
                bad("ROUTING_CYCLE", preds[0], "PRECEDES forms a cycle")
                broken = True
                break
            chain.append(preds[0])
        if broken:
            continue
        chain.reverse()
        ops: list[Operation] = []
        for oid in chain:
            if not ok_entity(oid):
                bad("UNCONFIRMED_INPUT", oid, "operation is not confirmed")
                continue
            req = edges(oid, "REQUIRES", Direction.OUT)
            elig, times = [], set()
            for rid, mid, amount in req:
                if mid not in state.entities or not ontology.is_a(
                    state.entities[mid].data.concept, "Equipment"
                ):
                    continue
                if mid not in machines:
                    bad("MACHINE_OUTSIDE_LINE", rid, f"{mid} is not in line {line_id}")
                    continue
                sec = _seconds_per_piece(amount, f"{rid}.amount", bad)
                if sec is not None:
                    elig.append(mid)
                    times.add(sec)
            if not elig:
                bad("MISSING_PROCESSING_TIME", oid, "no confirmed machine with a per-piece time")
            if len(times) > 1:
                bad(
                    "HETEROGENEOUS_RATES",
                    oid,
                    "eligible machines have different times; one rate per operation is supported",
                )
            buf = inputs.input_buffers.get(oid)
            if buf is None:
                bad("MISSING_BUFFER", oid, "no input buffer was supplied for this operation")
                continue
            mats = []
            for rid, mat, amount in edges(oid, "CONSUMES", Direction.OUT):
                mu = mat_units.get(mat)
                if mu is None:
                    bad("MISSING_MATERIAL_STOCK", mat, "material stock/unit not supplied")
                    continue
                per = _per_piece(amount, mu, f"{rid}.amount", bad)
                if per is not None:
                    mats.append(MaterialUse(material_id=mat, per_piece=Qty(value=per, unit=mu)))
            setup = inputs.setup.get(oid)
            proc_dist = Dist(value=str(next(iter(times))), unit="s") if len(times) == 1 else None
            ops.append(
                Operation(
                    op_id=oid,
                    input_buffer=buf.buffer_id,
                    machines=tuple(sorted(elig)),
                    processing=proc_dist,
                    setup=setup[1] if setup else None,
                    setup_policy=setup[0] if setup else SetupPolicy.NONE,
                    materials=tuple(mats),
                )
            )
        if len(ops) == len(chain):
            routings.append(Routing(routing_id=f"route:{pid}", operations=tuple(ops)))
            products.append(
                Product(
                    product_id=pid,
                    routing_id=f"route:{pid}",
                    batch_size=inputs.batch_sizes.get(pid, 0),
                )
            )
            if pid not in inputs.batch_sizes:
                bad("MISSING_BATCH_SIZE", pid, "transfer batch size not supplied")

    structural = {
        "UNKNOWN_LINE",
        "AMBIGUOUS_ROUTING",
        "NON_LINEAR_ROUTING",
        "ROUTING_CYCLE",
        "UNCONFIRMED_INPUT",
    }
    if any(i.code in structural for i in issues) or not routings:
        if not routings:
            bad("NO_ROUTINGS", line_id, "no confirmed product routing could be built")
        return TwinBuild(model=None, issues=tuple(issues), snapshot=snap)
    buffers = {b.buffer_id: b for b in inputs.input_buffers.values()}
    model = FactoryModel(
        model_id=inputs.model_id,
        factory_id=state.factory_id,
        horizon=inputs.horizon,
        machines=tuple(
            Machine(
                machine_id=m,
                name=state.entities[m].data.name,
                line_id=line_id,
                calendar_id=inputs.machine_calendars.get(m),
                planned_downtime=inputs.planned_downtime.get(m, ()),
            )
            for m in machines
        ),
        buffers=tuple(sorted(buffers.values(), key=lambda b: b.buffer_id)),
        calendars=inputs.calendars,
        materials=inputs.materials,
        routings=tuple(routings),
        products=tuple(products),
        orders=inputs.orders,
        breakdowns=inputs.breakdowns,
        policies=inputs.policies,
        sources=(snap,),
        assumptions=(
            "routing, eligibility, per-piece times and material use come from confirmed "
            "ontology relations",
        ),
    )
    return TwinBuild(model=model, issues=tuple(issues), snapshot=snap)


def _seconds_per_piece(amount: object, path: str, bad: object) -> str | None:
    if not isinstance(amount, QuantityValue):
        bad("MISSING_PROCESSING_TIME", path, "REQUIRES has no amount")  # type: ignore[operator]
        return None
    try:
        u = parse_unit(amount.unit)
        if u.dimension != parse_unit("s/piece").dimension:
            bad("UNIT_MISMATCH", path, f"{amount.unit!r} is not time per piece")  # type: ignore[operator]
            return None
        v = Quantity.of(amount.value, u).to("s/piece").result.value
    except UnitError as exc:
        bad("UNIT_REJECTED", path, str(exc))  # type: ignore[operator]
        return None
    if v.denominator != 1:
        bad(
            "DURATION_NOT_ON_RESOLUTION", path, f"{amount.value} {amount.unit} is not whole seconds"
        )  # type: ignore[operator]
        return None
    return str(v.numerator)


def _per_piece(amount: object, mat_unit: str, path: str, bad: object) -> str | None:
    if not isinstance(amount, QuantityValue):
        bad("MISSING_MATERIAL_USE", path, "CONSUMES has no amount")  # type: ignore[operator]
        return None
    try:
        got, want = parse_unit(amount.unit), parse_unit(f"{mat_unit}/piece")
    except UnitError as exc:
        bad("UNIT_REJECTED", path, str(exc))  # type: ignore[operator]
        return None
    if got.dimension != want.dimension:
        bad("UNIT_MISMATCH", path, f"{amount.unit!r} is not {mat_unit}/piece")  # type: ignore[operator]
        return None
    if got.symbol != want.symbol:
        bad(
            "UNIT_CONVERSION_REQUIRED",
            path,
            f"convert {amount.unit!r} to {mat_unit}/piece explicitly",
        )  # type: ignore[operator]
        return None
    return amount.value
