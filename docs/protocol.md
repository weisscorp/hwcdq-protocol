# HWCDQ BLE wire protocol

This document describes the protocol implemented by Android package
`com.y.hw.charge`, version `1.7.5`. Most command construction remains an
independent static reconstruction from the preserved APK. Controlled macOS
monitoring has additionally confirmed the sampled charger's advertisement,
complete GATT table, FFE2 subscription, application credential ACK,
firmware/serial/configuration responses, repeated telemetry, and one accidental
output-control transmission. Each statement below distinguishes static AOT
evidence, live packets, synthetic test vectors, and still-missing
state-changing evidence.

The minimal interoperable path is recovered: connect, subscribe, perform the
application credential check, request configuration and telemetry, set voltage,
set current, and switch output on/off. Device-specific limits must be read from
configuration and enforced by the client before any setter is exposed.

This wire protocol is broader than one product profile. The companion desktop
application is named **Pidzoom Portable charger HW178P** and applies an HW178P
control envelope of `50.00…178.00 V` and `0.01…14.00 A`. The exact model name
and `50 V` minimum were supplied by the owner on 2026-08-25; neither is encoded
in the recovered packets. The sampled live opcode-`0x05` response independently
reports maxima of `178 V` and `14 A`. The `0.01 A` floor is conservative client
policy, not a hardware-proven minimum.

The complete executable Python reference is the Qt-free `hwcdq-client`
distribution under `packages/hwcdq-client`, imported as `hwcdq`. Named packet
encoders and decoders are available from `hwcdq` or `hwcdq.protocol`; examples
in this document use `hwcdq.protocol`. Pure native codec/framing packages for
Swift and Kotlin are under `native/`. All three implementations are governed
by the machine-readable [`../contract/v1`](../contract/v1) facts and golden
vectors; package boundaries are documented in
[`cross-platform.md`](cross-platform.md). The historical
`tools.hwcdq_protocol` path is a deprecated identity shim. Low-level
`hwcdq.protocol.encode_packet` is advanced framing machinery for evidence and
tests, not a user-facing raw-command interface and not proof that an arbitrary
opcode is safe.

## Artifact identity

- APK: `artifacts/original/hwcdq.apk` (retained locally, not tracked)
- Size: `9,460,472` bytes
- SHA-256: `c7ee52a5e348982a0a4f51b6640b325cfe03c2f4b65a02860c2198a681c9263f`
- Package/version: `com.y.hw.charge` / `1.7.5` (`versionCode=1`)
- Flutter engine revision: `55eae6864b296dd9f43b2cc7577ec256e5c32a8d`
- Flutter release: `3.22.1` (exact engine-pin match)
- Dart SDK: `3.4.1`, snapshot hash `d20a1be77c3d3c41b2a5accaee1ce549`
- Application snapshot: Android ARM64, compressed pointers, sound null safety

Full provenance, certificate fingerprint, binary hashes, tooling, and
addresses are in [`../evidence/manifest.md`](../evidence/manifest.md).

## GATT transport

All UUIDs below use the Bluetooth base UUID
`0000xxxx-0000-1000-8000-00805f9b34fb`.

| UUID | Role | Operation | Confidence |
| --- | --- | --- | --- |
| `FFE1` | HWCDQ service | parent of FFE2 and FFE3 | confirmed static and confirmed on the sampled charger |
| `FFE2` | charger → client (RX) | enable notifications; receive framed responses/telemetry; no explicit app read | confirmed static and live; properties are indicate, notify, read, write, and WNR |
| `FFE3` | client → charger (TX) | write application request frames; prefer WNR, acknowledged write fallback | role/properties confirmed static and live; WNR durability pending soak |
| `FFE0` | unrelated preliminary lead | present in AOT, but not selected by this route and absent from the sampled charger's GATT table | rejected for the sampled charger |

This is deliberately not rewritten as the common generic “FFE0 service / FFE1
UART characteristic” convention. In `_MainPageState@548070398.initState`, AOT
VA `0x3e85e0..0x3e860c` loads object-pool strings FFE1, FFE2, and FFE3 into the
three globals consumed by `Ble.open_recv` and `Ble.send`. `Ble.open_recv` at
`0x3d5f48..0x3d5f5c` calls `setNotifiable(device, FFE1, FFE2,
notification)`. `Ble.send` at `0x3d32b4..0x3d32ec` calls
`writeValue(device, FFE1, FFE3, chunk, withoutResponse)`.

The sampled charger advertised FFE1, and live discovery found exactly one FFE1
service containing sibling FFE2 and FFE3. FFE2 advertised `indicate`, `notify`,
`read`, `write`, and `write-without-response`; FFE3 advertised `write` and
`write-without-response`. CoreBluetooth reported a maximum WNR write size of
253 for both characteristics. This evidence is the untracked diagnostic log
`logs/charger-live-20260825.jsonl`, PID 73156, sequence 166; a redacted evidence
summary is preserved in [`../evidence/live-gatt-2026-08-25.md`](../evidence/live-gatt-2026-08-25.md).

The bundled Android Quick Blue bridge ultimately uses the legacy
`BluetoothGatt.writeCharacteristic` call and does not set `writeType` from the
Dart `withoutResponse` argument. Thus the application expresses WNR intent,
but the effective Android ATT write type is inherited from the discovered
characteristic/default and is not proven by this APK alone. The sampled FFE3
supports both modes. A monitoring build initially fixed `.withResponse`; in a
three-and-a-half-minute read-only session its final `0x06` write never received
CoreBluetooth's `didWrite` completion before the three-second native deadline,
although many earlier application replies were valid. The rebuilt independent
client therefore fixes `.withoutResponse` whenever FFE3 advertises WNR and
uses acknowledged `.write` only as a compatibility fallback. Never switch type
after a timeout because the first command may already have been applied. This
policy avoids the observed host acknowledgement stall; it does **not** prove
the original Android ATT type, and real-device long-run WNR durability remains
awaiting the bounded read-only soak in `live-testing.md`.

The earlier FFE0 lead is now resolved for this device in favor of FFE1. The
first desktop build had an obsolete FFE0-only selector, so it rejected the
usable live table at log sequence 173. That rejection occurred before
subscribing, authenticating, or writing any application packet. It is evidence
of an old client-policy error, not evidence that FFE1 is unusable.

Neither FFE2 nor FFE3 is used for an explicit GATT read in the recovered
HWCDQ path. Application reads are command writes to FFE3 followed by responses
on FFE2. Although the sampled FFE2 advertises both notify and indicate, the APK
asks the plugin for **notification**. CoreBluetooth uses
`setNotifyValue(true, for:)` for either property.

## Connection and handshake

The Android application performs this sequence:

1. Connect, with a 3-second operation timeout.
2. Discover services, also with a 3-second timeout.
3. Require exactly one FFE1 service with one notify-capable FFE2 and one writable
   FFE3 sibling; never pair characteristics across parents.
4. Enable updates on FFE2 and wait for the subscription callback.
5. Wait 1 second.
6. Request ATT MTU 120 on Android, then allow up to 2 seconds for it.
7. Send the application credential check (`0x02`).
8. On success, request firmware version (`0x01`), serial (`0x04`), and complete
   configuration (`0x05`).
9. Poll current values (`0x06`) while the page is active.

Evidence is the connection closure at AOT VA `0x3e8e44`: connect at
`0x3e8f40`, service discovery at `0x3e8f84`, notification setup at
`0x3e8fc4`, the 1-second delay at `0x3e8fd4`, MTU request at `0x3e9018`,
password check at `0x3e90a4`, then firmware/serial/config calls at
`0x3e9288`, `0x3e92dc`, and `0x3e9344`. `Ble.requestMtu` loads Dart Smi
`0xf0`, i.e. integer 120, at `0x3d5a2c`.

The original page's status loop is also explicit rather than unsolicited:
after a completed iteration it constructs a 500,000-microsecond delay at
`0x3eb03c..0x3eb068`, then calls the opcode-`0x06` `get_values` path at
`0x3eb0d4..0x3eb0d8`. Its receive wait uses 1,000,000 microseconds at
`0x3eb2ac..0x3eb2c8`. The independent desktop client deliberately uses a
gentler maintenance policy: wait five seconds after every completed exchange,
read telemetry in ordinary slots, and substitute opcode `0x05` when the
60-second configuration refresh is due. Operator transactions retain priority
and maintenance skips a busy serializer rather than overlapping it.

There is no BLE pairing, bonding, PIN entry, challenge, nonce, timestamp, or
packet sequence in this connection path. Opcode `0x02` carries an unsalted
MD5-derived bearer credential; it is application-layer authentication, not BLE
pairing. The Android `deviceId` (normally a MAC address) selects the locally
saved credential and plugin connection, but neither it nor the serial number is
inserted into the credential or packet.

## Application framing

Every normal control/status request and response is a self-delimiting byte
frame:

```text
offset  size  field
0       1     remaining_length = payload_length + 2
1       1     opcode
2       N     payload
2 + N   1     checksum
```

- `remaining_length` counts the opcode, payload, and checksum, but not itself.
- The minimum legal value is 2; a one-byte length limits payload to 253 bytes.
- Requests and their responses use the same opcode. There is no response-bit
  transformation such as `opcode | 0x80`.
- There is no header magic, terminator, sequence number, or packet counter.
- Multi-byte numeric payload fields are little-endian.

`recv_packet` at AOT VA `0x3d2920` first reads one byte, then exactly that many
bytes. It sums all remaining bytes except the last, compares the low eight bits
with the last byte, and returns the opcode plus payload. This gives a simple
stream reassembly rule: buffer one byte, wait until `1 + buffer[0]` bytes are
available, validate, emit the frame, and repeat.

### Checksum

The HWCDQ path does not use a CRC despite unrelated `appendCRC`,
`Crc32IsoHdlc`, and `Crc32Xz` strings elsewhere in the application.

```python
def checksum(opcode: int, payload: bytes) -> int:
    return (opcode + sum(payload)) & 0xff
```

Parameters, expressed in CRC terminology:

- family/polynomial/init/xorout/reflection: not applicable; this is an 8-bit
  additive checksum;
- covered bytes: opcode followed by the complete payload;
- excluded bytes: length and checksum itself;
- stored width/order: one byte, so endianness is not applicable.

The generic packet builder and the independent codec tests reproduce all
vectors later in this document.

## BLE fragmentation

The application builds the entire frame first. `Ble.send` divides it into
ordered chunks of at most `MTU - 3`, performs one FFE3 write per chunk, and
waits 30 ms between chunks. There is no application fragment header, fragment
number, or per-fragment checksum. Reassembly is solely by the first frame byte.

With the app's requested ATT MTU of 120, its maximum chunk size is 117 bytes.
Do not hard-code 117 on iOS: use `maximumWriteValueLength(for:)` for the
selected write type and preserve packet order.
For the recovered control requests (3 or 7 bytes), no fragmentation occurs.

No separate application-level fragment timeout was found. The reference client
uses a three-second native write deadline. Once all chunks have completed, it
starts a separate eight-second deadline for the matching application response.
On either timeout, discard the partial RX frame and invalidate the current
notification generation before reconnecting. This separation prevents slow
native completion from consuming the charger's full response allowance.

The firmware updater is an intentionally unsupported exception: after a
framed opcode-`0x00` length announcement, it sends raw firmware in logical
1024-byte blocks through the same MTU−3 splitter and waits for one raw ACK byte
per block. It then sends framed `0x28` and `0x24` zero-valued commands. This
path is documented in `commands.md` only for inventory completeness; no updater
or encoder is supplied, and none of it was executed.

## Scalar encodings

| Encoding | Size | Byte order | Use |
| --- | ---: | --- | --- |
| `uint8` | 1 | n/a | flags and compact counters |
| `uint16` | 2 | little-endian | selected configuration fields |
| `int32` bit pattern | 4 | little-endian | generic integer/bool setters |
| IEEE-754 binary32 | 4 | little-endian | voltage, current, temperatures, statistics |
| ASCII hex + NUL | 33 | byte string | opcode-`0x02` MD5 credential |
| UTF-8 + NUL | variable | byte string | generic string setters |
| fixed ASCII | fixed | byte string | two configuration string fields |

`ByteBloc.readFloat32` at `0x3d72c4` uses an ARM64 `ldr s1`; `readUint16` at
`0x3dd2f8` uses `ldrh`. The generic setter at `0x475d1c..0x475e14` emits an
integer as four bytes `(value >> 0, 8, 16, 24)`, while strings are UTF-8 plus a
zero byte. Target voltage/current callbacks first call `floatToIntBits` at
`0x43db3c`, so the four generic-setter bytes are the original binary32 bits.
There is no decimal scaling: 84.0 V is binary32 `00 00 A8 42`, not integer 840
or 8400.

## Recovered core commands

The full inventory and confidence for settings commands is in
[`commands.md`](commands.md).

### Firmware version — `0x01`

Request payload is empty:

```text
02 01 01
```

The response uses opcode `0x01`; its payload is interpreted by the app as a
firmware-version string. Treat arbitrary response bytes as device-provided
text until a live sample establishes exact termination/encoding behavior.

### Credential check — `0x02`

The wire payload is not the plaintext entered by the user. It is exactly 32
ASCII hexadecimal characters containing an MD5 digest, followed by NUL:

```text
ASCII_HEX(MD5(UTF8(plaintext_password))) || 00
```

The ordinary user-entry callback hashes the entered UTF-8 text and produces
lowercase Dart `Digest.toString()` hex. The app stores that digest text in the
per-device SharedPreferences JSON entry `password`; later handshakes send the
saved digest directly. When no saved entry exists, the exact APK fallback is
the uppercase MD5 of the empty string:

```text
D41D8CD98F00B204E9800998ECF8427E
```

Its exact complete request frame is:

```text
23 02 44 34 31 44 38 43 44 39 38 46 30 30 42 32 30 34
45 39 38 30 30 39 39 38 45 43 46 38 34 32 37 45 00 45
```

The first `23` declares 35 following bytes. The checksum input is opcode `02`
plus the 32 ASCII digest bytes and `00`; the additive checksum is `45`.

An accepted response is `03 02 01 03`; a rejected response is
`03 02 00 02`. The fallback is a static bearer credential, not proof that the
plaintext password is blank or that this charger still accepts it. If the
credential was changed, recover its saved digest from the owner's original
Android application data or obtain it from a passive opcode-`0x02` capture. Do
not brute-force it.

The literal `0000` in `libapp.so` is not evidence of a default password: its
sole target-code reference is in `_BigIntImpl.toString`, unrelated to HWCDQ
authentication. A test may explicitly configure any synthetic password, but
the default desktop simulator follows empty input to the exact APK fallback;
neither behavior makes a statement about hardware plaintext credentials.

The reference Python codec exposes two deliberately distinct levels:

```python
from hwcdq.protocol import (
    derive_password_credential,
    encode_check_password,
    encode_check_password_credential,
)

derive_password_credential(plaintext)          # empty -> exact APK fallback
encode_check_password(plaintext="")            # derive, then frame
encode_check_password_credential(hex_digest)  # validate 32 ASCII hex, then frame
```

For nonempty text, `derive_password_credential` returns lowercase UTF-8 MD5
hex. The direct credential encoder preserves valid input case because the APK
fallback is uppercase. `decode_packet` reports only
`credential_format_valid`; it never creates a decoded plaintext/credential
string field that could be rendered accidentally.

### Serial number — `0x04`

Request:

```text
02 04 04
```

The response payload is binary. The app renders each byte as two hexadecimal
digits; do not assume the payload is ASCII.

### Configuration — `0x05`

Request:

```text
02 05 05
```

The recovered parser consumes exactly 103 payload bytes in this order:

```text
f32le × 4, u8,
f32le × 6, u8,
f32le, u8 × 6,
ASCII[23], u8,
f32le × 2, u8 × 3,
u16le × 2,
ASCII[8],
u8 × 4
```

This structural layout is confirmed at `CONFIG.fromByteBloc`, AOT VA
`0x3ea540`. Semantic names and setter opcodes are listed only where their UI
call sites could be tied back to a field; unnamed offsets must be preserved as
unknown rather than guessed.

### Current values / telemetry — `0x06`

Request:

```text
02 06 06
```

The response payload is exactly 46 bytes, so its complete frame is 49 bytes
and begins with length `30` hex:

```text
payload +0   f32le
payload +4   f32le
payload +8   f32le
payload +12  f32le
payload +16  f32le
payload +20  f32le
payload +24  f32le
payload +28  f32le
payload +32  f32le
payload +36  u8
payload +37  f32le
payload +41  f32le
payload +45  u8
```

The parser is `VALUES.fromByteBloc` at AOT VA `0x3eb38c`. Field-name mappings
are recorded in the telemetry table below; the exact raw layout remains the
normative fallback for fields not yet semantically proven.

| Payload offset | Type | Meaning | Confidence/evidence |
| ---: | --- | --- | --- |
| 0 | f32le | input voltage, volts | confirmed; UI `0x484220`, object `+0x07` at `0x484278` |
| 4 | f32le | input current, amps | confirmed; UI `0x484368`, object `+0x0f` at `0x4843c0` |
| 8 | f32le | input frequency, hertz | confirmed; UI `0x4849c8`, object `+0x17` at `0x484a20` |
| 12 | f32le | temperature 1 | confirmed; `Temp` row `0x4852a4`, object `+0x1f` at `0x4852fc` |
| 16 | f32le | temperature 2 | confirmed; second temperature row `0x485520`, object `+0x27` at `0x485598` |
| 20 | f32le | output voltage, volts | confirmed; UI `0x4845e4`, object `+0x2f` at `0x48464c` |
| 24 | f32le | output current, amps | confirmed; UI `0x484748`, object `+0x37` at `0x4847b0` |
| 28 | f32le | current point | confirmed; UI `0x485160`, object `+0x3f` at `0x4851b8` |
| 32 | f32le | efficiency | confirmed; UI `0x484b10`, object `+0x47` at `0x484b68` |
| 36 | u8 | current output: strict `0` = ON/Open, `1` = OFF/Close, other = unknown | confirmed; AOT UI `0x486a20`, object `+0x4f` at `0x486ad4`, plus live frame and physical display |
| 37 | f32le | accumulated capacity, Ah | confirmed; object `+0x57`, `Ah` suffix at `0x485aa4` |
| 41 | f32le | accumulated energy, Wh | confirmed; object `+0x5f`, `Wh` suffix at `0x485b88` |
| 45 | u8 | module count | confirmed; UI `0x485678`, object `+0x67` at `0x485720` |

Input and output power are computed in the UI as voltage × current; they are
not separate telemetry payload fields. For reproducible cross-language output,
decode each binary32 operand, promote both values to binary64, and then
multiply. The two temperature labels do not prove which physical sensor is
which, so retain the neutral names `temperature_1` and `temperature_2`.

Telemetry and opcode `0x0C` have the same semantic polarity: `0` is ON/Open and
`1` is OFF/Close. Their wire types differ: telemetry uses one u8 at payload
offset 36, while the output-control request uses int32le. Any telemetry value
other than `0` or `1` is malformed/unknown; a client must not coerce or copy it
into a command.

### Set target voltage — `0x07`

Payload is one positive finite IEEE-754 binary32 value in volts, little-endian.
The low-level encoding has no range field. An HW178P client must first enforce
`50.00 <= value <= min(valid_device_max_voltage, 178.00)`.

### Set charge current — `0x08`

Payload is one positive finite IEEE-754 binary32 value in amperes,
little-endian. An HW178P client must first enforce
`0.01 <= value <= min(valid_device_max_single_module_current, 14.00)`. Do not
multiply the single-module limit by module count.

### Start/stop output — `0x0C`

Payload is a four-byte little-endian integer:

- `00 00 00 00`: energize/start/open output — **confirmed live**;
- `01 00 00 00`: de-energize/stop/close output — **high confidence** from the
  AOT toggle callback, but not yet transmitted to the sampled charger.

For generic setters, the response reuses the opcode and a one-byte payload
`01` is treated as request acceptance. The corresponding framed ACK for opcode
`X` is `03 X 01 ((X + 1) & ff)`. For output control this byte is not the output
state and is not sufficient to report success: require a later `0x06` response
whose strict payload byte 36 matches the requested state.

Live evidence is PID 7487 in the preserved 2026-08-25 incident: sequence 331
reported raw `1`/OFF at about 4.08 V; sequence 349 transmitted
`06 0C 00 00 00 00 0C`; sequence 359 returned acceptance ACK
`03 0C 01 0D`; sequence 378 reported raw `0`/ON; and sequences 432/462 showed
the voltage rising through 17.819 V to 134.112 V. The AOT callback loads the
state at `0x487490`, maps raw `1` to argument `0` and raw `0` to argument `1`,
loads opcode `0x0C` at `0x487500`, and calls the setter at `0x487504`. See
[`../evidence/live-gatt-2026-08-25.md`](../evidence/live-gatt-2026-08-25.md).

## Configuration and safety limits

The app exposes device-configured maximum voltage and maximum current as
binary32 fields in the `0x05` response. Their exact payload offsets are derived
from the parser/UI mapping in [`commands.md`](commands.md). The sampled live
configuration frame carries `00 00 32 43` at payload offset 33 (`178.0 V`) and
`00 00 60 41` at payload offset 37 (`14.0 A`). Those are device-reported upper
limits, not evidence for a minimum or a model-name field.

A safe independent HW178P client must:

1. complete `0x02` authentication;
2. read `0x05` successfully;
3. reject non-finite setpoints, voltage outside
   `50.00…min(max_voltage, 178.00) V`, and current outside
   `0.01…min(max_single_module_current, 14.00) A`;
4. require an explicit user action to send `0x0C` start;
5. stop or surface an error on missing/malformed telemetry rather than
   inventing values.

If either valid device maximum is narrower than the model maximum, the device
value wins. A missing, non-finite, non-positive, or otherwise unusable maximum
blocks setters and Start. A larger reported maximum never expands this profile.
Stop is unchanged: it remains available under its output-state/authorization
interlocks and does not depend on setpoint validity.

The offline Python codec validates only representation. It intentionally has
no hard-coded charger limits because those vary by hardware and firmware. Code
using it must apply a product/device policy before transmission.

The reference client separates transport/read access from mutation authority.
Monitoring mode may send authentication plus non-mutating requests `0x01`,
`0x04`, `0x05`, and `0x06`, but no exposed mutation. Opcodes `0x07`, `0x08`,
and `0x0C` all require explicit output-controls mode. Within that mode, Stop
requires authenticated transport and fresh telemetry explicitly reporting ON;
it does not require valid setpoint limits, fresh configuration, or Start
readiness. Stale, unknown, or already-OFF state fails closed.

## Authentication and identifiers

| Candidate mechanism | Finding |
| --- | --- |
| Serial number | Read by opcode `0x04`; not included in other recovered frames |
| Android MAC/device ID | Connection identifier passed to Quick Blue; not packet payload |
| BLE pairing/bonding | no call in the recovered native/plugin route |
| Application credential | opcode `0x02`; 32 ASCII MD5-hex characters + NUL; unsalted bearer value rather than plaintext |
| Missing saved credential | exact uppercase fallback `D41D8CD98F00B204E9800998ECF8427E`; accepted live by the sampled charger, without implying a blank plaintext password |
| Change password | opcode `0x03`; first 18 ASCII characters of MD5 hex for old password, then the same for new password; documentation-only |
| Device/Bluetooth name | likely config ASCII[23] and string setter `0x1E`; high confidence, not captured |
| Pairing token/cloud token | no evidence in packet construction or handshake |
| Timestamp/nonce/challenge | absent from the recovered handshake/frame |
| Sequence number/counter | absent from frame and packet builder |

“No evidence” is scoped to the inspected Android 1.7.5 HWCDQ route. It is not a
claim about every firmware revision.

## Test vectors

Spaces are only for readability.

| Action | Checksum input | Checksum | Complete encoded frame |
| --- | --- | ---: | --- |
| get firmware | `01` | `01` | `02 01 01` |
| fallback credential | `02 44 34 31 44 38 43 44 39 38 46 30 30 42 32 30 34 45 39 38 30 30 39 39 38 45 43 46 38 34 32 37 45 00` | `45` | `23 02 44 34 31 44 38 43 44 39 38 46 30 30 42 32 30 34 45 39 38 30 30 39 39 38 45 43 46 38 34 32 37 45 00 45` |
| plaintext `test` → lowercase MD5 credential | `02 30 39 38 66 36 62 63 64 34 36 32 31 64 33 37 33 63 61 64 65 34 65 38 33 32 36 32 37 62 34 66 36 00` | `CA` | `23 02 30 39 38 66 36 62 63 64 34 36 32 31 64 33 37 33 63 61 64 65 34 65 38 33 32 36 32 37 62 34 66 36 00 CA` |
| credential accepted | `02 01` | `03` | `03 02 01 03` |
| credential rejected | `02 00` | `02` | `03 02 00 02` |
| get serial | `04` | `04` | `02 04 04` |
| get config | `05` | `05` | `02 05 05` |
| get telemetry | `06` | `06` | `02 06 06` |
| set voltage 84.0 V | `07 00 00 A8 42` | `F1` | `06 07 00 00 A8 42 F1` |
| set voltage 48.0 V — protocol-format vector only; below HW178P profile, do not transmit | `07 00 00 40 42` | `89` | `06 07 00 00 40 42 89` |
| set current 10.0 A | `08 00 00 20 41` | `69` | `06 08 00 00 20 41 69` |
| set current 20.0 A — protocol-format vector only; above HW178P profile, do not transmit | `08 00 00 A0 41` | `E9` | `06 08 00 00 A0 41 E9` |
| start/open (confirmed live) | `0C 00 00 00 00` | `0C` | `06 0C 00 00 00 00 0C` |
| stop/close (high confidence; live pending) | `0C 01 00 00 00` | `0D` | `06 0C 01 00 00 00 0D` |

The values above were calculated from the recovered application builder and
are asserted as literal expected bytes in `tests/test_protocol.py`; the tests
do not derive expected packets by calling the encoder under test. The start
vector additionally appears verbatim in the live incident evidence; the Stop
vector remains a static/AOT-derived high-confidence vector.

The `48 V` and `20 A` rows deliberately prove binary32/checksum framing only.
They are outside the HW178P application envelope and must be rejected before
BLE transmission. Safe in-profile encoding examples include `134 V` as
`06 07 00 00 06 43 50` and `14 A` as `06 08 00 00 60 41 A9`; these examples
do not upgrade the setters from static to live-confirmed.

### Captured telemetry decoder vector — output OFF

Live diagnostic PID 67357 sequence 3176 captured this complete checksum-valid
FFE2 notification:

```text
30 06 00 30 5D 43 00 00 00 00 00 85 48 42 00 CD 06 42
00 00 18 42 8D 6A 82 40 00 00 00 00 00 00 00 00 00 00
00 00 01 00 00 00 00 00 00 00 00 01 0F
```

Checksum input is every byte from opcode `06` through the last payload byte
`01`; their low-eight-bit sum is the stored checksum `0F`. Decoded values:

| Field | Value |
| --- | ---: |
| input voltage | `221.1875 V` |
| input current / power | `0 A` / `0 W` |
| input frequency | `50.1298828125 Hz` |
| temperature 1 / 2 | `33.7001953125 °C` / `38.0 °C` |
| output voltage | `4.075506687164307 V` |
| output current / power | `0 A` / `0 W` |
| current point / efficiency | `0` / `0` |
| raw output state | `1` = OFF/Close |
| accumulated capacity / energy | `0 Ah` / `0 Wh` |
| module count | `1` |

At the same session state the physical charger display showed 4.076 V,
0.000 A, and its inactive output indicator; the owner explicitly stated that
output was off. The AOT Open/Close branch independently maps raw `1` to
Close/off. The historic JSONL's `output_enabled:true` field was emitted by the
old desktop decoder and is preserved as evidence of that software bug, not as
protocol evidence. Attachment hashes and the complete event chain are recorded
in `../evidence/live-gatt-2026-08-25.md`.

### Synthetic layout vector

The test suite also retains a clearly labelled synthetic vector to exercise
the complete raw layout, including an intentionally invalid output-state enum:

- float offsets 0..32 contain values 1.0 through 9.0;
- byte 36 is `A5`;
- floats at 37 and 41 are 10.0 and 11.0;
- byte 45 is `5A`;
- opcode is `06`, and the frame checksum is generated/verified separately.

Complete synthetic frame:

```text
30 06
00 00 80 3F  00 00 00 40  00 00 40 40
00 00 80 40  00 00 A0 40  00 00 C0 40
00 00 E0 40  00 00 00 41  00 00 10 41
A5
00 00 20 41  00 00 30 41
5A A8
```

Decoded values are input V/A/Hz `1/2/3`, temperatures `4/5`, output V/A
`6/7`, current point `8`, efficiency `9`, raw output state `0xA5`, accumulated
capacity `10 Ah`, accumulated energy `11 Wh`, and module count `0x5A`. Derived
input/output power is `2 W` / `42 W`. The final `A8` is the valid additive
checksum. Because `0xA5` is neither `0` nor `1`, its decoded boolean state is
`None`/unknown. This synthetic frame is **not** presented as a charger capture.

## iOS / CoreBluetooth implementation notes

The exact APK route and the sampled charger agree: discover FFE1 and accept
exactly one FFE1 instance containing one usable sibling FFE2/FFE3 pair. Reject
ambiguous duplicate pairs and never mix characteristics across parents. FFE2
is the response/telemetry subscription characteristic; FFE3 is command TX.
The old FFE0-only client policy is disproven for this device.

The recovered APK explicitly enables FFE2 notifications, so the reference
client requires the `notify` property. The sampled FFE2 has both `notify` and
`indicate`; CoreBluetooth subscribes with `setNotifyValue(true, for:)`. An
indicate-only characteristic is not accepted without new device-specific
evidence and a specification update.

Recommended state machine:

```text
scan → connect → discover FFE1 → require one sibling FFE2/FFE3 pair
     → enable FFE2 notifications → wait for subscription callback
     → wait 1 second (matching the recovered Android route)
     → credential 0x02 → firmware 0x01 → serial 0x04 → config 0x05
     → derive effective HW178P bounds; fail closed if unusable
     → telemetry 0x06 → ready
     → wait 5 s after each maintenance exchange
       → telemetry 0x06 normally / config 0x05 when 60 s refresh is due
```

An iOS client may remember the last successfully authenticated peripheral's
machine-local `CBPeripheral.identifier` UUID and preselect only an exact match
after scanning. This is a convenience hint, not identity or authority: never
auto-connect, never overwrite a manual selection with a later advertisement,
and tolerate the UUID becoming stale. Persist no password, derived credential,
serial, advertised name, or telemetry for this feature.

After every initial or refreshed `0x05`, compute the HW178P bounds as
`voltage = 50.00…min(deviceMaxVoltage, 178.00)` and
`current = 0.01…min(deviceMaxSingleModuleCurrent, 14.00)`. If the resulting
range is empty or either maximum is invalid, disable voltage/current setters
and Start. Recheck the same limits against freshly read configuration inside
the serialized Start transaction. Do not apply these checks to Stop; its safety
contract is based on explicit control authorization and fresh ON telemetry.

CoreBluetooth does not expose Android's explicit `requestMtu(120)`. Prefer
`.withoutResponse` when FFE3 advertises `.writeWithoutResponse`, using
`maximumWriteValueLength(for: .withoutResponse)`,
`canSendWriteWithoutResponse`, and
`peripheralIsReady(toSendWriteWithoutResponse:)`. If WNR is absent but
acknowledged `.write` exists, use `.withResponse` and issue the next chunk only
after `didWriteValueFor`. Chunk only when a complete frame is larger than the
selected maximum; add no fragment metadata. Fix the selected type for the
entire connection.

Use roughly three seconds for connect/discovery and for the native write phase;
start a separate eight-second application-response timer only after the last
chunk completes. A response timeout makes the sequence-less notification
stream unsafe: invalidate that peripheral generation, discard partial/late RX,
disconnect, and never issue another request on the same link. A read-only
maintenance failure may visibly reconnect after 1, 2, and 5 seconds, at most
three times, then rerun credential → firmware/serial → config → telemetry.
Cancel this recovery on explicit Disconnect or shutdown.

Never replay a state-changing command automatically or interpret reconnect as
its continuation: its result is unknown until an independent readback or a new
operator decision establishes state. A mutation ACK and its readback are
separate exchanges with independent deadlines. Do not assume authentication or
limits survive a connection.

Minimal Swift-style pseudocode:

```swift
let serviceFFE1 = CBUUID(string: "FFE1")
let rxFFE2 = CBUUID(string: "FFE2")
let txFFE3 = CBUUID(string: "FFE3")
let hw178pVoltageRange = 50.0...178.0
let hw178pCurrentRange = 0.01...14.0

func applyConfig(_ config: ChargerConfig) throws {
    guard config.maxVoltage.isFinite,
          config.maxSingleModuleCurrent.isFinite else { throw UnsafeConfig() }
    let maxV = min(config.maxVoltage, hw178pVoltageRange.upperBound)
    let maxA = min(config.maxSingleModuleCurrent, hw178pCurrentRange.upperBound)
    guard maxV >= hw178pVoltageRange.lowerBound,
          maxA >= hw178pCurrentRange.lowerBound else { throw UnsafeConfig() }
    effectiveVoltageRange = hw178pVoltageRange.lowerBound...maxV
    effectiveCurrentRange = hw178pCurrentRange.lowerBound...maxA
}

func sendSetVoltage(_ volts: Double) throws {
    guard effectiveVoltageRange.contains(volts) else { throw UnsafeSetpoint() }
    send(encodeSetVoltage(Float(volts)))
}

func sendSetCurrent(_ amps: Double) throws {
    guard effectiveCurrentRange.contains(amps) else { throw UnsafeSetpoint() }
    send(encodeSetCurrent(Float(amps)))
}

central.scanForPeripherals(withServices: nil) // filter after discovery/name evidence

func centralManager(_ central: CBCentralManager,
                    didDiscover peripheral: CBPeripheral, ...) {
    discovered[peripheral.identifier] = peripheral
    if peripheral.identifier == savedLastAuthenticatedID && !userSelectedManually {
        preselectInUI(peripheral) // convenience only; do not connect here
    }
}

func userDidChoose(_ peripheral: CBPeripheral) {
    self.peripheral = peripheral
    peripheral.delegate = self
    central.connect(peripheral) // only an explicit operator action connects
}

func centralManager(_ central: CBCentralManager,
                    didConnect peripheral: CBPeripheral) {
    peripheral.discoverServices([serviceFFE1])
}

func peripheral(_ peripheral: CBPeripheral,
                didDiscoverServices error: Error?) {
    let matches = (peripheral.services ?? []).filter { $0.uuid == serviceFFE1 }
    guard matches.count == 1 else {
        failConnection("one FFE1 service is required")
        return
    }
    peripheral.discoverCharacteristics([rxFFE2, txFFE3], for: matches[0])
}

func peripheral(_ peripheral: CBPeripheral,
                didDiscoverCharacteristicsFor service: CBService,
                error: Error?) {
    guard let candidateRX = service.characteristics?.first(where: { $0.uuid == rxFFE2 }),
          let candidateTX = service.characteristics?.first(where: { $0.uuid == txFFE3 })
    else { return }

    guard service.uuid == serviceFFE1,
          chooseOnlyUniqueTopology(parent: service,
                                   rx: candidateRX,
                                   tx: candidateTX) else { return }
    rx = candidateRX
    tx = candidateTX
    guard rx.properties.contains(.notify) else { return }

    if tx.properties.contains(.writeWithoutResponse) {
        writeType = .withoutResponse
    } else if tx.properties.contains(.write) {
        writeType = .withResponse
    } else {
        return // incompatible GATT table; do not guess
    }
    // The recovered route explicitly requires notify; indicate-only is rejected.
    peripheral.setNotifyValue(true, for: rx)
}

func peripheral(_ peripheral: CBPeripheral,
                didUpdateNotificationStateFor characteristic: CBCharacteristic,
                error: Error?) {
    guard characteristic.uuid == rxFFE2,
          characteristic.isNotifying, error == nil else { return }
    DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
        // Lowercase MD5 hex for nonempty UTF-8 user input; use the exact
        // uppercase APK fallback only when intentionally selecting fallback.
        let credential = derivePasswordCredential(userPassword)
        send(encodeCheckPasswordCredential(credential))
    }
}

func send(_ frame: Data) {
    let n = peripheral.maximumWriteValueLength(for: writeType)
    pendingChunks.append(contentsOf: frame.chunks(ofCount: n))
    sendNextChunk()
}

func sendNextChunk() {
    guard let chunk = pendingChunks.first else { return }
    if writeType == .withoutResponse {
        guard peripheral.canSendWriteWithoutResponse else { return }
        pendingChunks.removeFirst()
        peripheral.writeValue(chunk, for: tx, type: .withoutResponse)
        sendNextChunk() // flow control will stop and resume via readiness callback
    } else {
        peripheral.writeValue(chunk, for: tx, type: .withResponse)
        // Remove it and call sendNextChunk() only from didWriteValueFor on success.
    }
}

func peripheralIsReady(toSendWriteWithoutResponse peripheral: CBPeripheral) {
    sendNextChunk()
}

func peripheral(_ peripheral: CBPeripheral,
                didWriteValueFor characteristic: CBCharacteristic,
                error: Error?) {
    guard writeType == .withResponse, characteristic.uuid == txFFE3,
          error == nil else { return }
    pendingChunks.removeFirst()
    sendNextChunk()
}

func peripheral(_ peripheral: CBPeripheral,
                didUpdateValueFor characteristic: CBCharacteristic,
                error: Error?) {
    guard characteristic.uuid == rxFFE2, let bytes = characteristic.value else { return }
    receiveBuffer.append(bytes)
    while receiveBuffer.count >= 1 {
        let total = 1 + Int(receiveBuffer[0])
        guard total >= 3 && total <= 256 else {
            failConnection("invalid HWCDQ frame length; cannot resynchronize")
            return
        }
        guard receiveBuffer.count >= total else { break }
        let frame = receiveBuffer.prefix(total)
        receiveBuffer.removeFirst(total)
        guard verifyChecksum(frame) else {
            failConnection("invalid HWCDQ checksum; reconnect required")
            return
        }
        handle(decodePacket(frame))
        // After a successful opcode-0x02 authentication response only:
        // persist peripheral.identifier, never the credential or device data.
    }
}
```

The sampled charger advertised FFE1 before connection, so a client may rank
that advertised service highly while still scanning broadly and requiring
explicit operator selection. The
sole target-code xref to `HWCDQBLE_NIUB` is at `0x3cef08..0x3cef20` in a scan
callback: it is passed to a dynamically dispatched boolean method on a value
from `BlueScanResult.toMap` before the app adds the device. This strongly
supports its use as a scan discriminator, but does not prove the map field or
whether the predicate is equality, `contains`, or something else. Product
screenshots and the sampled advertisement show names beginning `HWCDQ_`.
Neither name string should be a hard security filter: scan broadly, let the
user select the charger, then require the post-connection FFE1/FFE2/FFE3
topology.

## Known gaps and safest next evidence

- The sampled GATT table resolves the parent as FFE1 and proves that FFE3
  offers both write modes. The original Android app's effective ATT type is
  still unknown because its bridge does not apply the Dart WNR argument.
- The corrected live monitor has confirmed FFE2 notifications, the fallback
  credential ACK, firmware/serial/configuration replies, and 127 checksum-valid
  telemetry frames. It used acknowledged FFE3 writes. The rebuilt
  WNR-preferred policy still needs a bounded read-only soak; long-run WNR
  durability is not verified yet.
- The live raw state byte `1` plus AOT and physical-display evidence confirms
  telemetry OFF/Close. A later accidental monitoring-mode command
  live-confirmed `0x0C` int32le zero as Start/ON: its acceptance ACK was followed
  by raw state zero and output-voltage ramp. Voltage/current setters still need
  staged ACK/readback vectors; Stop int32le one remains high confidence from AOT
  and has not been transmitted to this hardware.
- Some settings/calibration opcodes are statically mapped only to UI fields;
  firmware ranges and side effects require a passive capture before exposure.
- Factory/reset-like operations remain documentation-only and have no encoder.
- The password-change and firmware-update paths were reconstructed only far
  enough to inventory their writes; both remain deliberately unimplemented.
- Firmware-version response string termination and serial length need a real
  response sample.

The safest next step is the bounded monitoring-only WNR soak in
`live-testing.md`: subscribe to FFE2, authenticate once, send only the known
non-mutating queries, and verify periodic telemetry/config plus any clean
read-only recovery. Preserve raw redacted packets and stop on rejection,
malformed framing, or exhausted reconnects. Only after that pass is repeatable
should the one-change-at-a-time voltage/current and Stop plan proceed with a
corrected control-gated build and independent physical safety controls.
Do not fuzz, brute-force credentials, or transmit unknown opcodes.
