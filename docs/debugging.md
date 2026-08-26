# Local diagnostic mode

Diagnostic mode is an opt-in, local-only engineering trace for correlating an
operator action with controller state and BLE traffic. It does not contact a
server, upload a file, enable unknown commands, relax an interlock, or change
the application's timeout/retry policy.

## Starting a trace

Use the virtual-environment launcher with `--debug` for simulation and
development:

```sh
.venv/bin/hwcdq-control --simulate --debug
```

Do not use `.venv/bin/hwcdq-control --debug` for live CoreBluetooth. The
development Homebrew `Python.app` has no
`NSBluetoothAlwaysUsageDescription`, so macOS TCC terminated the first scan
immediately after `scan_started` and before any advertisement was logged. Build
and verify the application that owns its Bluetooth privacy metadata:

```sh
.venv/bin/python -m pip install --no-deps -e packages/hwcdq-client
.venv/bin/python -m pip install -e '.[macos-app]'
.venv/bin/python tools/build_macos_app.py build
.venv/bin/python tools/build_macos_app.py verify
```

For the first live monitoring trace, launch that bundle through LaunchServices
with the exact absolute paths below and no output-control flag:

```sh
open -n "$PWD/dist/Pidzoom Portable charger HW178P.app" \
  --args --debug \
  --debug-log "$PWD/logs/charger-live.jsonl" \
  --scan-seconds 8
```

Choose **Allow** at the first macOS Bluetooth prompt, then press
**Сканировать** once. The bundle is a local arm64, ad-hoc-signed current-machine
build for macOS 26.0 or later, not a notarized or distributable release. It is
verified on macOS 26.5.2. Passing its offline verifier does not confirm live
permission or radio access; the missing-usage-description fix is live-confirmed
only when this exact bundle scan no longer aborts.

The direct development launcher defaults to `logs/hwcdq-debug.jsonl` beneath
its current working directory. The frozen bundle defaults to the intentionally
retained legacy path
`~/Library/Logs/HWCDQ Bench Control/hwcdq-debug.jsonl`, so it does not depend on
LaunchServices' working directory and existing traces remain continuous across
the visible product rename. The path is not the current display name. The
direct launcher prints the resolved absolute path to stderr. The window shows a
DEBUG badge containing the filename; hover it for the full path or an error.
Without `--debug`, the logger is a no-op and creates no file. For the first
controlled live run, still supply the documented absolute `--debug-log` path so
the evidence location is unambiguous.

Choose a destination with `--debug-log`, which requires `--debug`. This direct
example is for simulation/development only:

```sh
.venv/bin/hwcdq-control --simulate --debug \
  --debug-log logs/charger-session.jsonl
```

On POSIX systems, use a dedicated private parent directory. A missing directory
is created with mode `0700`; an existing direct parent is accepted only when it
is mode `0700`. The active file and backups are forced to mode `0600`.

## Storage and failure behavior

- Records are JSON Lines: one complete UTF-8 JSON object per line, flushed
  before `emit` returns.
- A pre-existing regular file is appended to. If its final byte is not a
  newline, the logger inserts one before the next record.
- Rotation occurs at 5 MiB. The active file is
  `hwcdq-debug.jsonl`; three backups are retained as `.1`, `.2`, and `.3`, with
  `.1` the newest rotated file.
- Event sequence numbers are logger-instance-local, restart on each launch, and
  continue across rotation. For physical file order, read `.3`, `.2`, `.1`,
  then the active file. Use `application_started`, timestamps, process fields,
  and `sequence` to separate an earlier run already present in an appended log.
- The sink is protected by one lock, so Qt and asyncio worker-thread records do
  not interleave within a JSON line.
- Any symlink in the destination path, a non-regular target, an unsafe direct
  parent mode, a rotation collision with an unsafe target, or an I/O/encoding
  failure fails closed by disabling further diagnostic writes. It never changes
  BLE sequencing, deadlines, connection teardown, or output controls.
- A disabled sink is visible as a DEBUG error badge and reported on stderr at
  startup. Treat the resulting trace as incomplete.

There is no network sink or automatic upload. A diagnostic trace can contain
the charger/CoreBluetooth identifier, advertised name and services, RSSI,
GATT UUIDs/properties, firmware/serial bytes, configuration, telemetry,
non-password raw application frames, timing, and error messages. Keep it as
sensitive local engineering evidence and review it before sharing.

## JSONL schema

Schema version 1 has these fields on every line:

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | integer | Currently `1`. |
| `timestamp_utc` | string | UTC ISO-8601 wall-clock timestamp with milliseconds. |
| `monotonic_ns` | integer | Local monotonic timestamp for interval/order analysis. |
| `sequence` | integer | Strictly increasing number from this logger instance. |
| `process` | object | Process `pid` and executable `name`. |
| `thread` | object | Emitting thread `id` and `name`. |
| `category` | string | Stable subsystem namespace from the inventory below. |
| `event` | string | Stable semantic event name. |
| `details` | object | Event-specific, recursively sanitized values. |

Example with representative, non-secret values:

```json
{"schema_version":1,"timestamp_utc":"2026-08-25T12:34:56.789Z","monotonic_ns":123456789,"sequence":42,"process":{"pid":1234,"name":"hwcdq-control"},"thread":{"id":5678,"name":"hwcdq-asyncio"},"category":"ble.serializer","event":"transaction_acquired","details":{"opcode":7,"transaction_id":"tx-000007","operation":"set_voltage","priority":10,"queue_wait_ms":1.25,"response_timeout_seconds":8.0,"native_write_timeout_seconds":3.0}}
```

Wall time is useful for correlation with external capture; monotonic timestamps
and transaction IDs are authoritative for local durations. Queue wait is
reported separately. Each request gets a three-second native-write phase; only
after all chunks complete does its fresh eight-second application-response
deadline begin. A mutation ACK and verifying readback are distinct exchanges,
each with independent phase deadlines.

## Event inventory

The inventory is deliberately semantic. Detail fields may grow while category
and event names remain the stable routing keys.

| Category | Events | What they establish |
|---|---|---|
| `app.lifecycle` | `application_started`, `window_shown`, `startup_failed`, `shutdown_started`, `shutdown_finished` | Process flags/mode and the outer application lifetime. |
| `controller.worker` | `worker_ready`, `event_loop_started`, `coroutine_submitted`, `worker_shutdown_started`, `worker_shutdown_finished`, `event_loop_stopped` | Asyncio worker/thread lifetime and submitted work. |
| `controller.lifecycle` | `controller_initialized`, `shutdown_started`, `shutdown_ignored`, `shutdown_finished` | Controller construction and idempotent shutdown. |
| `controller.mode` | `mode_announced` | Simulation, monitoring, or control mode and whether output controls were enabled. |
| `controller.action` | `scan_submitted`, `scan_ignored`, `scan_stop_submitted`, `scan_stop_ignored`, `connect_submitted`, `disconnect_submitted`, `refresh_submitted`, `set_voltage_submitted`, `set_current_submitted`, `start_submitted`, `stop_submitted`, `stop_ignored` | Public actions accepted or ignored by the controller. Committed or Start-confirmed numeric V/I values are included; passwords are not. |
| `controller.operation` | `operation_registered`, `operation_finished`, `operation_state_changed` | Asynchronous operation lifetime, outcome, busy state, and sanitized error. |
| `controller.validation` | `action_rejected_locally` | A command rejected before BLE because a local precondition failed. |
| `controller.session` | `session_created`, `existing_session_disconnect_started`, `manual_connect_waiting_for_old_client`, `disconnect_no_session`, `session_event_received`, `stale_session_event_discarded` | Session ownership, manual-connect waiting for old native work, forwarding of sanitized backend events, and suppression of callbacks from a replaced session. |
| `controller.reconnect` | `reconnect_scheduled`, `reconnect_backoff_started`, `reconnect_attempt_failed`, `reconnect_waiting_for_old_client`, `reconnect_succeeded`, `reconnect_exhausted` | Read-only recovery intent, 1/2/5-second attempt number, outcome, no-mutation-replay flag, and waiting to create a new native client until the old one is fully finished. |
| `ble.scan` | `scan_started`, `advertisement_observed`, `scan_cancelled`, `scan_failed`, `scan_finished` | Scan duration, advertisements, identifiers, names, RSSI/services, result count, cancellation, and failure. |
| `ble.lifecycle` | `connect_started`, `transport_connected`, `session_ready`, `connect_cancelled`, `connect_failed`, `disconnect_started`, `disconnect_finished`, `transport_disconnected_callback`, `stale_transport_disconnect_discarded`, `poisoned_client_teardown_started`, `poisoned_client_teardown_finished` | Native link/session state, callback generation, cancellation, disconnect origin, and post-invalidation teardown. |
| `ble.gatt` | `services_discovered`, `topology_selected`, `notifications_enabled` | The full discovered hierarchy/properties, exact selected parent and sibling RX/TX characteristics, properties-derived write mode, WNR chunk size, and notification setup. |
| `ble.authentication` | `authentication_accepted` | Authentication outcome without credential material. Its opcode-`0x02` details are collapsed by the sink. |
| `ble.serializer` | `transaction_queued`, `transaction_queue_cancelled`, `transaction_queue_failed`, `transaction_acquired`, `transaction_cancelled`, `transaction_failed`, `transaction_completed`, `transaction_released` | Priority, queue wait, unique transaction ID, slot occupancy, response/native timeout policy, and release. |
| `ble.packet` | `request_started`, `tx_frame_prepared`, `response_wait_started`, `response_matched`, `request_failed`, `request_finished`, `rx_frame_decoded`, `unexpected_response_opcode` | Opcode-level request/response correlation, non-secret frame/checksum, write mode/chunk count, response matching, and failure. |
| `ble.write` | `chunk_write_started`, `chunk_write_completed`, `native_write_failed`, `native_write_cancelled` | TX characteristic, response mode, chunk index/count, safe raw bytes, immediate native failure, and cancellation. |
| `ble.timeout` | `deadline_expired_between_chunks`, `native_write_deadline_expired`, `response_deadline_expired` | Exact timeout phase, whether transmission was attempted, and reconnect/no-replay decision. A response timeout explicitly marks the sequence-less stream desynchronized. |
| `ble.notification` | `fragment_received`, `stale_fragment_discarded` | RX characteristic, raw safe fragment, fragment length, pre-feed buffer length, and old-generation fragments discarded after invalidation/reconnect. |
| `ble.framing` | `fragment_processed`, `frame_reassembly_failed` | Reassembly progress, completed frame count, buffered bytes, and framing reset/error. A successfully decoded frame records `checksum_valid: true` under `ble.packet`; a checksum/framing failure is recorded here and through error/session events. |
| `ble.readback` | `configuration_updated`, `telemetry_updated`, `mutation_evaluated`, `mutation_outcome_resolved` | Parsed values, ACK plus readback comparison, and resolution of an earlier unknown outcome. |
| `ble.safety` | `mutation_outcome_ambiguous`, `client_poisoned` | An unconfirmed state-changing result or a client that can no longer be used safely. |
| `session.state` | `state_changed` | State transition, physical transport status, and authentication status. |
| `session.event` | `state`, `tx`, `rx`, `info`, `warning`, `error`, `data` | The redacted event stream also shown to the UI/controller. |
| `ui` | `window_opened`, `window_closing`, `shutdown_scan_requested`, `window_closed`, `mode_rendered`, `state_rendered`, `device_list_rendered`, `device_selected`, `last_device_remembered`, `tab_selected`, `button_clicked`, `shortcut_triggered`, `action_blocked`, `dialog_opened`, `dialog_submitted`, `dialog_rejected`, `dialog_closed`, `command_submitted`, `safe_load_acknowledgement_changed`, `operation_rendered`, `packet_row_appended`, `packet_row_selected`, `clipboard_copy_completed`, `export_completed`, `export_failed` | Semantic operator actions and rendered state. `last_device_remembered` contains only the CoreBluetooth identifier; no password/credential, serial, name, or telemetry is persisted. No raw keys, edit history, clipboard contents, or secret-derived metadata. |

For a state-changing command, a normal correlation chain is:

```text
ui command_submitted
  -> controller.action *_submitted
  -> ble.serializer transaction_queued/acquired
  -> ble.packet request_started/tx_frame_prepared
  -> ble.write chunk_write_started/chunk_write_completed
  -> ble.notification fragment_received
  -> ble.framing fragment_processed
  -> ble.packet rx_frame_decoded/response_matched
  -> ble.readback mutation_evaluated
  -> ble.serializer transaction_completed/released
  -> controller.operation operation_finished
```

A timeout, cancellation, disconnect, framing error, or mismatched opcode
branches into the corresponding `ble.timeout`, `ble.safety`, `ble.lifecycle`,
`ble.framing`, `session.event`, and `controller.operation` records.

## Input and credential privacy boundary

The diagnostic mode is **not** a keylogger. It records button/menu actions and
the named shortcuts F5 and Ctrl+Shift+`.` as semantic events only. It never
records:

- raw key-down/key-up/keypress events or printable characters;
- password-field focus, entered text, edit history, or password length;
- clipboard contents (only that an explicit redacted copy completed);
- a credential packet, payload, length, checksum, BLE chunk, notification
  fragment, plaintext, MD5-hex derivative, decoded/transformed form, or
  exception rendering containing the credential.

The controller registers the transient plaintext with the sanitizer before it
submits the async connection. `Credential` immediately derives its
32-character wire value and retains no plaintext; each session registers that
derived value before emitting its own lifecycle or packet diagnostics. The UI
emits only a typed authentication-dialog action with no credential-derived
fields, and the session omits credential frames/chunks before
they enter its ordinary event pipeline. The sink then recursively redacts
sensitive-key fields and common literal, UTF-8, hex, escaped, and bytes
renderings as a second defense. It also rejects raw key/text event names. More
importantly, every event whose root `details.opcode` is credential opcode `0x02`
is collapsed at the final sink to exactly:

```json
{"opcode":2,"redacted":"[REDACTED]"}
```

This applies to TX, RX, chunks, fragments, framing, checksums, decoded objects,
and nested packet representations even if a caller accidentally supplies them.
Do not infer password correctness, credential case, format, or length from the
diagnostic file.

## Controlled live-session collection

No charger was contacted during the initial implementation. A first attempt
using the direct Homebrew Python launcher was terminated by macOS TCC on Scan.
A packaged run then confirmed the sampled FFE1 parent with sibling FFE2/FFE3;
its old FFE0-only selector stopped before application traffic. Corrected
monitoring PID 67357 subsequently completed FFE2 notification setup,
authentication, firmware/serial/configuration, and repeated telemetry. It used
acknowledged FFE3 writes and ended on a missing native write callback; the
rebuilt WNR-preferred policy has not yet completed its bounded hardware soak.
See
[`../evidence/live-gatt-2026-08-25.md`](../evidence/live-gatt-2026-08-25.md).
Validate the updated logging path with the simulator, rebuild/verify the
packaged `.app`, then continue only the bounded monitoring sequence in
[`live-testing.md`](live-testing.md).

1. Start `.venv/bin/hwcdq-control --simulate --debug`; leave the password field
   empty to select the APK fallback credential, refresh, and quit normally.
2. Verify the DEBUG badge stayed healthy, every JSONL line parses, sequence
   numbers/order are coherent across backups, and fallback credential
   `D41D8CD98F00B204E9800998ECF8427E`, its lowercase form, and its ASCII hex
   rendering are absent.
3. Install the `macos-app` extra, then run
   `.venv/bin/python tools/build_macos_app.py build` and
   `.venv/bin/python tools/build_macos_app.py verify`. The verifier checks
   `Info.plist`, both Bluetooth purpose keys, bundle/signing identifiers,
   deep/strict ad-hoc signing without a Team ID, every bundled Mach-O's thin
   arm64 architecture and macOS 26.0 deployment compatibility, PyInstaller
   warnings, frozen version and import-only CoreBluetooth modules, the Qt Cocoa
   plugin, and an offscreen simulated `window_shown`. It never starts a BLE
   scan.
4. With the charger/output in the operator's independently established safe
   state, launch the `.app` using the exact absolute `open -n ... --args`
   monitoring command shown above, **without** output controls. Accept the
   macOS Bluetooth prompt, then scan once.
   Monitoring may send only authentication and documented reads. Any `0x07`,
   `0x08`, or `0x0C` TX is a safety failure: close that build and preserve the
   trace rather than continuing.
5. Select the known charger and connect once. For this password-unknown run,
   leave the authentication field empty and submit it exactly once to select
   the APK fallback credential. If it is rejected, the response is malformed,
   or the request times out, disconnect/quit without trying another value. On
   success, inspect GATT, then perform only the documented 10-minute read-only
   WNR soak. Do not send unknown opcodes.
6. Preserve the active file and `.1`–`.3` backups together with the exported
   redacted packet journal. Record whether a DEBUG error appeared.
7. Only after monitoring is repeatable and decoded limits/telemetry are
   credible may a separate LaunchServices run of the same bundle use
   `--enable-output-controls --debug`, following the one-change-at-a-time
   setpoint and Start/Stop phases in `live-testing.md`.

If `native_write_failed`, `native_write_deadline_expired`,
`native_write_cancelled`, `response_deadline_expired`, or `client_poisoned`
appears, no later application write is permitted on that BLE client—even Stop.
The client generation is invalidated and torn down best-effort. Read-only
maintenance may visibly create a clean client after 1/2/5-second backoff; a
mutation is never replayed or used to trigger that continuation. Do not use an
apparent late notification as permission to reuse the invalidated client.

Simulator evidence confirms the diagnostic pipeline and application state
machine only. Offline bundle verification confirms package structure, signing,
frozen imports, and simulated startup only. The packaged live run separately
confirms TCC access, scan, FFE1 service layout, FFE2 RX notification,
FFE3 TX, credential ACK, identity/configuration, and telemetry. Long-run WNR
durability, the new maintenance/reconnect policy on hardware, all
state-changing ACK/readback, and electrical behavior remain unverified until
their controlled stages are completed.
