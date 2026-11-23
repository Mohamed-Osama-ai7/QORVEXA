"""Discrete-event simulation kernel.

* Integer clock (ticks = seconds). Events live in a binary heap ordered by
  ``(time, kind rank, sequence)``, so simultaneous events are processed in a fixed, documented order
  and the run is bit-for-bit reproducible.
* After all events of one instant are applied, a dispatch fix-point runs: admit released jobs,
  unload blocked machines, then start work on idle available machines (sorted by machine id; jobs
  ordered by the dispatch policy).
* Machine time is accounted in exactly one state at every instant: PROCESSING, SETUP, BLOCKED,
  STARVED, WAITING_RESOURCE, DOWN, PLANNED_DOWNTIME, OFF_SHIFT.
* Blocking after service: a finished job that cannot enter the next buffer stays on its machine.
* Deadlocks (a closed set of blocked machines whose target buffers can only be drained by each
  other) stop the run with a diagnosis. Event limits, a wall-clock timeout and a cancellation token
  bound every run. Advisory only: nothing here talks to equipment.
"""

from __future__ import annotations

import hashlib
import heapq
import time
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from fractions import Fraction
from typing import Final, Protocol

from qorvexa.industrial.digital_twin.contracts import DispatchPolicy, InterruptPolicy, SetupPolicy
from qorvexa.industrial.digital_twin.stochastic import Streams
from qorvexa.industrial.digital_twin.validation import CompiledModel, COp, COrder

DEFAULT_MAX_EVENTS: Final = 2_000_000
TRACE_LIMIT: Final = 20_000
STATES: Final = (
    "PROCESSING", "SETUP", "BLOCKED", "STARVED", "WAITING_RESOURCE", "DOWN",
    "PLANNED_DOWNTIME", "OFF_SHIFT",
)  # fmt: skip


class Ev(IntEnum):
    """Rank among simultaneous events (lower first). Work finishing exactly at a shift end
    completes before the shift closes; repairs and openings precede closings."""

    PHASE_END = 0
    REPAIR = 1
    SHIFT_ON = 2
    PLANNED_END = 3
    SHIFT_OFF = 4
    PLANNED_START = 5
    FAIL = 6
    DELIVERY = 7
    RELEASE = 8
    HORIZON = 9


class RunStatus(StrEnum):
    COMPLETED = "COMPLETED"  # every released job finished or was scrapped
    HORIZON_REACHED = "HORIZON_REACHED"  # stopped at the horizon with work remaining
    DEADLOCK = "DEADLOCK"
    EVENT_LIMIT = "EVENT_LIMIT"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


class CancelToken(Protocol):
    def is_set(self) -> bool: ...


@dataclass
class Job:
    seq: int
    job_id: str
    order: COrder
    pieces: int
    route: tuple[COp, ...]
    released: int
    step: int = 0
    enter_buf: int = 0
    wait: int = 0
    entered_line: int | None = None
    first_start: int | None = None
    finished: int | None = None
    scrapped: int = 0
    initial_pieces: int = 0


@dataclass
class MRun:
    machine_id: str
    calendar_id: str
    shift_on: bool = False
    planned: bool = False
    failed: bool = False
    job: Job | None = None
    op: COp | None = None
    phase: str | None = None  # SETUP | PROCESS | BLOCKED
    phase_end: int | None = None
    phase_full: int = 0
    remaining: int | None = None  # suspended work
    token: int = 0
    fail_token: int = 0
    repair_end: int | None = None
    last_product: str | None = None
    wait: str = "STARVED"
    acc: dict[str, int] = field(default_factory=lambda: dict.fromkeys(STATES, 0))
    failures: int = 0
    jobs_done: int = 0
    pieces_done: int = 0

    @property
    def available(self) -> bool:
        return self.shift_on and not self.planned and not self.failed

    def state(self) -> str:
        if self.failed:
            return "DOWN"
        if not self.shift_on:
            return "OFF_SHIFT"
        if self.planned:
            return "PLANNED_DOWNTIME"
        if self.phase == "SETUP":
            return "SETUP"
        if self.phase == "PROCESS":
            return "PROCESSING"
        if self.phase == "BLOCKED":
            return "BLOCKED"
        return self.wait


@dataclass
class BRun:
    buffer_id: str
    capacity: int | None
    items: list[Job] = field(default_factory=list)
    occ: int = 0
    area: int = 0
    peak: int = 0

    def room(self, pieces: int) -> bool:
        return self.capacity is None or self.occ + pieces <= self.capacity


@dataclass
class RunData:
    """Raw outcome of one replication (converted to typed metrics by ``metrics.py``)."""

    status: RunStatus
    end_time: int
    horizon: int
    events: int
    event_digest: str
    trace: list[tuple[int, str, str, str]]
    trace_truncated: bool
    machines: dict[str, MRun]
    buffers: dict[str, BRun]
    jobs: list[Job]
    unreleased_pieces: dict[str, int]
    release_backlog_pieces: int
    completed: dict[str, int]
    scrapped: dict[str, int]
    pool_size: dict[str, int]
    pool_area: dict[str, int]
    material_initial: dict[str, Fraction]
    material_delivered: dict[str, Fraction]
    material_consumed: dict[str, Fraction]
    material_stock: dict[str, Fraction]
    wip_area: int
    wip_peak: int
    deadlock: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)


class Simulator:
    def __init__(
        self,
        cm: CompiledModel,
        *,
        seed: int = 0,
        replication: int = 0,
        max_events: int = DEFAULT_MAX_EVENTS,
        timeout_s: float = 60.0,
        cancel: CancelToken | None = None,
        trace_limit: int = TRACE_LIMIT,
    ) -> None:
        self.cm = cm
        self.rand = Streams(seed, replication)
        self.max_events, self.timeout_s, self.cancel = max_events, timeout_s, cancel
        self.trace_limit = trace_limit
        self.heap: list[tuple[int, int, int, tuple[object, ...]]] = []
        self.seq = 0
        self.now = 0
        self.machines = {k: MRun(k, cm.machines[k].calendar_id) for k in sorted(cm.machines)}
        self.by_calendar: dict[str, list[MRun]] = {}
        for m in self.machines.values():
            self.by_calendar.setdefault(m.calendar_id, []).append(m)
        self.buffers = {k: BRun(k, v) for k, v in sorted(cm.buffers.items())}
        self.pool_free = dict(cm.pools)
        self.pool_area = dict.fromkeys(cm.pools, 0)
        self.stock = {k: v.initial for k, v in cm.materials.items()}
        self.delivered = dict.fromkeys(cm.materials, Fraction(0))
        self.consumed = dict.fromkeys(cm.materials, Fraction(0))
        self.release_q: dict[str, deque[Job]] = {}
        self.jobs: list[Job] = []
        self.completed: dict[str, int] = dict.fromkeys(cm.routes, 0)
        self.scrapped: dict[str, int] = dict.fromkeys(cm.routes, 0)
        self.unreleased = {o.order_id: o.quantity for o in cm.orders}
        self.scrap_acc: dict[str, Fraction] = {}
        self.wip_area = self.wip_peak = 0
        self.events = 0
        self.digest = hashlib.sha256()
        self.trace: list[tuple[int, str, str, str]] = []
        self.trace_truncated = False
        # machine -> buffers it can draw from
        self.feeds: dict[str, list[str]] = {k: [] for k in self.machines}
        self.consumers: dict[str, set[str]] = {}
        for route in cm.routes.values():
            for op in route:
                for mid in op.machines:
                    if op.in_buf not in self.feeds[mid]:
                        self.feeds[mid].append(op.in_buf)
                    self.consumers.setdefault(op.in_buf, set()).add(mid)
        for v in self.feeds.values():
            v.sort()

    # ------------------------------------------------------------------ plumbing
    def push(self, t: int, kind: Ev, *payload: object) -> None:
        self.seq += 1
        heapq.heappush(self.heap, (t, int(kind), self.seq, payload))

    def log(self, kind: str, a: str = "", b: str = "") -> None:
        rec = (self.now, kind, a, b)
        self.digest.update(repr(rec).encode())
        if len(self.trace) < self.trace_limit:
            self.trace.append(rec)
        else:
            self.trace_truncated = True

    def _advance(self, t: int) -> None:
        dt = t - self.now
        if dt <= 0:
            return
        for m in self.machines.values():
            m.acc[m.state()] += dt
        wip = 0
        for b in self.buffers.values():
            b.area += b.occ * dt
            wip += b.occ
        wip += sum(m.job.pieces for m in self.machines.values() if m.job is not None)
        self.wip_area += wip * dt
        for pid, size in self.cm.pools.items():
            self.pool_area[pid] += (size - self.pool_free[pid]) * dt
        self.now = t

    def _wip_now(self) -> int:
        return sum(b.occ for b in self.buffers.values()) + sum(
            m.job.pieces for m in self.machines.values() if m.job is not None
        )

    # ------------------------------------------------------------------ setup
    def _schedule_static(self) -> None:
        cm = self.cm
        for cid, windows in sorted(cm.calendars.items()):
            if cid not in self.by_calendar:
                continue
            for s, e in windows:
                self.push(s, Ev.SHIFT_ON, cid)
                if e < cm.horizon:
                    self.push(e, Ev.SHIFT_OFF, cid)
        for mid, mc in sorted(cm.machines.items()):
            for s, e in mc.planned:
                self.push(s, Ev.PLANNED_START, mid)
                self.push(e, Ev.PLANNED_END, mid)
            if mc.failure is not None:
                ttf = self.rand.ticks(mc.failure[0], f"ttf:{mid}")
                self.push(ttf, Ev.FAIL, mid, None, self.machines[mid].fail_token)
        for mid, at, dur in cm.breakdowns:
            self.push(at, Ev.FAIL, mid, dur, -1)
        for mat in sorted(cm.materials.values(), key=lambda x: x.material_id):
            for t, q in mat.deliveries:
                self.push(t, Ev.DELIVERY, mat.material_id, q)
        for o in sorted(cm.orders, key=lambda o: (o.release, o.order_id)):
            self.push(o.release, Ev.RELEASE, o.order_id)
        self.push(cm.horizon, Ev.HORIZON)

    # ------------------------------------------------------------------ run
    def run(self) -> RunData:
        self._schedule_static()
        orders = {o.order_id: o for o in self.cm.orders}
        status: RunStatus | None = None
        deadlock: tuple[str, ...] = ()
        started = time.monotonic()
        loops = 0
        end = self.cm.horizon
        while self.heap:
            t = self.heap[0][0]
            if t > self.cm.horizon:
                break
            self._advance(t)
            while self.heap and self.heap[0][0] == t:
                _, kind, _, payload = heapq.heappop(self.heap)
                if self._handle(Ev(kind), payload, orders):
                    self.events += 1
                if self.events >= self.max_events:
                    status = RunStatus.EVENT_LIMIT
                    break
            if status is not None:
                end = t
                break
            self._dispatch()
            self.wip_peak = max(self.wip_peak, self._wip_now())
            for b in self.buffers.values():
                b.peak = max(b.peak, b.occ)
            dl = self._deadlocked()
            if dl:
                status, deadlock, end = RunStatus.DEADLOCK, dl, t
                self.log("DEADLOCK", ",".join(dl))
                break
            if self._all_done():
                status, end = RunStatus.COMPLETED, t
                break
            loops += 1
            if loops % 64 == 1:
                if self.cancel is not None and self.cancel.is_set():
                    status, end = RunStatus.CANCELLED, t
                    break
                if time.monotonic() - started > self.timeout_s:
                    status, end = RunStatus.TIMEOUT, t
                    break
        if status is None:
            status = RunStatus.HORIZON_REACHED
            self._advance(self.cm.horizon)
            end = self.cm.horizon
        backlog = sum(j.pieces for q in self.release_q.values() for j in q)
        return RunData(
            status=status, end_time=end, horizon=self.cm.horizon, events=self.events,
            event_digest=self.digest.hexdigest(), trace=self.trace,
            trace_truncated=self.trace_truncated, machines=self.machines, buffers=self.buffers,
            jobs=self.jobs, unreleased_pieces=dict(self.unreleased),
            release_backlog_pieces=backlog, completed=self.completed, scrapped=self.scrapped,
            pool_size=dict(self.cm.pools), pool_area=self.pool_area,
            material_initial={k: v.initial for k, v in self.cm.materials.items()},
            material_delivered=self.delivered, material_consumed=self.consumed,
            material_stock=self.stock, wip_area=self.wip_area, wip_peak=self.wip_peak,
            deadlock=deadlock,
        )  # fmt: skip

    def _all_done(self) -> bool:
        if any(self.unreleased.values()):
            return False
        return all(j.finished is not None for j in self.jobs)

    # ------------------------------------------------------------------ events
    def _handle(self, kind: Ev, p: tuple[object, ...], orders: dict[str, COrder]) -> bool:
        if kind is Ev.PHASE_END:
            m = self.machines[str(p[0])]
            if p[1] != m.token:
                return False  # superseded by an interruption
            self._phase_end(m)
        elif kind in (Ev.SHIFT_ON, Ev.SHIFT_OFF):
            for m in self.by_calendar.get(str(p[0]), []):
                before = m.available
                m.shift_on = kind is Ev.SHIFT_ON
                self._avail_changed(m, before)
            self.log(kind.name, str(p[0]))
        elif kind in (Ev.PLANNED_START, Ev.PLANNED_END):
            m = self.machines[str(p[0])]
            before = m.available
            m.planned = kind is Ev.PLANNED_START
            self.log(kind.name, m.machine_id)
            self._avail_changed(m, before)
        elif kind is Ev.FAIL:
            self._fail(self.machines[str(p[0])], p[1], p[2])
        elif kind is Ev.REPAIR:
            m = self.machines[str(p[0])]
            if p[1] != m.fail_token:
                return False
            before = m.available
            m.failed, m.repair_end = False, None
            self.log("REPAIRED", m.machine_id)
            self._avail_changed(m, before)
            mc = self.cm.machines[m.machine_id]
            if mc.failure is not None and p[2]:
                ttf = self.rand.ticks(mc.failure[0], f"ttf:{m.machine_id}")
                self.push(self.now + ttf, Ev.FAIL, m.machine_id, None, m.fail_token)
        elif kind is Ev.DELIVERY:
            mat, q = str(p[0]), p[1]
            assert isinstance(q, Fraction)  # noqa: S101
            self.stock[mat] += q
            self.delivered[mat] += q
            self.log("DELIVERY", mat, str(q))
        elif kind is Ev.RELEASE:
            self._release(orders[str(p[0])])
        elif kind is Ev.HORIZON:
            self.log("HORIZON")
        return True

    def _fail(self, m: MRun, duration: object, chain_token: object) -> None:
        stochastic_chain = duration is None
        if stochastic_chain and chain_token != m.fail_token:
            return
        mc = self.cm.machines[m.machine_id]
        if stochastic_chain:
            assert mc.failure is not None  # noqa: S101
            dur = self.rand.ticks(mc.failure[1], f"repair:{m.machine_id}")
        else:
            assert isinstance(duration, int)  # noqa: S101
            dur = duration
        before = m.available
        end = self.now + dur
        if m.failed and m.repair_end is not None:
            end = max(end, m.repair_end)
        m.failed, m.repair_end = True, end
        m.failures += 1
        m.fail_token += 1
        self.log("FAILURE", m.machine_id, str(dur))
        self.push(
            end, Ev.REPAIR, m.machine_id, m.fail_token, stochastic_chain or mc.failure is not None
        )
        self._avail_changed(m, before)

    def _avail_changed(self, m: MRun, before: bool) -> None:
        after = m.available
        if before and not after and m.phase in ("SETUP", "PROCESS") and m.phase_end is not None:
            rem = m.phase_end - self.now
            if self.cm.policies.interrupt is InterruptPolicy.RESTART:
                rem = m.phase_full
            m.remaining, m.phase_end = rem, None
            m.token += 1
            self.log("INTERRUPT", m.machine_id, f"{m.phase}:{rem}")
        elif not before and after and m.remaining is not None:
            m.phase_end = self.now + m.remaining
            m.remaining = None
            m.token += 1
            self.push(m.phase_end, Ev.PHASE_END, m.machine_id, m.token)
            self.log("RESUME", m.machine_id, str(m.phase))

    def _release(self, o: COrder) -> None:
        route = self.cm.routes[o.product_id]
        batch = self.cm.batch[o.product_id]
        left = o.quantity
        k = 0
        while left > 0:
            n = min(batch, left)
            k += 1
            j = Job(
                seq=len(self.jobs), job_id=f"{o.order_id}#{k}", order=o, pieces=n, route=route,
                released=self.now, initial_pieces=n,
            )  # fmt: skip
            self.jobs.append(j)
            self.release_q.setdefault(route[0].in_buf, deque()).append(j)
            left -= n
        self.unreleased[o.order_id] = 0
        self.log("RELEASE", o.order_id, str(o.quantity))

    # ------------------------------------------------------------------ work
    def _phase_end(self, m: MRun) -> None:
        job, op = m.job, m.op
        assert job is not None and op is not None  # noqa: S101
        if m.phase == "SETUP":
            m.last_product = job.order.product_id
            self.log("SETUP_END", m.machine_id, job.job_id)
            self._begin(m, "PROCESS", self._proc_ticks(op, m, job))
            return
        # processing finished
        for pid, n in op.pools:
            self.pool_free[pid] += n
        m.last_product = job.order.product_id
        m.jobs_done += 1
        scrap = self._scrap(op, job)
        if scrap:
            job.pieces -= scrap
            job.scrapped += scrap
            self.scrapped[job.order.product_id] += scrap
            self.log("SCRAP", job.job_id, str(scrap))
        m.pieces_done += job.pieces
        self.log("OP_END", m.machine_id, f"{job.job_id}@{op.op_id}")
        job.step += 1
        m.phase = m.phase_end = None
        if job.pieces == 0:
            job.finished = self.now
            m.job = m.op = None
            return
        if job.step == len(job.route):
            job.finished = self.now
            self.completed[job.order.product_id] += job.pieces
            self.log("COMPLETE", job.job_id, str(job.pieces))
            m.job = m.op = None
            return
        nxt = self.buffers[job.route[job.step].in_buf]
        if nxt.room(job.pieces):
            self._enter(job, nxt)
            m.job = m.op = None
        else:
            m.phase = "BLOCKED"
            self.log("BLOCKED", m.machine_id, nxt.buffer_id)

    def _scrap(self, op: COp, job: Job) -> int:
        if op.scrap == 0:
            return 0
        if self.cm.stochastic:
            return sum(
                1 for _ in range(job.pieces) if self.rand.bernoulli(op.scrap, f"scrap:{op.op_id}")
            )
        acc = self.scrap_acc.get(op.op_id, Fraction(0))
        new = acc + op.scrap * job.pieces
        self.scrap_acc[op.op_id] = new
        return min(job.pieces, int(new) - int(acc))  # deterministic accumulation, no rounding

    def _proc_ticks(self, op: COp, m: MRun, job: Job) -> int:
        return self.rand.ticks(op.proc, f"proc:{op.op_id}:{m.machine_id}") * job.pieces

    def _begin(self, m: MRun, phase: str, dur: int) -> None:
        m.phase, m.phase_full = phase, dur
        m.token += 1
        if m.available:
            m.phase_end = self.now + dur
            self.push(m.phase_end, Ev.PHASE_END, m.machine_id, m.token)
        else:  # cannot happen for a fresh start; kept for setup->process across a closure
            m.remaining, m.phase_end = dur, None
        self.log(f"{phase}_START", m.machine_id, m.job.job_id if m.job else "")

    def _enter(self, job: Job, b: BRun) -> None:
        b.items.append(job)
        b.occ += job.pieces
        job.enter_buf = self.now

    def _feasible(self, job: Job, op: COp) -> bool:
        for mat, per in op.materials:
            if self.stock[mat] < per * job.pieces:
                return False
        return all(self.pool_free[pid] >= n for pid, n in op.pools)

    def _start(self, m: MRun, job: Job, b: BRun, op: COp) -> None:
        b.items.remove(job)
        b.occ -= job.pieces
        job.wait += self.now - job.enter_buf
        if job.first_start is None:
            job.first_start = self.now
        for mat, per in op.materials:
            need = per * job.pieces
            self.stock[mat] -= need
            self.consumed[mat] += need
        for pid, n in op.pools:
            self.pool_free[pid] -= n
        m.job, m.op = job, op
        product = job.order.product_id
        needs_setup = op.setup is not None and (
            op.setup_policy is SetupPolicy.EVERY_JOB
            or (op.setup_policy is SetupPolicy.ON_PRODUCT_CHANGE and m.last_product != product)
        )
        self.log("START", m.machine_id, f"{job.job_id}@{op.op_id}")
        if needs_setup and op.setup is not None:
            self._begin(m, "SETUP", self.rand.ticks(op.setup, f"setup:{op.op_id}:{m.machine_id}"))
        else:
            self._begin(m, "PROCESS", self._proc_ticks(op, m, job))

    def _order_key(self, job: Job) -> tuple[float, ...]:
        pol = self.cm.policies.dispatch
        if pol is DispatchPolicy.PRIORITY:
            return (-job.order.priority, job.enter_buf, job.seq)
        if pol is DispatchPolicy.EDD:
            due = job.order.due if job.order.due is not None else float("inf")
            return (due, job.enter_buf, job.seq)
        return (job.enter_buf, job.seq)

    def _dispatch(self) -> None:
        changed, rounds = True, 0
        unload_off = self.cm.policies.blocked_machine_may_unload_off_shift
        while changed:
            rounds += 1
            if rounds > 10_000:  # pragma: no cover - defensive: dispatch must converge
                raise RuntimeError("dispatch did not converge")
            changed = False
            for buf_id in sorted(self.release_q):
                q, b = self.release_q[buf_id], self.buffers[buf_id]
                while q and b.room(q[0].pieces):
                    j = q.popleft()
                    self._enter(j, b)
                    j.entered_line = self.now
                    self.log("ADMIT", j.job_id, buf_id)
                    changed = True
            for m in self.machines.values():
                if m.phase != "BLOCKED" or m.job is None:
                    continue
                if not (m.available or unload_off):
                    continue
                nxt = self.buffers[m.job.route[m.job.step].in_buf]
                if nxt.room(m.job.pieces):
                    self._enter(m.job, nxt)
                    self.log("UNBLOCK", m.machine_id, nxt.buffer_id)
                    m.job = m.op = m.phase = None
                    changed = True
            for m in self.machines.values():
                if m.job is not None or not m.available:
                    continue
                cands: list[tuple[tuple[float, ...], Job, BRun, COp]] = []
                for buf_id in self.feeds[m.machine_id]:
                    b = self.buffers[buf_id]
                    for j in b.items:
                        op = j.route[j.step]
                        if op.in_buf == buf_id and m.machine_id in op.machines:
                            cands.append((self._order_key(j), j, b, op))
                cands.sort(key=lambda c: c[0])
                for _, j, b, op in cands:
                    if self._feasible(j, op):
                        self._start(m, j, b, op)
                        changed = True
                        break
                else:
                    m.wait = "WAITING_RESOURCE" if cands else "STARVED"

    def _deadlocked(self) -> tuple[str, ...]:
        blocked = {
            m.machine_id: m.job.route[m.job.step].in_buf
            for m in self.machines.values()
            if m.phase == "BLOCKED" and m.job is not None
        }
        unload_off = self.cm.policies.blocked_machine_may_unload_off_shift
        # a blocked machine that may unload later (when it becomes available) is not stuck
        s = {k for k in blocked if unload_off or self.machines[k].available}
        shrink = True
        while shrink:
            shrink = False
            for mid in sorted(s):
                if not self.consumers.get(blocked[mid], set()) <= s:
                    s.discard(mid)
                    shrink = True
        return tuple(sorted(s))
