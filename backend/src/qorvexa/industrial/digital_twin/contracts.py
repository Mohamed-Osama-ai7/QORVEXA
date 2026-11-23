"""Typed, versioned simulation contracts: factory model, distributions, policies, scenarios.

Every value that the simulation needs is explicit. ``None`` means UNKNOWN and blocks the
simulations that depend on it; nothing is defaulted from imagination. Quantities and durations are
exact decimal text plus a unit; the Units Engine converts them, explicitly, to simulation ticks.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

F = ConfigDict(frozen=True, extra="forbid")
MODEL_SCHEMA: Final = "qorvexa-twin-model/1"
ENGINE_VERSION: Final = "qorvexa-des/1.0"
RESOLUTION_UNIT: Final = "s"  # one simulation tick = one second

MAX_MACHINES: Final = 500
MAX_OPERATIONS: Final = 2000
MAX_ORDERS: Final = 10_000
MAX_JOBS: Final = 100_000


class Qty(BaseModel):
    """Exact quantity: decimal text + unit expression understood by the Units Engine."""

    model_config = F
    value: str
    unit: str


class DistKind(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    TRIANGULAR = "TRIANGULAR"
    EXPONENTIAL = "EXPONENTIAL"


class Dist(BaseModel):
    """A duration. DETERMINISTIC uses ``value``; TRIANGULAR ``low/mode/high``; EXPONENTIAL
    ``mean``. All parameters share ``unit`` (a time unit). Nothing is fitted from data."""

    model_config = F
    kind: DistKind = DistKind.DETERMINISTIC
    unit: str
    value: str | None = None
    low: str | None = None
    mode: str | None = None
    high: str | None = None
    mean: str | None = None


def fixed(value: str, unit: str) -> Dist:
    return Dist(kind=DistKind.DETERMINISTIC, value=value, unit=unit)


class SetupPolicy(StrEnum):
    NONE = "NONE"
    ON_PRODUCT_CHANGE = "ON_PRODUCT_CHANGE"  # includes the first job a machine ever runs
    EVERY_JOB = "EVERY_JOB"


class InterruptPolicy(StrEnum):
    """What happens to a setup/operation interrupted by a break, planned downtime or failure."""

    RESUME_REMAINING = "RESUME_REMAINING"
    RESTART = "RESTART"  # the interrupted phase starts again from zero


class DispatchPolicy(StrEnum):
    FIFO = "FIFO"  # earliest buffer entry first
    PRIORITY = "PRIORITY"  # highest order priority first, then FIFO
    EDD = "EDD"  # earliest due date first, then FIFO


class QuantityPolicy(StrEnum):
    """How continuous plan quantities may become discrete jobs. There is no implicit default."""

    EXACT_INTEGER_ONLY = "EXACT_INTEGER_ONLY"
    FLOOR_TO_BATCH = "FLOOR_TO_BATCH"
    CEIL_TO_BATCH = "CEIL_TO_BATCH"


class Interval(BaseModel):
    model_config = F
    start: Qty  # offset from simulation start
    end: Qty


class Shift(BaseModel):
    model_config = F
    name: str
    start: str  # "HH:MM" within the day
    end: str  # "HH:MM", may be "24:00"
    breaks: tuple[tuple[str, str], ...] = ()


class Calendar(BaseModel):
    model_config = F
    calendar_id: str
    continuous: bool = False  # explicitly 24/7; otherwise ``shifts`` repeat every day
    shifts: tuple[Shift, ...] = ()


class FailureModel(BaseModel):
    """Stochastic unplanned failures over calendar (wall) time.
    Repair proceeds regardless of shift.
    """

    model_config = F
    time_to_failure: Dist
    repair: Dist


class Breakdown(BaseModel):
    """An explicit unplanned breakdown at a known time (deterministic scenario input)."""

    model_config = F
    machine_id: str
    at: Qty
    duration: Qty


class Machine(BaseModel):
    model_config = F
    machine_id: str
    name: str = ""
    line_id: str | None = None
    calendar_id: str | None = None  # None = unknown availability -> blocks
    planned_downtime: tuple[Interval, ...] = ()
    failures: FailureModel | None = None


class Buffer(BaseModel):
    model_config = F
    buffer_id: str
    capacity: int | Literal["UNBOUNDED"] | None = None  # pieces; None = unknown -> blocks


class ResourcePool(BaseModel):
    """A shared resource (e.g. operators) with a fixed number of identical units."""

    model_config = F
    pool_id: str
    size: int


class Material(BaseModel):
    model_config = F
    material_id: str
    unit: str
    initial_stock: Qty | None = None  # None = unknown -> blocks operations that consume it
    deliveries: tuple[tuple[Qty, Qty], ...] = ()  # (at, quantity)


class MaterialUse(BaseModel):
    model_config = F
    material_id: str
    per_piece: Qty


class Operation(BaseModel):
    model_config = F
    op_id: str
    input_buffer: str
    machines: tuple[str, ...]  # eligible machines
    processing: Dist | None = None  # per piece; None = unknown -> blocks
    setup: Dist | None = None
    setup_policy: SetupPolicy = SetupPolicy.NONE
    materials: tuple[MaterialUse, ...] = ()
    pools: tuple[tuple[str, int], ...] = ()
    scrap_rate: str | None = None  # exact fraction of pieces; only when explicitly modeled


class Routing(BaseModel):
    """Linear routing: operation i feeds input buffer of operation i+1;
    the last feeds the sink.
    """

    model_config = F
    routing_id: str
    operations: tuple[Operation, ...]


class Product(BaseModel):
    model_config = F
    product_id: str
    routing_id: str
    batch_size: int = 1  # transfer batch: pieces moved and processed together


class Order(BaseModel):
    model_config = F
    order_id: str
    product_id: str
    quantity: int
    release_at: Qty
    due_at: Qty | None = None
    priority: int = 0


class Policies(BaseModel):
    model_config = F
    dispatch: DispatchPolicy = DispatchPolicy.FIFO
    interrupt: InterruptPolicy = InterruptPolicy.RESUME_REMAINING
    preemption: Literal["NONE"] = "NONE"  # jobs are never preempted by other jobs
    pools_held_during_interruption: bool = True
    blocked_machine_may_unload_off_shift: bool = True


class SourceRef(BaseModel):
    """Where the model came from (ontology snapshot, plan, manual)."""

    model_config = F
    kind: str
    ref: str
    revision: int | None = None
    sha256: str = ""


class FactoryModel(BaseModel):
    model_config = F
    schema_version: str = MODEL_SCHEMA
    model_id: str
    factory_id: str
    horizon: Qty
    machines: tuple[Machine, ...]
    buffers: tuple[Buffer, ...]
    calendars: tuple[Calendar, ...] = ()
    pools: tuple[ResourcePool, ...] = ()
    materials: tuple[Material, ...] = ()
    routings: tuple[Routing, ...]
    products: tuple[Product, ...]
    orders: tuple[Order, ...]
    breakdowns: tuple[Breakdown, ...] = ()
    policies: Policies = Policies()
    sources: tuple[SourceRef, ...] = ()
    assumptions: tuple[str, ...] = ()


# ----------------------------------------------------------------------------- scenarios
class SetProcessingTime(BaseModel):
    model_config = F
    kind: Literal["SET_PROCESSING_TIME"] = "SET_PROCESSING_TIME"
    op_id: str
    processing: Dist


class AddMachine(BaseModel):
    model_config = F
    kind: Literal["ADD_MACHINE"] = "ADD_MACHINE"
    machine: Machine
    eligible_for: tuple[str, ...]  # op ids


class SetBufferCapacity(BaseModel):
    model_config = F
    kind: Literal["SET_BUFFER_CAPACITY"] = "SET_BUFFER_CAPACITY"
    buffer_id: str
    capacity: int | Literal["UNBOUNDED"]


class AddPlannedDowntime(BaseModel):
    model_config = F
    kind: Literal["ADD_PLANNED_DOWNTIME"] = "ADD_PLANNED_DOWNTIME"
    machine_id: str
    interval: Interval


class AddBreakdown(BaseModel):
    model_config = F
    kind: Literal["ADD_BREAKDOWN"] = "ADD_BREAKDOWN"
    breakdown: Breakdown


class RemoveBreakdowns(BaseModel):
    model_config = F
    kind: Literal["REMOVE_BREAKDOWNS"] = "REMOVE_BREAKDOWNS"
    machine_id: str


class SetDispatchPolicy(BaseModel):
    model_config = F
    kind: Literal["SET_DISPATCH_POLICY"] = "SET_DISPATCH_POLICY"
    dispatch: DispatchPolicy


class SetOrderQuantity(BaseModel):
    model_config = F
    kind: Literal["SET_ORDER_QUANTITY"] = "SET_ORDER_QUANTITY"
    order_id: str
    quantity: int


class SetCalendar(BaseModel):
    model_config = F
    kind: Literal["SET_CALENDAR"] = "SET_CALENDAR"
    calendar: Calendar  # replaces the calendar with the same id


Change = Annotated[
    SetProcessingTime
    | AddMachine
    | SetBufferCapacity
    | AddPlannedDowntime
    | AddBreakdown
    | RemoveBreakdowns
    | SetDispatchPolicy
    | SetOrderQuantity
    | SetCalendar,
    Field(discriminator="kind"),
]


class Scenario(BaseModel):
    model_config = F
    scenario_id: str
    description: str = ""
    baseline_model_sha256: str
    changes: tuple[Change, ...] = Field(min_length=1, max_length=100)
