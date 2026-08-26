# Pidzoom Portable Charger HW178P — protocol, libraries, and desktop workbench

`hwcdq-protocol` is a small monorepo for independent interoperability with the
**Pidzoom Portable Charger HW178P** (the HWCDQ BLE protocol). It contains the
wire specification, reusable client libraries for several languages, and an
optional Qt desktop workbench for testing the implementation against a charger
or a deterministic simulator.

This is an unofficial project. Pidzoom, HWCDQ, HW Smart Charger, and HW178P
are names or marks of their respective owners. This repository is not
affiliated with, endorsed by, or supported by the charger manufacturer.

## What should I use?

The repository has one protocol contract and several consumers. Pick the layer
that matches the application you are building:

| You are building | Use | What it provides |
| --- | --- | --- |
| A Python, Home Assistant, or automation integration | [`hwcdq-client`](packages/hwcdq-client) | Qt-free Python package, optional Bleak BLE transport, authenticated session, telemetry, and safety-gated control |
| A macOS diagnostic UI or local test bench | [`hwcdq-control`](src/hwcdq_control) | The Qt desktop app/CLI that imports `hwcdq-client`; supports simulation and controlled BLE diagnostics |
| An iOS app | [`native/swift`](native/swift) | Swift codec and framing library; add CoreBluetooth transport/session code in the host app |
| An Android app | [`native/kotlin/hwcdq-protocol`](native/kotlin/hwcdq-protocol) | Kotlin/JVM codec and framing library; add Android BLE transport/session code in the host app |
| Another implementation or a protocol audit | [`contract/v1`](contract/v1) and [`docs/`](docs) | Language-neutral vectors, packet layouts, confidence labels, and evidence |

`hwcdq-client` and `hwcdq-control` are separate Python distributions that live
in this same repository. The desktop workbench is not a second protocol
implementation: it imports the canonical `hwcdq` package.

## Status and scope

The protocol was reconstructed from the Android application and validated with
one owner-operated HW178P unit. Treat the evidence labels in
[`docs/commands.md`](docs/commands.md) and [`contract/v1`](contract/v1) as
part of the API:

| Area | Current status |
| --- | --- |
| GATT service `FFE1`, notify `FFE2`, write `FFE3` | Live-confirmed on one unit |
| Authentication, firmware, serial, configuration, telemetry | Live-confirmed on one unit |
| Python monitoring session | Implemented and tested with deterministic fakes |
| Start/stop and setpoint packet layouts | Reconstructed; live control coverage is intentionally limited |
| Swift and Kotlin packages | Codec/framing only; no platform BLE session |
| Other Pidzoom models and firmware revisions | Unknown |

The recovered application credential is an application-layer bearer value. It
is not a claim that the charger has no password or that BLE pairing is absent.
Anyone with Bluetooth access and the credential may be able to control output.
Do not publish diagnostic logs or transmit guessed credentials.

This software can energize a high-voltage power converter. Read the safety
interlocks and live-testing procedure before enabling control. Monitoring also
sends authentication and read-only query frames; it is not a passive radio
sniffer.

## Repository layout

| Path | Purpose | Runtime boundary |
| --- | --- | --- |
| [`packages/hwcdq-client`](packages/hwcdq-client) | Canonical Qt-free Python package | Async BLE session, codec, profile, fakes |
| [`native/swift`](native/swift) | `HWCDQProtocol` Swift Package | Foundation codec/framing; iOS 13/macOS 12; no CoreBluetooth session |
| [`native/kotlin/hwcdq-protocol`](native/kotlin/hwcdq-protocol) | Kotlin/JVM library | Codec/framing; no Android BLE session |
| [`contract/v1`](contract/v1) | Versioned language-neutral contract | Wire facts, confidence, profiles, vectors |
| [`src/hwcdq_control`](src/hwcdq_control) | Optional desktop workbench | Qt/macOS UI and CLI around the Python library |
| [`docs/protocol.md`](docs/protocol.md) | Human-readable protocol specification | Framing, fields, telemetry, CoreBluetooth notes |
| [`docs/commands.md`](docs/commands.md) | Opcode table | Direction, payload, response, confidence |
| [`docs/cross-platform.md`](docs/cross-platform.md) | Cross-language contract | Python, Swift, and Kotlin boundaries |
| [`evidence/manifest.md`](evidence/manifest.md) | Provenance ledger | APK hashes, offsets, toolchain, evidence status |

The Python package is the reusable library. The desktop application is a
diagnostic client, not a dependency of the protocol implementation.

## Python installation from source

The project is intentionally source-first. It is not published to PyPI,
Maven Central, or a Swift package registry by this repository.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --no-deps -e packages/hwcdq-client
```

Install the optional [Bleak](https://bleak.readthedocs.io/) transport for real
Bluetooth access:

```sh
.venv/bin/python -m pip install -e 'packages/hwcdq-client[bleak]'
```

The base package uses only the Python standard library. Python 3.11 or newer
is required.

## Read-only Python quick start

The safe starting point is `AccessMode.MONITOR_ONLY`. The transport receives
an opaque platform identifier: on macOS this is normally a CoreBluetooth UUID,
not the charger's MAC address.

```python
import asyncio

from hwcdq import (
    AccessMode,
    ChargerSession,
    Credential,
    DeviceTarget,
)
from hwcdq.bleak import BleakScanner, BleakTransport


async def main() -> None:
    scanner = BleakScanner()
    devices = await scanner.scan(8.0)
    for index, device in enumerate(devices):
        print(index, device.name or "(unnamed)", device.identifier, device.rssi)

    if not devices:
        raise RuntimeError("no BLE devices found")
    selection = int(input("Select device number: "))
    selected = devices[selection]

    session = ChargerSession(
        BleakTransport(),
        access=AccessMode.MONITOR_ONLY,
    )
    try:
        snapshot = await session.connect(
            DeviceTarget(selected.identifier, advertised_name=selected.name),
            Credential.apk_fallback(),
        )
        print(snapshot.state, snapshot.firmware, snapshot.serial_number)
        print(snapshot.config)
        print(snapshot.telemetry)
        session.start_periodic_telemetry(interval=5.0, config_interval=60.0)
        await asyncio.sleep(15.0)
    finally:
        await session.disconnect()


asyncio.run(main())
```

Production UIs should require explicit device selection instead of taking the
first scan result. A coordinator that wants reconnects or periodic polling
should own that policy; `ChargerSession` owns one connection and never retries
a state-changing write.

## Public Python API

The stable high-level surface is exported from `hwcdq`:

- `ChargerSession` — serialized connect, authentication, GATT discovery,
  telemetry/config refresh, and safety-gated control.
- `AccessMode`, `SessionOptions`, `PIDZOOM_HW178P`, and `DeviceTarget` —
  explicit access and hardware identity policy.
- `Credential` — derives or stores the validated wire credential without
  retaining plaintext in the object representation.
- `AsyncGattTransport` and `AsyncScanner` — small seams for Bleak, CoreBluetooth
  bridges, Home Assistant coordinators, or deterministic test doubles.
- `encode_*`, `decode_packet`, and `verify_checksum` — protocol-level helpers
  for advanced integrations.
- `hwcdq.testing.FakeTransport` and `FakeScanner` — Bluetooth-free tests.

Control methods are intentionally guarded:

```python
from hwcdq import AccessMode, ChargerSession

session = ChargerSession(transport, access=AccessMode.CONTROL)
await session.set_voltage(134.0, operator_confirmed=True)
await session.set_current(4.0, operator_confirmed=True)
await session.start(confirmed_volts=134.0, confirmed_amps=4.0)
await session.stop()
```

The advanced example is not a recommendation to energize a charger. The
session checks fresh telemetry/configuration, the HW178P envelope of
50–178 V and 0.01–14 A, device-reported maxima, explicit operator intent,
acknowledgement, and readback. Stop is a de-energizing operation with its own
fresh-output-state check. Ambiguous mutating writes are never replayed.

## HW178P profile and observed data

The application profile currently documents target voltage `50.00…178.00 V`
and target current `0.01…14.00 A`. These are the owner-provided model envelope
and the sampled device's observed maxima, not universal limits for every
Pidzoom charger.

The client can decode or expose:

- firmware and binary serial-number bytes;
- configured target voltage/current and device maxima;
- input/output voltage, current, and derived power;
- two numbered temperature readings;
- output state, input frequency, module count, accumulated Ah, and Wh.

No confirmed state-of-charge percentage, charge timer, or percentage-based
current taper is present in the recovered protocol. Such behavior must not be
invented by an integration.

## Protocol summary

The recovered GATT profile uses service `FFE1`, with notifications on `FFE2`
and application writes on `FFE3`. The preliminary `FFE0` lead was rejected by
the live GATT table. The wire format is a framed, checksummed little-endian
protocol. There is no confirmed packet sequence number; request and response
matching is performed by the serialized client and opcode.

The complete field definitions, byte offsets, CRC/checksum algorithm, packet
fragmentation rules, CoreBluetooth notes, and test vectors are in
[`docs/protocol.md`](docs/protocol.md). The command inventory with confidence
levels is in [`docs/commands.md`](docs/commands.md). Do not transmit an opcode
marked `tentative` or `unknown` to live hardware.

## Native packages

The native packages consume the same `contract/v1` vectors as Python:

```sh
cd native/swift
swift test

cd ../kotlin/hwcdq-protocol
./gradlew test
```

Swift is a Foundation-only package suitable for an iOS CoreBluetooth adapter.
Kotlin targets the JVM/Android codec boundary. Neither package scans,
connects, stores credentials, or schedules polling; those platform policies
belong in the host application.

## Desktop workbench

The optional Qt application is useful for local simulation and controlled
diagnostics. It is not required by `hwcdq-client`:

```sh
.venv/bin/python -m pip install --no-deps -e packages/hwcdq-client
.venv/bin/python -m pip install -e .
.venv/bin/hwcdq-control --simulate
```

The UI is currently Russian. Live macOS operation requires the packaged app's
Bluetooth usage description and an Apple-silicon Mac; the bundle is local,
arm64, ad-hoc signed, unnotarized, and not a distributable release. Follow
[`docs/app.md`](docs/app.md) and [`docs/live-testing.md`](docs/live-testing.md)
for the staged procedure. Never use live output controls without reviewing the
interlocks and the evidence status.

Opt-in diagnostic logging is local only. Logs contain device identifiers,
telemetry, and non-credential frames; they must be treated as sensitive and
must not be attached to public issues. See [`docs/debugging.md`](docs/debugging.md).

## Development and verification

Install the package and run the repository checks:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/validate_contract.py --check

QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m unittest discover -s tests -v

cd native/swift && swift test
cd ../kotlin/hwcdq-protocol && ./gradlew test
```

The CI workflow is hardware-independent and does not download the APK or
upload diagnostic artifacts. A green local run proves codec and software
regressions only; it does not prove compatibility with an untested charger.

## Evidence and limitations

The evidence ledger records the source APK hash, Flutter/AOT observations,
binary offsets, live GATT topology, packet captures, and confidence for each
claim. The original APK, unpacked vendor binaries, research cache, and raw
diagnostic logs are intentionally excluded from Git.

The public evidence distinguishes:

- `confirmed` — observed on the sampled unit or reproduced by a checked
  vector;
- `high confidence` — supported by AOT analysis and consistent responses but
  not fully exercised on hardware;
- `tentative` / `unknown` — not safe to transmit.

The sampled unit is not a compatibility certification. Firmware revisions,
regional hardware, multiple-module systems, limits, and authentication policy
may differ.

## Security and responsible use

The protocol credential is sent at the application layer over the BLE link.
This project did not establish a per-device authentication or pairing model.
Do not log credentials, brute-force passwords, fuzz unknown opcodes, bypass
hardware limits, or operate a charger unattended. Report security issues
privately using [`SECURITY.md`](SECURITY.md); do not include raw captures or
device identifiers in a public issue.

## Contributing

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before changing the contract or
adding live evidence. Protocol changes require a confidence label, a vector,
and cross-language tests. Keep hardware interaction bounded, reversible, and
explicitly authorized by the device owner.

## License

The library and repository tooling are released under the [MIT License](LICENSE).
Third-party dependencies retain their own licenses. This project is an
unofficial interoperability implementation and carries no manufacturer
warranty.

## Project history

Current unreleased work is tracked in [`CHANGELOG.md`](CHANGELOG.md). Package
versions remain independently managed until a public release is deliberately
tagged.
