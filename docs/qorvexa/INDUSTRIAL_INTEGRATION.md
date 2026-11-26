# Industrial Integration (Member 6) — Step 2: Telemetry normalization

Package: `backend/src/qorvexa/connectors/telemetry/` · tests: `backend/tests/test_telemetry_normalization.py` (55 cases).
Pure Python, **no network I/O, no new dependencies**, no existing file modified. Real OPC UA / MQTT / historian connectors are a later step and will feed this layer.

`RawMeasurement (loose types, from a connector) -> TelemetryProcessor -> TelemetryRecord (typed, UTC, quality-flagged)`

## Record
`factory_id, equipment_id, tag_id, value (float|None), unit, timestamp (UTC|None), ingested_at, source, source_id, quality, quality_reasons, raw_value_repr, raw_unit, conversion_rule, source_quality_raw, out_of_order, synthetic`.

## Rules
| Concern | Behaviour |
|---|---|
| Missing value | `value=None`, quality `MISSING`. Never substituted or interpolated. |
| NaN/inf, strings, booleans | `value=None`, quality `INVALID` (`VALUE_NOT_FINITE` / `VALUE_NOT_NUMERIC`). No silent coercion of `"75.2"`. |
| Timestamps | ISO-8601 / aware datetime -> UTC. **Naive** timestamps and **numeric epochs** are refused unless `naive_timezone` / `epoch_unit` is configured. Future beyond `max_future_skew_s` -> `INVALID`. |
| Staleness | Age > `max_age_s` -> `STALE` (value kept, flagged). `max_age_s=None` for historian backfill. `stale_series()` lists series with no recent sample. |
| Units | Per-tag `TagSpec(canonical_unit)`. Conversion is recorded in `conversion_rule`; unknown/cross-kind units -> `INVALID` (`UNIT_MISMATCH`), missing unit -> `UNIT_MISSING`. Without a spec the value is kept in its source unit. |
| Source quality | OPC UA StatusCode severity bits (00 Good / 01 Uncertain / 10 Bad / 11 invalid) or text flags. **Absent quality is UNCERTAIN, not GOOD** (configurable). |
| Combining | Worst of: source quality, timestamp checks, value checks, staleness (`GOOD < UNCERTAIN < STALE < BAD < MISSING < INVALID`). |
| Duplicates | Same series+timestamp+value -> `DUPLICATE` (dropped). Same timestamp, different value -> `REJECTED CONFLICTING_DUPLICATE` (first value wins, nothing overwritten). |
| Out of order | Accepted, flagged `out_of_order`. Dedupe memory is bounded (`dedupe_window`, default 1024/series, `max_series` 10,000), so a duplicate older than the window cannot be detected. |
| Identifiers | Must match `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`, else `REJECTED` (no record can be keyed). |
| Synthetic data | `source=SIMULATED` forces `synthetic=True`. |

## Open decisions for the Technical Lead
1. `DataQuality` / `TelemetrySource` are **proposed**, not yet project-wide enums.
2. The existing Units Engine (`industrial/units`) is exact-ratio only and **refuses offset units (degC/degF/K)** and has no pressure units, so telemetry has its own small table (temperature, pressure, velocity). Decide whether to extend the Units Engine instead.
3. Default `absent_quality=UNCERTAIN` for sources that send no quality flag.

## Not covered
Range/plausibility checks, unit-table extension beyond the three kinds above, persistence, and any protocol I/O.

---

# Step 3: Simulated factory + observed machine state

Packages: `backend/src/qorvexa/connectors/simulated/`, `.../telemetry/machine_state.py` · tests: `backend/tests/test_simulated_factory.py` (17 cases). No I/O, no new dependencies.

## Simulated factory (SYNTHETIC - never plant data)
`SimulatedFactory(seed, factory_id, machines, start, step_s, faults)` yields MQTT-style `Frame(topic, payload)` or flattened `RawMeasurement`s. Same seed -> identical stream. Every payload carries `"synthetic": true` and measurements use `TelemetrySource.SIMULATED`.

* **Topic convention (PROPOSED):** `qorvexa/v1/<factory_id>/<equipment_id>/telemetry`. Connectors only **subscribe**; nothing publishes control messages.
* **Payload:** `factory_id, equipment_id, timestamp (UTC ISO-8601 Z), measurements{tag:{value,unit}}, quality, synthetic`.
* **Tags:** `temperature` (C), `vibration` (mm/s), `production_count` (piece, monotonic), `machine_state` (state code).
* **Machine model:** RUNNING/IDLE/DOWN with seeded random failures and repair times; temperature relaxes toward a state-dependent target; vibration is higher when running. It is a test-signal generator, **not** a physical model and not related to the Digital Twin DES.
* **Fault injection (`FaultProfile`, per-frame probabilities):** `gap_rate` (frame never sent), `missing_value_rate` (null), `malformed_value_rate` (`"N/A"`), `bad_quality_rate`, `duplicate_rate`, `out_of_order_rate` (delivered one tick late).

## Observed machine state
`derive_machine_state(factory_id, equipment_id, record)` -> `MachineStateSnapshot(state, state_timestamp, last_update, source, quality, reasons, synthetic)`.
`MachineState`: IDLE, RUNNING, BLOCKED, STARVED, SETUP, MAINTENANCE, DOWN, UNKNOWN. Wire encoding `StateCode` 0..6 (PROPOSED).

* GOOD or UNCERTAIN data -> the reported state (quality is carried along).
* Missing, stale, BAD, INVALID, unrecognized or fractional code, or no observation at all -> **`UNKNOWN`, never `DOWN`**.
* This is the *observed* state; it is separate from the DES micro-states in `industrial/digital_twin`.

## Next
MQTT connector (subscribe-only) consuming these frames, then OPC UA; both need new dependencies (`paho-mqtt`, `asyncua`) and a `uv.lock` change that the Technical Lead must approve.

---

# Step 4: Read-only MQTT ingestion (transport-agnostic)

Packages: `backend/src/qorvexa/connectors/mqtt/`, `connectors/readonly.py`, `connectors/connector_health.py`, `connectors/simulated/transport.py` · tests: `backend/tests/test_mqtt_ingest.py` (39 cases).

**No MQTT client library is imported and no socket is opened.** The connector runs on a small `MqttTransport` protocol (`connect`, `subscribe`, `poll`, `close`) that has **no publish method**. A real adapter (e.g. around Eclipse Paho) will implement only those four operations once the dependency is approved. Everything below is verified against `SimulatedMqttTransport`, **not a real broker**.

| Concern | Behaviour |
|---|---|
| Read-only | `ensure_read_only()` refuses any transport/client exposing `publish`, `write*`, `set_value*`, `call_method`, `send`, `command`, ... (checked at construction). |
| Subscriptions | Only `qorvexa/v1/<allowed factory>/(+\|<equipment>)/telemetry`. `#`, `$SYS`, wildcard factory, other suffixes and non-allowlisted factories raise at construction. |
| Parsing | Topic must match the convention; payload <= 64 KiB, UTF-8 JSON object without `NaN`/`Infinity`; `factory_id`/`equipment_id` in the body must equal the topic's (anti-spoof); <= 200 tags; tag IDs and units validated. Failures return a category and never reach the sink. |
| Normalization | Each tag becomes a `RawMeasurement` -> `TelemetryProcessor` (quality, UTC time, dedupe, out-of-order, units). |
| Reconnect | Bounded exponential backoff with jitter (`BackoffPolicy`, default 1 s x2 up to 60 s, 8 attempts). After the limit the connector is `FAILED` and stops trying until `reset()`. `step()` is non-blocking and driven by an injected clock. |
| Health | `ConnectorHealth` (state, last connect / message, error category, counters, `synthetic`). `as_check()` maps to the observability model: CONNECTED -> UP (DEGRADED if no data for `stale_after_s`), DISCONNECTED -> DEGRADED, FAILED -> DOWN, INIT -> UNKNOWN. Register connectors as **OPTIONAL** dependencies so an outage degrades but does not unready the platform. |
| Metrics | `qorvexa_connector_messages_total{connector,outcome}`, `qorvexa_connector_connection_failures_total{connector}`, `qorvexa_connector_up{connector}`. Labels are bounded (no tag or equipment values). |
| Logs | Structured events carry `component`, `event_type`, `connector_id`, `error_category` only; payload contents are never logged. |

## Not done / needs approval
* Real Paho adapter: needs `paho-mqtt` and a `uv.lock` change (Technical Lead approval). TLS (`mqtts`), authentication and certificate handling belong in that adapter and must read secrets from the environment, never from source.
* Not tested against a real broker (QoS semantics, retained messages, session persistence).
* Topic convention and QoS defaults (1) are PROPOSED.
* Connector health is not yet registered in any API route (`api/` is a shared file).

---

# Step 5: Read-only OPC UA ingestion (transport-agnostic)

Packages: `backend/src/qorvexa/connectors/opcua/`, `connectors/simulated/opcua_transport.py` · tests: `backend/tests/test_opcua_readonly.py` (29 cases).

**No OPC UA client library is imported and no socket is opened.** The connector runs on an `OpcUaTransport` protocol with `connect`, `close`, `namespaces`, `browse`, `read`, `create_subscription`, `poll_changes`, `delete_subscription`. There is **no write, method-call, node-management or history-update operation**. A real adapter (e.g. around `asyncua`) will implement only these, ideally against a server-side read-only account. Everything here is verified against `SimulatedOpcUaTransport`, **not a real OPC UA server**.

| Concern | Behaviour |
|---|---|
| Read-only | `ensure_read_only()` refuses a client exposing `write_value`, `call_method`, `add_nodes`, ... at construction. A test wraps the transport in a recorder and proves the connector touches only the eight protocol operations. |
| Approved nodes | `ApprovedNode(node_id, factory_id, equipment_id, tag_id, unit)`. Only these are read or subscribed. A reading the server returns for any other node is dropped and counted as `unapproved_node`. |
| Discovery | `discover()` browses from `i=85` (bounded depth/size) and reports namespaces, approved nodes found / missing, and a **count** of unapproved variables seen; their identities are never exposed. |
| Modes | `poll` (read all approved nodes each step) or `subscribe` (data-change only; the subscription is re-created after every reconnect and deleted on `close()`). |
| Quality | StatusCode severity -> `DataQuality` through the shared mapper. A BAD status never carries a value. If the source timestamp is missing the server timestamp is used but quality is capped at UNCERTAIN (a server time is not the measurement time). |
| Node errors | Unknown / access-denied / no-data / communication / other-bad are counted per category (`qorvexa_opcua_node_errors_total{connector,category}`) and still produce a record with `value=None`, never a substituted value. |
| Staleness | An unchanged value with an unchanged source timestamp is a `DUPLICATE`; `TelemetryProcessor.stale_series()` then reports the series, so a hung source (server up, data frozen) is visible even though the connection is healthy. |
| Reconnect / health / metrics | Same bounded backoff, `FAILED` state and `reset()`, `ConnectorHealth` and `qorvexa_connector_*` metrics as the MQTT connector. Register as an OPTIONAL dependency. |
| Config | `OpcUaConnectionConfig` validates the endpoint with the existing `validate_edge_endpoint` (opc.tcp only, no URL credentials, host allowlist, no private/loopback/metadata addresses). Credentials and certificate paths are referenced by environment-variable **name** and resolved at connect time; the config holds no secret, and there is no unencrypted `none` security mode. |

## Known limits / open decisions
* Not tested against a real server: real session/subscription semantics, certificate trust, publishing intervals and keep-alives are untested.
* The named StatusCodes in `status.py` should be checked against the OPC UA reference before use against a real server.
* `validate_edge_endpoint` rejects private and loopback addresses, so a lab or plant server on a private network cannot be configured through it today. That is an existing, deliberate policy; the Technical Lead must decide how approved private endpoints are allowed.
* The reconnect/health logic is duplicated between the MQTT and OPC UA connectors (about 50 lines); extract a shared base once a third connector (historian) needs it.
* The real adapter needs `asyncua` and a `uv.lock` change (Technical Lead approval).
* Simulator fix in this step: `SimulatedFactory.machine_ids` was added, and the simulated OPC UA server's time advances only with reads (not on connect).
