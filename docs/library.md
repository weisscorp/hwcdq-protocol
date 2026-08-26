# `hwcdq-client` library

The reusable implementation is an independent Python distribution under
`packages/hwcdq-client`. Its import namespace is `hwcdq`. It contains the
wire codec, framing, GATT selection, asynchronous charger session, safety
profile, diagnostics, abstract transport/scanner contracts, and deterministic
test fakes. It does not import Qt and its base installation has no third-party
runtime dependencies.

The repository-root `hwcdq-control` distribution is the desktop application.
It owns the Qt UI, command-line interface, saved desktop preferences, Bleak
worker lifecycle, and desktop reconnect policy. The application imports the
same `hwcdq` objects available to independent clients; it does not carry a
second protocol implementation.

Swift and Kotlin applications use separate idiomatic codec packages governed
by the same checked-in contract vectors. They do not replace or wrap the
Python runtime, so this package's API and the desktop/Home Assistant path stay
unchanged. See [`cross-platform.md`](cross-platform.md).

## Installation

For library development or a consumer that supplies its own transport:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --no-deps -e packages/hwcdq-client
```

Install the optional maintained Bleak adapter when the process itself needs
Bluetooth discovery and connections:

```sh
.venv/bin/python -m pip install -e 'packages/hwcdq-client[bleak]'
```

For this repository's desktop application, install the core first and the
root application second:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --no-deps -e packages/hwcdq-client
.venv/bin/python -m pip install -e .
```

For a local macOS bundle, use the root packaging extra in the second command:

```sh
.venv/bin/python -m pip install --no-deps -e packages/hwcdq-client
.venv/bin/python -m pip install -e '.[macos-app]'
.venv/bin/python tools/build_macos_app.py build
.venv/bin/python tools/build_macos_app.py verify
```

The two-step sequence is intentional while both distributions live in one
repository: it makes the reusable boundary testable without installing Qt and
lets the desktop dependency resolve to the local editable core.

## Supported API

The root `hwcdq` namespace deliberately exports the stable, typed surface:

- `DeviceTarget` preserves a platform BLE identifier exactly. On macOS this is
  normally a machine-local CoreBluetooth UUID, not a MAC address.
- `Credential.from_password`, `Credential.from_digest`, and
  `Credential.apk_fallback` validate or derive the wire credential without
  retaining plaintext or revealing the digest through `str`/`repr`.
- `PIDZOOM_HW178P`, `ChargerProfile`, and `EffectiveLimits` define the product
  envelope and fail-closed intersection with fresh device maxima.
- `ChargerSession`, `SessionOptions`, `SessionSnapshot`, and session events
  implement one serialized authenticated connection.
- `AsyncGattTransport` and `AsyncScanner` are structural async protocols for
  alternate Bluetooth backends.
- the named protocol encoders, `decode_packet`, and `verify_checksum` are safe
  imports for protocol consumers.

Low-level `encode_packet` remains available only as the advanced
`hwcdq.protocol.encode_packet` API. It accepts arbitrary opcodes and performs
framing, not command authorization or HW178P range checks. Do not expose it as
a user-facing raw-command sender.

The built-in Bleak implementation is optional and imported explicitly:

```python
from hwcdq import (
    AccessMode,
    ChargerSession,
    Credential,
    DeviceTarget,
)
from hwcdq.bleak import BleakScanner, BleakTransport
```

`hwcdq.testing` exports `FakeScanner` and `FakeTransport` for tests and offline
development. They exercise the same codec, framing, GATT, authentication, and
session logic as a real transport without touching Bluetooth.

## Session ownership

A `ChargerSession` owns one `AsyncGattTransport` on one asyncio event loop. A
consumer creates a target and credential, connects once, and explicitly
disconnects:

```python
from hwcdq import ChargerSession, Credential, DeviceTarget
from hwcdq.bleak import BleakTransport

session = ChargerSession(BleakTransport())
snapshot = await session.connect(
    DeviceTarget("platform-specific-identifier"),
    Credential.apk_fallback(),
)
snapshot = await session.refresh()
await session.disconnect()
```

The default access mode is monitoring only. `AccessMode.CONTROL` is merely the
first process-level opt-in; voltage, current, Start, and Stop still require the
session's fresh-state and readback interlocks. The canonical HW178P model range
is `50.00…178.00 V` and `0.01…14.00 A`. Each fresh opcode-`0x05` response must
also contain valid maxima, and the narrower device maximum wins. Missing,
Boolean, zero, non-finite, incomplete, or below-model device maxima invalidate
the complete effective limit set.

The library never retries an application write and never replays a mutation.
Connection attempts, disconnects, failed setup, unexpected transport loss,
and poisoned native writes invalidate the prior device generation and its
configuration/telemetry.

## Transport and scanner contracts

An `AsyncGattTransport` supplies:

- `connected`;
- `connect(identifier, disconnected_callback)` and idempotent `disconnect()`;
- `discover_gatt()`;
- `start_notify`, `stop_notify`;
- `write(characteristic_uuid, data, response=...)`.

An `AsyncScanner` supplies `scan(duration, callback=None)`. Duration is a
positive finite number, cancellation belongs to the caller, and identifiers
are opaque strings. A scanner does not connect automatically.

`hwcdq.bleak` implements both contracts when the `[bleak]` extra is installed.
Importing any non-optional `hwcdq` module neither imports Bleak nor accesses
Bluetooth.

## Compatibility modules

The historical `tools.hwcdq_protocol`, `hwcdq_control.backend`,
`hwcdq_control.diagnostics`, and `hwcdq_control.bleak_transport` paths are
deprecated identity shims. They import the canonical objects; they are not
independent implementations. New code must import `hwcdq`,
`hwcdq.protocol`, `hwcdq.bleak`, or `hwcdq.testing` directly.

The identity shim also preserves the former session calling convention while
existing consumers migrate:

```python
from hwcdq_control.backend import ChargerSession

session = ChargerSession(
    transport,
    output_controls_enabled=False,
    request_timeout=8.0,
    notification_settle_delay=1.0,
)
snapshot = await session.connect(platform_identifier, plaintext_password)
```

These arguments are normalized immediately into `AccessMode`,
`SessionOptions`, `DeviceTarget`, and `Credential` inside the same canonical
class. Plaintext is derived before lifecycle logging and is never retained on
the session. Typed `profile`/`access`/`options` arguments cannot be mixed with
the deprecated constructor options; new integrations should use the typed API
shown above.

## Future Home Assistant integration

No Home Assistant integration or offline-device supervisor is implemented in
this library. A future HA coordinator should own:

- configured/persisted platform device identifier;
- periodic discovery while the peripheral is absent;
- bounded reconnect and backoff after read-only failures;
- periodic configuration/telemetry refresh while connected;
- entity availability and stale-state reporting.

That coordinator must create a clean session generation for every reconnect,
rerun authentication and initial reads, and never replay voltage, current,
Start, Stop, or any other mutation after an ambiguous result. These are
consumer responsibilities rather than hidden behavior inside
`ChargerSession`.

## Validation

After the two-step editable install, run the complete repository suite:

```sh
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m unittest discover -s tests -v
```

Build the standalone library wheel without resolving anything from the
network:

```sh
.venv/bin/python -m pip wheel --no-deps --no-build-isolation \
  packages/hwcdq-client -w /tmp/hwcdq-client-wheel
```
