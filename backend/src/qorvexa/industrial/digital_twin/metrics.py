"""Operational metrics computed only from simulated events and exact time accounting.

Ratios keep their exact numerator/denominator; ``value`` is a 6-decimal display of that ratio.
Definitions (all over the observed window ``[0, end_time]``):

* utilization = (processing + setup) / available time, where available time = time on shift minus
  planned downtime (unplanned failures stay inside available time and are reported as DOWN).
* throughput = completed pieces per hour of window; WIP = time-average pieces inside the line
  (buffers + machines); lead time = completion - order release; cycle time = completion - first
  operation start; waiting = time spent in buffers. Averages are per transfer batch (job).
* No OEE, availability % or efficiency is reported: their inputs (ideal rates, quality definitions)
  are not part of the model.
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

from pydantic import BaseModel, ConfigDict

from qorvexa.industrial.digital_twin.kernel import RunData, RunStatus

F = ConfigDict(frozen=True, extra="forbid")
BOTTLENECK_UTILIZATION = Fraction(85, 100)


class Ratio(BaseModel):
    model_config = F
    num: int
    den: int
    value: str


def ratio(num: Fraction | int, den: Fraction | int) -> Ratio | None:
    if den == 0:
        return None
    f = Fraction(num) / Fraction(den)
    d = (Decimal(f.numerator) / Decimal(f.denominator)).quantize(Decimal("0.000001"))
    return Ratio(num=f.numerator, den=f.denominator, value=str(d))


class MachineMetrics(BaseModel):
    model_config = F
    machine_id: str
    processing_s: int
    setup_s: int
    blocked_s: int
    starved_s: int
    waiting_resource_s: int
    down_s: int
    planned_downtime_s: int
    off_shift_s: int
    available_s: int
    utilization: Ratio | None
    failures: int
    jobs: int
    pieces: int


class BufferMetrics(BaseModel):
    model_config = F
    buffer_id: str
    capacity: int | None
    avg_occupancy: Ratio | None
    peak: int
    final: int


class ProductMetrics(BaseModel):
    model_config = F
    product_id: str
    completed: int
    scrapped: int
    throughput_per_hour: Ratio | None
    avg_lead_time_s: Ratio | None
    avg_cycle_time_s: Ratio | None
    avg_wait_s: Ratio | None


class OrderMetrics(BaseModel):
    model_config = F
    order_id: str
    product_id: str
    quantity: int
    completed: int
    scrapped: int
    finished_at: int | None
    due: int | None
    late: bool | None  # None: no due date given


class PoolMetrics(BaseModel):
    model_config = F
    pool_id: str
    size: int
    utilization: Ratio | None


class MaterialMetrics(BaseModel):
    model_config = F
    material_id: str
    initial: str
    delivered: str
    consumed: str
    final: str
    balanced: bool


class Bottleneck(BaseModel):
    model_config = F
    rank: int
    machine_id: str
    utilization: Ratio | None
    avg_input_queue: Ratio | None
    upstream_blocked_s: int
    reasons: tuple[str, ...]


class Conservation(BaseModel):
    model_config = F
    released: int
    completed: int
    scrapped: int
    in_buffers: int
    on_machines: int
    release_backlog: int
    unreleased: int
    ordered: int
    balanced: bool


class Metrics(BaseModel):
    model_config = F
    window_s: int
    completed: int
    scrapped: int
    throughput_per_hour: Ratio | None
    wip_avg: Ratio | None
    wip_peak: int
    avg_lead_time_s: Ratio | None
    avg_cycle_time_s: Ratio | None
    avg_wait_s: Ratio | None
    late_orders: int
    on_time_orders: int
    unfinished_orders: int
    machines: tuple[MachineMetrics, ...]
    buffers: tuple[BufferMetrics, ...]
    products: tuple[ProductMetrics, ...]
    orders: tuple[OrderMetrics, ...]
    pools: tuple[PoolMetrics, ...]
    materials: tuple[MaterialMetrics, ...]
    bottlenecks: tuple[Bottleneck, ...]
    conservation: Conservation

    def machine(self, mid: str) -> MachineMetrics:
        return next(m for m in self.machines if m.machine_id == mid)

    def product(self, pid: str) -> ProductMetrics:
        return next(p for p in self.products if p.product_id == pid)


def _mean(xs: list[int]) -> Ratio | None:
    return ratio(sum(xs), len(xs)) if xs else None


def compute_metrics(run: RunData) -> Metrics:
    w = run.end_time
    machines = []
    for mid, m in sorted(run.machines.items()):
        a = m.acc
        available = w - a["OFF_SHIFT"] - a["PLANNED_DOWNTIME"]
        machines.append(
            MachineMetrics(
                machine_id=mid, processing_s=a["PROCESSING"], setup_s=a["SETUP"],
                blocked_s=a["BLOCKED"], starved_s=a["STARVED"],
                waiting_resource_s=a["WAITING_RESOURCE"], down_s=a["DOWN"],
                planned_downtime_s=a["PLANNED_DOWNTIME"], off_shift_s=a["OFF_SHIFT"],
                available_s=available, utilization=ratio(a["PROCESSING"] + a["SETUP"], available),
                failures=m.failures, jobs=m.jobs_done, pieces=m.pieces_done,
            )
        )  # fmt: skip
    buffers = tuple(
        BufferMetrics(
            buffer_id=b.buffer_id, capacity=b.capacity, avg_occupancy=ratio(b.area, w),
            peak=b.peak, final=b.occ,
        )
        for b in run.buffers.values()
    )  # fmt: skip
    done = [j for j in run.jobs if j.finished is not None and j.pieces > 0]
    products = []
    for pid in sorted(run.completed):
        js = [j for j in done if j.order.product_id == pid]
        products.append(
            ProductMetrics(
                product_id=pid, completed=run.completed[pid], scrapped=run.scrapped[pid],
                throughput_per_hour=ratio(run.completed[pid] * 3600, w),
                avg_lead_time_s=_mean(
                    [j.finished - j.released for j in js if j.finished is not None]
                ),
                avg_cycle_time_s=_mean(
                    [
                        j.finished - j.first_start
                        for j in js
                        if j.finished is not None and j.first_start is not None
                    ]
                ),
                avg_wait_s=_mean([j.wait for j in js]),
            )
        )  # fmt: skip
    orders, late, on_time, unfinished = [], 0, 0, 0
    by_order: dict[str, list] = {}  # type: ignore[type-arg]
    for j in run.jobs:
        by_order.setdefault(j.order.order_id, []).append(j)
    order_objs = {j.order.order_id: j.order for j in run.jobs}
    for oid in sorted(set(order_objs) | set(run.unreleased_pieces)):
        o = order_objs.get(oid)
        js = by_order.get(oid, [])
        all_done = (
            bool(js)
            and all(j.finished is not None for j in js)
            and not run.unreleased_pieces.get(oid)
        )
        fin = (
            max((j.finished for j in js if j.finished is not None), default=None)
            if all_done
            else None
        )
        due = o.due if o else None
        is_late: bool | None = None
        if due is not None:
            is_late = (fin is not None and fin > due) or (fin is None and w > due)
        if not all_done:
            unfinished += 1
        if is_late:
            late += 1
        elif is_late is False:
            on_time += 1
        orders.append(
            OrderMetrics(
                order_id=oid, product_id=o.product_id if o else "", quantity=o.quantity if o else 0,
                completed=sum(j.pieces for j in js if j.finished is not None),
                scrapped=sum(j.scrapped for j in js), finished_at=fin, due=due, late=is_late,
            )
        )  # fmt: skip
    pools = tuple(
        PoolMetrics(pool_id=p, size=s, utilization=ratio(run.pool_area[p], s * w))
        for p, s in sorted(run.pool_size.items())
    )
    materials = tuple(
        MaterialMetrics(
            material_id=k, initial=str(run.material_initial[k]),
            delivered=str(run.material_delivered[k]), consumed=str(run.material_consumed[k]),
            final=str(run.material_stock[k]),
            balanced=run.material_initial[k] + run.material_delivered[k] - run.material_consumed[k]
            == run.material_stock[k],
        )
        for k in sorted(run.material_initial)
    )  # fmt: skip
    released = sum(j.initial_pieces for j in run.jobs)
    completed = sum(run.completed.values())
    scrapped = sum(run.scrapped.values())
    in_buf = sum(b.occ for b in run.buffers.values())
    on_m = sum(m.job.pieces for m in run.machines.values() if m.job is not None)
    ordered = sum(j.order.quantity for j in {j.order.order_id: j for j in run.jobs}.values()) + sum(
        run.unreleased_pieces.values()
    )
    cons = Conservation(
        released=released, completed=completed, scrapped=scrapped, in_buffers=in_buf,
        on_machines=on_m, release_backlog=run.release_backlog_pieces,
        unreleased=sum(run.unreleased_pieces.values()), ordered=ordered,
        balanced=released == completed + scrapped + in_buf + on_m + run.release_backlog_pieces
        and ordered == released + sum(run.unreleased_pieces.values()),
    )  # fmt: skip
    return Metrics(
        window_s=w, completed=completed, scrapped=scrapped,
        throughput_per_hour=ratio(completed * 3600, w), wip_avg=ratio(run.wip_area, w),
        wip_peak=run.wip_peak,
        avg_lead_time_s=_mean([j.finished - j.released for j in done if j.finished is not None]),
        avg_cycle_time_s=_mean(
            [
                j.finished - j.first_start
                for j in done
                if j.finished is not None and j.first_start is not None
            ]
        ),
        avg_wait_s=_mean([j.wait for j in done]), late_orders=late, on_time_orders=on_time,
        unfinished_orders=unfinished, machines=tuple(machines), buffers=buffers,
        products=tuple(products), orders=tuple(orders), pools=pools, materials=materials,
        bottlenecks=_bottlenecks(run, machines), conservation=cons,
    )  # fmt: skip


def _bottlenecks(run: RunData, machines: list[MachineMetrics]) -> tuple[Bottleneck, ...]:
    """Candidates, not verdicts: high utilization, input-queue accumulation, upstream blocking."""
    w = run.end_time
    feeds: dict[str, set[str]] = {}
    upstream: dict[str, set[str]] = {}
    for route in {id(j.route): j.route for j in run.jobs}.values():
        for i, op in enumerate(route):
            for mid in op.machines:
                feeds.setdefault(mid, set()).add(op.in_buf)
                if i > 0:
                    upstream.setdefault(mid, set()).update(route[i - 1].machines)
    rows = []
    for mm in machines:
        q = sum((run.buffers[b].area for b in feeds.get(mm.machine_id, ())), 0)
        up = sum(run.machines[u].acc["BLOCKED"] for u in upstream.get(mm.machine_id, ()))
        u = Fraction(mm.utilization.num, mm.utilization.den) if mm.utilization else Fraction(0)
        rows.append((u, q, up, mm))
    rows.sort(key=lambda r: (-r[0], -r[1], -r[2], r[3].machine_id))
    out: list[Bottleneck] = []
    for i, (u, q, up, mm) in enumerate(rows):
        reasons = []
        if u >= BOTTLENECK_UTILIZATION:
            reasons.append(f"utilization {float(u):.3f} >= {float(BOTTLENECK_UTILIZATION)}")
        if i == 0 and u > 0:
            reasons.append("highest utilization in the run")
        if q and q == max(r[1] for r in rows):
            reasons.append("largest time-average input queue")
        if up and up == max(r[2] for r in rows):
            reasons.append("upstream machines blocked longest")
        if reasons:
            out.append(
                Bottleneck(
                    rank=len(out) + 1, machine_id=mm.machine_id, utilization=mm.utilization,
                    avg_input_queue=ratio(q, w), upstream_blocked_s=up, reasons=tuple(reasons),
                )
            )  # fmt: skip
    return tuple(out)


def status_is_complete(status: RunStatus) -> bool:
    return status is RunStatus.COMPLETED
