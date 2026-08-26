# hwcdq-client

`hwcdq-client` is the canonical Qt-free Python library for the Pidzoom
Portable Charger HW178P. It contains the recovered HWCDQ packet codec, an
asynchronous session with explicit safety interlocks, transport protocols, and
deterministic fakes for tests.

The base install uses only the Python standard library. Install the optional
Bleak adapter when a real Bluetooth transport is required:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --no-deps -e packages/hwcdq-client
.venv/bin/python -m pip install -e 'packages/hwcdq-client[bleak]'
```

The platform BLE identifier is opaque. On macOS it is normally a CoreBluetooth
UUID rather than the charger's MAC address.

```python
from hwcdq import AccessMode, ChargerSession, Credential, DeviceTarget
from hwcdq.bleak import BleakTransport

session = ChargerSession(BleakTransport(), access=AccessMode.MONITOR_ONLY)
snapshot = await session.connect(
    DeviceTarget("platform-specific-identifier"),
    Credential.apk_fallback(),
)
print(snapshot.telemetry)
await session.disconnect()
```

Control mode is an explicit process-level opt-in. Mutating methods additionally
enforce fresh telemetry, fresh device configuration, the HW178P model envelope
of 50–178 V and 0.01–14 A, and readback confirmation. The library never
retries an ambiguous mutating write.

`AsyncGattTransport` and `AsyncScanner` make the core reusable with another
Bluetooth implementation. `hwcdq.testing` provides deterministic in-process
fakes without Bluetooth or Qt.

Native iOS and Android codecs live under `native/swift` and
`native/kotlin/hwcdq-protocol`; they consume the same language-neutral
`contract/v1` vectors but do not provide platform BLE sessions. See the root
[`README.md`](../../README.md) and [`docs/library.md`](../../docs/library.md)
for the complete project map.
