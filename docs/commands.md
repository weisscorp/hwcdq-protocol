# HWCDQ command inventory

Requests and responses use the same opcode. Every row below is wrapped in the
frame described in [`protocol.md`](protocol.md):
`[payload_length + 2][opcode][payload][sum8]`.
The executable cross-language subset and its golden packets are also pinned in
[`../contract/v1`](../contract/v1); tentative and unknown rows remain
non-executable there.

Confidence vocabulary:

- **confirmed** — direct target-route AOT builder/parser and UI call-site
  evidence, with literal encoder vectors where applicable;
- **high confidence** — direct code/data-flow evidence with one remaining
  semantic detail (usually a label, response encoding, or firmware range);
- **tentative** — plausible static association that must not be exposed for
  transmission without passive capture;
- **unknown** — observed opcode/call site but meaning is not established.

Confidence is attached to the specific claim, not merely the opcode number.
The sampled charger has now live-confirmed subscription, authentication,
firmware/serial/configuration queries, repeated telemetry, and the int32le-zero
output-control value. Voltage/current setters and int32le-one Stop remain
without live transmission evidence.

The companion **Pidzoom Portable charger HW178P** client applies
`50.00…178.00 V` and `0.01…14.00 A`, intersected with narrower valid maxima in
the `0x05` response. The exact model and `50 V` minimum are owner-supplied, not
wire fields; `0.01 A` is conservative client policy rather than a proven
hardware minimum.

## Interoperability commands

| Opcode | Direction | Meaning | Payload | Response | Confidence |
| ---: | --- | --- | --- | --- | --- |
| `0x01` | client → charger | get firmware version | none | same opcode; bytes passed to `String.fromCharCodes` | confirmed static and live (`tps_2.2.4`) |
| `0x02` | client → charger | check application credential | 32 ASCII MD5-hex characters followed by `00` | same opcode; payload `01` accepted, `00` rejected | confirmed static; exact vectors; fallback accepted live |
| `0x03` | client → charger | change application password | fixed 18-byte old-password digest text + fixed 18-byte new-password digest text; see below | same opcode; payload `01` means success | high confidence; no encoder by design |
| `0x04` | client → charger | get serial number | none | same opcode; binary serial rendered by app as hex | confirmed static and live |
| `0x05` | client → charger | get complete configuration | none | same opcode; 103-byte payload | confirmed static and live |
| `0x06` | client → charger | get current values/telemetry | none | same opcode; 46-byte payload | confirmed static and live; 127 checksum-valid frames |
| `0x07` | client → charger | set target voltage | f32le volts; HW178P client permits `50…min(device max, 178) V` | same opcode; payload `01` means success | confirmed static; literal vectors; live pending |
| `0x08` | client → charger | set target charge current | f32le amps; HW178P client permits `0.01…min(device single-module max, 14) A` | same opcode; payload `01` means success | confirmed static; literal vectors; live pending |
| `0x0C` | client → charger | current output control | int32le: `0` energize/start/ON, `1` de-energize/stop/OFF | same opcode; payload `01` means request accepted, not returned state; verify via `0x06` | `0` confirmed live; `1` high confidence from AOT toggle callback, live pending |

Exact empty-payload requests:

```text
get firmware   02 01 01
get serial     02 04 04
get config     02 05 05
get telemetry  02 06 06
```

## Settings and calibration writes

These mappings follow a UI label/controller into its closure and then into the
opcode passed to `save_config`; they are not inferred merely from opcode order.
The response convention is the same-opcode one-byte success flag described
below.

| Opcode | Direction | Meaning | Request payload | Confidence |
| ---: | --- | --- | --- | --- |
| `0x09` | client → charger | set offline/default voltage | f32le volts | confirmed static |
| `0x0A` | client → charger | set offline/default current | f32le amps | confirmed static |
| `0x0B` | client → charger | set power-on output | int32le bool (`0`/`1`) | confirmed static |
| `0x0D` | client → charger | set voltage calibration | f32le | confirmed static |
| `0x0E` | client → charger | set voltage-feedback calibration | f32le | confirmed static |
| `0x0F` | client → charger | set current calibration | f32le | confirmed static |
| `0x10` | client → charger | set current-feedback calibration | f32le | confirmed static |
| `0x11` | client → charger | set maximum voltage | f32le volts | confirmed static |
| `0x12` | client → charger | set maximum single-module current | f32le amps | confirmed static |
| `0x14` | client → charger | set automatic stop | int32le bool (`0`/`1`) | confirmed static |
| `0x15` | client → charger | set shutdown current | f32le amps | confirmed static |
| `0x16` | client → charger | set a temperature-protection/settings flag | int32le bool (`0`/`1`), then app sends `0x24`/zero | tentative semantic; format/call confirmed |
| `0x17` | client → charger | set temperature protection threshold | int32le | confirmed static |
| `0x19` | client → charger | set an additional temperature/settings enum | int32le, then app sends `0x24`/zero | unknown semantic; format/call confirmed |
| `0x1A` | client → charger | set protection cut-off temperature | int32le | confirmed static |
| `0x1B` | client → charger | set fan boost temperature | int32le | confirmed static |
| `0x1C` | client → charger | set fan maximum-speed temperature | int32le | confirmed static |
| `0x20` | client → charger | enable/disable two-stage charging | int32le bool (`0`/`1`) | confirmed static |
| `0x21` | client → charger | set second-stage voltage | f32le volts | confirmed static |
| `0x22` | client → charger | set second-stage current | f32le amps | confirmed static |
| `0x23` | client → charger | set offline/button control | int32le bool (`0`/`1`) | high confidence; UI wording is ambiguous in translation |
| `0x24` | client → charger | apply/reboot follow-up | int32le zero | high confidence role; exact reboot side effect requires capture |
| `0x25` | client → charger | set settings/factory integer | int32le, then app sends `0x24`/zero | unknown semantic; format/call confirmed |
| `0x26` | client → charger | set soft-start coefficient | int32le | confirmed static |
| `0x27` | client → charger | set power limit | int32le | confirmed static |
| `0x29` | client → charger | set maximum power | int32le | confirmed static |
| `0x2A` | client → charger | set display language | UTF-8 string + `00`; followed by an apply/reboot command in the app | high confidence; apply opcode still separately documented |
| `0x2B` | client → charger | set settings/factory integer | int32le, then app sends `0x24`/zero | unknown semantic; format/call confirmed |
| `0x2E` | client → charger | set settings/factory integer | int32le, then app sends `0x24`/zero | unknown semantic; format/call confirmed |
| `0x2F` | client → charger | set settings/factory integer | int32le | unknown semantic; format/call confirmed |
| `0x30` | client → charger | set settings/factory integer | int32le, then app sends `0x24`/zero | unknown semantic; format/call confirmed |

Although these formats are statically strong, the APK does not supply safe
universal ranges and no live ACK has been captured. The reference codec does
not encode them yet. Calibration, limit, thermal, power, and apply/reboot
controls should remain unavailable in an iOS UI until a passive original-app
capture confirms one known-safe value and its ACK on the user's exact firmware.

## Device identity and credential management

Opcode `0x1E` is a string setter reached from the device-information page and
connected to the 23-byte configuration string at payload offset 52. The
strongest interpretation is **set Bluetooth/device name**. Its request payload
uses the generic UTF-8-plus-NUL format. Because an advertisement capture has
not confirmed how that string is exposed, the semantic confidence is **high**,
not confirmed, and no reference encoder is supplied.

The ordinary opcode-`0x02` handshake does not send plaintext. The route reads a
per-device SharedPreferences JSON entry keyed by Android `deviceId`, takes its
`password` value, and sends that already-derived 32-character digest plus NUL.
If the entry is missing, the APK uses the exact uppercase fallback
`D41D8CD98F00B204E9800998ECF8427E`. User-entered nonempty text is converted as
`lowercase_hex(MD5(UTF8(text)))` before retry and before persistence. The MAC
and serial number select/identify the device but are absent from the digest and
wire packet.

The exact fallback request and the two logical responses are:

```text
request  23 02 44 34 31 44 38 43 44 39 38 46 30 30 42 32 30 34
         45 39 38 30 30 39 39 38 45 43 46 38 34 32 37 45 00 45
accept   03 02 01 03
reject   03 02 00 02
```

This fallback is a replayable bearer credential, not evidence that the
plaintext password is empty. The sampled charger accepted it live, but a
reconfigured or different unit may not. If the owner changed the credential,
recover the saved digest from the original
Android app data or a passive opcode-`0x02` capture; do not brute-force it. The
literal `0000` embedded elsewhere in the AOT binary has no HWCDQ password code
xref.

Password change is not the generic string setter. `packetUtil.change_pass` at
AOT VA `0x475460` performs this sequence independently for the old and new
password:

```text
UTF-8(password) → MD5 → lowercase hexadecimal Digest.toString()
                → ByteBloc.writeString(length = 18)
```

`CHANGE_PASS.toByteBloc` concatenates the two fixed 18-byte regions, yielding a
36-byte opcode-`0x03` payload. `writeString` truncates a longer string and pads
a shorter one with zero bytes, so for ordinary MD5 hex the transmitted value is
the first 18 ASCII hex characters of each digest. The code path and sizes are
directly recovered; it has not been hardware-validated. Changing credentials
can lock out the owner, so this operation is documentation-only.

An alternative empty-payload firmware/text query uses opcode `0x1D` through
the same `get_fw_ver` response parser as `0x01`. The caller context does not
establish what component/version it selects, so it is **unknown**:

```text
02 1D 1D
```

## Firmware-update path (documented, never to be transmitted here)

The APK contains a separate updater. It is recorded only to make the outgoing
inventory complete and is explicitly outside the interoperability client:

| Stage | Wire behavior | Evidence/confidence |
| --- | --- | --- |
| initialize | framed opcode `0x00`, payload `uint32le` firmware byte length | confirmed static, `0x489c68..0x489d60` |
| data | unframed firmware bytes in logical blocks up to 1024; each block goes through ordinary MTU−3 BLE splitting | confirmed static, `0x4896d4..0x4898a4` |
| block ACK | updater reads one raw byte after each logical block | confirmed static; success value not published as a safe contract |
| finalize | framed opcode `0x28`, int32le zero | confirmed static call; semantics scoped to updater |
| apply/reboot | framed opcode `0x24`, int32le zero | high confidence |

This updater is the only recovered exception to the normal “all application
messages are length-framed” rule. It is not implemented in
`hwcdq_protocol.py`; using it would modify charger firmware, contrary to the
scope and safety constraints.

## Telemetry response (`0x06`)

| Payload offset | Size/type | Meaning | Unit | Confidence |
| ---: | --- | --- | --- | --- |
| 0 | 4 / f32le | input voltage | V | confirmed |
| 4 | 4 / f32le | input current | A | confirmed |
| 8 | 4 / f32le | input frequency | Hz | confirmed |
| 12 | 4 / f32le | temperature 1 | app does not expose sensor identity | confirmed |
| 16 | 4 / f32le | temperature 2 | app does not expose sensor identity | confirmed |
| 20 | 4 / f32le | output voltage | V | confirmed |
| 24 | 4 / f32le | output current | A | confirmed |
| 28 | 4 / f32le | current point | app label; exact engineering unit not proven | confirmed field label/type; unit unknown |
| 32 | 4 / f32le | efficiency | app label; display scaling should follow live sample | confirmed field label/type; unit presentation unknown |
| 36 | 1 / u8 | current output state | strict: `0` ON/Open, `1` OFF/Close, any other value unknown | confirmed AOT branch plus live frame/physical display |
| 37 | 4 / f32le | accumulated capacity | Ah | confirmed |
| 41 | 4 / f32le | accumulated energy | Wh | confirmed |
| 45 | 1 / u8 | module count | count | confirmed |

Input power is `input_voltage × input_current`; output power is
`output_voltage × output_current`. The Android UI performs these products. The
portable contract decodes each binary32 operand, promotes both operands to
binary64, and multiplies in binary64 so every wrapper reports the same result.

Telemetry and opcode `0x0C` use the same semantic polarity: `0` is ON/Open and
`1` is OFF/Close. The types still differ—telemetry is one u8 at payload offset
36, whereas the request is one int32le value. Never copy an unvalidated state
byte into a request: any telemetry value other than `0` or `1` is unknown. AOT
UI code at `0x486a20` reads object field `+0x4f`; the output callback loads the
opcode immediate at `0x487500` and calls the generic four-byte setter at
`0x487504`.

Representative live OFF telemetry, PID 67357 sequence 3176:

```text
30 06 00 30 5D 43 00 00 00 00 00 85 48 42 00 CD 06 42
00 00 18 42 8D 6A 82 40 00 00 00 00 00 00 00 00 00 00
00 00 01 00 00 00 00 00 00 00 00 01 0F
```

The checksum is valid (`0x0F`). It decodes to approximately 221.19 V input,
50.13 Hz, 33.70/38.00 °C, 4.0755 V and 0 A output, state byte `1`, 0 Ah,
0 Wh, and one module. The charger screen showed 4.076 V/0.000 A and the owner
explicitly confirmed output was off. This live evidence corrects the earlier
desktop decoder; the `output_enabled:true` value recorded inside the historic
JSONL line was the bug under investigation, not a wire fact.

## Configuration response (`0x05`)

The table is the complete 103-byte parser layout. Names are included only where
parser object fields were connected to editing/display code. `raw_*` fields are
deliberately not guessed.

| Payload offset | Size/type | Meaning | Confidence |
| ---: | --- | --- | --- |
| 0 | 4 / f32le | current target voltage | confirmed |
| 4 | 4 / f32le | current target current | confirmed |
| 8 | 4 / f32le | offline/default voltage | high confidence |
| 12 | 4 / f32le | offline/default current | high confidence |
| 16 | 1 / u8 | power-on output flag | confirmed static |
| 17 | 4 / f32le | voltage calibration | confirmed static |
| 21 | 4 / f32le | voltage-feedback calibration | confirmed static |
| 25 | 4 / f32le | current calibration | confirmed static |
| 29 | 4 / f32le | current-feedback calibration | confirmed static |
| 33 | 4 / f32le | maximum voltage limit | confirmed static and live; sampled value `178.0 V` (`00 00 32 43`) |
| 37 | 4 / f32le | maximum single-module current limit | confirmed static and live; sampled value `14.0 A` (`00 00 60 41`) |
| 41 | 1 / u8 | automatic-stop flag | confirmed static |
| 42 | 4 / f32le | shutdown current | confirmed static |
| 46 | 1 / u8 | raw setting | unknown |
| 47 | 1 / u8 | temperature protection threshold | confirmed static |
| 48 | 1 / u8 | raw temperature setting | tentative |
| 49 | 1 / u8 | protection cutoff temperature | confirmed static |
| 50 | 1 / u8 | fan boost temperature | confirmed static |
| 51 | 1 / u8 | fan maximum-speed temperature | confirmed static |
| 52 | 23 / fixed ASCII | raw device string | unknown |
| 75 | 1 / u8 | two-stage charging flag | confirmed static |
| 76 | 4 / f32le | second-stage voltage | confirmed static |
| 80 | 4 / f32le | second-stage current | confirmed static |
| 84 | 1 / u8 | offline/button-control flag | high confidence; UI translation ambiguous |
| 85 | 1 / u8 | raw setting flag | unknown |
| 86 | 1 / u8 | soft-start coefficient | confirmed static |
| 87 | 2 / u16le | power limit | confirmed static |
| 89 | 2 / u16le | maximum power | confirmed static |
| 91 | 8 / fixed ASCII | display-language string | high confidence |
| 99 | 1 / u8 | raw factory/device setting | unknown |
| 100 | 1 / u8 | raw factory/device setting | unknown |
| 101 | 1 / u8 | raw factory/device setting | unknown |
| 102 | 1 / u8 | raw factory/device setting | unknown |

The raw parser layout is confirmed even where a semantic label is not. A
client may decode and retain every field, but must not expose an unnamed value
as a writable setting.

## ACK convention

The generic configuration setter accepts success when the same-opcode response
payload is exactly one byte with value `01`. Its complete success ACK is:

```text
03 <opcode> 01 <(opcode + 1) modulo 256>
```

For output control, this ACK means only that the request was accepted. It does
not echo or otherwise report the output state. A client must issue/await a
subsequent `0x06` telemetry response and compare strict payload byte 36 with the
requested state before reporting success.

The application also has a failure path. Do not treat a syntactically valid
frame as success unless the command-specific response payload is understood.
For the output-control evidence chain and corrected AOT addresses, see
[`../evidence/live-gatt-2026-08-25.md`](../evidence/live-gatt-2026-08-25.md).

## Unknown and intentionally unsupported operations

Opcode `0x13` is directly emitted with an int32le zero from UI callback
`0x487a10`. Its UI context suggests a statistics-reset/clear action, but that
semantic connection is not yet strong enough to transmit or encode. It is
therefore **unknown**, not “clear Ah/Wh”.

The complete direct `save_config` opcode set in the HWCDQ page is `07`, `08`,
`09`, `0A`, `0B`, `0C`, `0D`, `0E`, `0F`, `10`, `11`, `12`, `13`, `14`, `15`,
`16`, `17`, `19`, `1E`, `20`, `21`, `22`, `23`, `24`, `25`, `26`, `27`, `28`,
`29`, `2A`, `2B`, `2E`, `2F`, and `30`. Repeated `0x24` call sites are the
follow-up described above. No direct setter calls were found for `0x18`,
`0x1F`, `0x2C`, or `0x2D`; gaps must not be filled by guessing. The offline
codec intentionally has no generic `set_config(opcode, value)` API, because
such an API would make accidental transmission of tentative commands too easy.

## Required-operation coverage

| Required path | Recovered information | Status |
| --- | --- | --- |
| discover charger | sampled advertisement included FFE1; user selection remains required because the exact `HWCDQBLE_NIUB` scan predicate is unresolved | live confirmed for sampled device; generic predicate partial |
| connect and discover | one live FFE1 parent contained sibling FFE2 and FFE3; FFE2 properties indicate/notify/read/write/WNR, FFE3 write/WNR, CoreBluetooth WNR max 253 | confirmed live on sampled device |
| subscribe | application explicitly uses FFE2 notify; sampled FFE2 advertises notify and indicate | confirmed static and live |
| transmit | use FFE3; independent client now prefers WNR when advertised, with acknowledged write as compatibility fallback | acknowledged writes/application replies live; WNR durability awaiting bounded read-only soak |
| application handshake | 32-hex MD5 credential `0x02`, then firmware/serial/config ordering | confirmed static and live with fallback credential |
| receive telemetry | `0x06`, frame reassembly, complete 46-byte semantic layout | confirmed static and live; output-state polarity corrected by physical evidence |
| set target voltage | `0x07`, f32le volts; HW178P envelope `50…178 V`, narrowed by valid device maximum | confirmed static; profile minimum owner-supplied |
| set target current | `0x08`, f32le amps; HW178P envelope `0.01…14 A`, narrowed by valid device single-module maximum | confirmed static; `0.01 A` is conservative client policy |
| start charging/output | `0x0C`, int32le `0`, frame `06 0C 00 00 00 00 0C` | confirmed live: accepted and telemetry changed OFF→ON |
| stop charging/output | `0x0C`, int32le `1`, frame `06 0C 01 00 00 00 0D` | high confidence from AOT toggle callback; not yet transmitted live |

The GATT table and read-only application path are no longer open questions for
the sampled charger. Voltage/current setters and Stop remain
hardware-unverified and must retain their stated confidence until a staged
one-change-at-a-time test supplies raw ACK plus readback. Start int32le zero is
live-confirmed by the preserved monitoring-mode incident, not by a planned safe
control test. Before any further control
pass, the rebuilt WNR policy needs the bounded monitoring-only soak described
in `live-testing.md`; no long-run WNR success is claimed yet.
