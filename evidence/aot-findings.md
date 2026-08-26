# AOT finding ledger

This ledger preserves the shortest useful evidence chain from the Flutter AOT
snapshot to each wire-level conclusion. Addresses are ARM64 AOT virtual
addresses in `lib/arm64-v8a/libapp.so`; string offsets are file offsets. The
analysis target SHA-256 is
`d5a6cf5bead911401b47f3ca96c94193eb872cf8150cea8342fdccb21a01e02f`.

The Dart private-name hash connects `_MainPageState@548070398` and the adjacent
`packetUtil`, `CONFIG`, and `VALUES` functions to
`package:hwcdq/modules/hwcdq`, rather than to one of the application's other
device modules.

## Function map

| AOT VA | Recovered function/purpose |
| ---: | --- |
| `0x3d2920` | packet receive/framing/checksum routine |
| `0x3d2b24` | `Ble.recv` stream accumulator |
| `0x3d2dc4` | shared packet send wrapper |
| `0x3d3160` | `Ble.send` and MTU fragmentation path |
| `0x3d5a2c` | `Ble.requestMtu` inner routine |
| `0x3d5f48` | `Ble.open_recv` notification setup call site |
| `0x3d72c4` | `ByteBloc.readFloat32` |
| `0x3dd2f8` | `ByteBloc.readUint16` |
| `0x3e85a4` | `_MainPageState@548070398.initState` |
| `0x3e8e44` | HWCDQ connection/handshake closure |
| `0x3ea344` / `0x3ea3b4` | get-config wrapper/async closure |
| `0x3ea540` | `CONFIG.fromByteBloc` |
| `0x3ea83c` / `0x3ea8a8` | get-serial wrapper/async closure |
| `0x3eaaa0` / `0x3eab50` | get-firmware wrapper/response parser |
| `0x3eacf0` / `0x3ead68` | password-check wrapper/async closure |
| `0x3eb190` / `0x3eb200` | get-values wrapper/async closure |
| `0x3eb38c` | `VALUES.fromByteBloc` |
| `0x4310d8` | per-device saved-credential lookup closure |
| `0x42fad8` | password-save callback: hash and persist credential |
| `0x3eb834` | user-password retry callback: hash and retry |
| `0x43db3c` | numeric `floatToIntBits` conversion |
| `0x475ae0` / `0x475b68` | generic `save_config` wrapper/async closure |
| `0x48743c` | output-control UI callback |
| `0x48757c` | set-current UI callback |
| `0x487790` | set-voltage UI callback |

## GATT UUID assignment

The init routine contains these loads/stores:

```asm
0x3e85e0  add x0, x27, 0x10, lsl 12
0x3e85e4  ldr x0, [x0, 0xce0]   ; PP+0x10ce0 = "...ffe1..."
0x3e85ec  str x0, [x1, 0x17b8]
0x3e85f0  add x0, x27, 0x10, lsl 12
0x3e85f4  ldr x0, [x0, 0xce8]   ; PP+0x10ce8 = "...ffe2..."
0x3e85fc  str x0, [x1, 0x17c0]
0x3e8600  add x0, x27, 0x13, lsl 12
0x3e8604  ldr x0, [x0, 0x2a0]   ; PP+0x132a0 = "...ffe3..."
0x3e860c  str x0, [x1, 0x17c8]
```

The object-pool decoder resolves the three comments exactly to the Bluetooth
base UUID forms of FFE1, FFE2, and FFE3. `Ble.open_recv` consumes the first two
and the enum string `notification`; `Ble.send` consumes the first and third and
the enum string `withoutResponse`. FFE0 resolves at PP+`0x10cd8`, but this init
routine does not load it. This proves the service/characteristic arguments and
Dart-side write intent. It does not prove the effective Android ATT write type:
the bundled Quick Blue bridge never applies that output-property argument with
`setWriteType`.

A later live CoreBluetooth discovery resolves the former FFE0/FFE1 conflict
for the sampled charger in favor of this AOT result. Diagnostic PID 73156,
sequence 166 records one FFE1 service containing FFE2 and FFE3. FFE2 advertised
indicate/notify/read/write/WNR; FFE3 advertised write/WNR; CoreBluetooth
reported WNR maximum 253 for both. The old desktop build's obsolete FFE0-only
selector then failed at sequence 173. That failure preceded
notification setup, authentication, and every application write. The redacted
record and log identity are preserved in `live-gatt-2026-08-25.md`.

## Scan discriminator

The object-pool string `HWCDQBLE_NIUB` has one target-code reference, at
`0x3cef08..0x3cef20` in `_HomePageState@545278425._anon_closure` (entry
`0x3cee58`). The closure calls `BlueScanResult.toMap` (`0x3d08cc`), retrieves a
value from that map, and passes `HWCDQBLE_NIUB` to a dynamically dispatched
boolean-returning method before `_HomePageState.addDevice` (`0x3cf8a8`). This
is strong evidence that the string is a scan-result discriminator.

The AOT call shape did not defensibly resolve the map key or distinguish
`contains`, equality, or another string predicate. It therefore does **not**
justify a hard advertisement-name or service-data filter. A client can scan
broadly, let the user select a device, and confirm the recovered GATT topology
after connection; `HWCDQBLE_NIUB` may be used only as a tentative ranking hint
until a passive advertisement capture identifies the exact field.

## Frame and checksum receiver

The beginning of the receive routine reads one byte, obtains its first list
element as the remaining length, then asks `Ble.recv` for that count:

```asm
0x3d2954  mov x1, 1
0x3d2958  bl  0x3d2b24          ; receive one byte
...
0x3d2978  stp xzr, x1, [x15]
0x3d2984  blr x30               ; index first byte
0x3d2988  sbfx x1, x0, 1, 0x1f ; untag Dart Smi length
0x3d2990  bl  0x3d2b24          ; receive exactly length bytes
```

The loop ends one element before the received list's length, loads each byte,
adds it, and stores the low byte in an accumulator:

```asm
0x3d29d0  sub  x4, x1, 1       ; last byte excluded from sum
0x3d29ec  cmp  x5, x4
0x3d2a2c  sbfx x1, x0, 1, 0x1f
0x3d2a34  add  x2, x0, x1
0x3d2a3c  strb w2, [x0, 0x17]  ; modulo 256
```

It then obtains the final byte and compares it to the accumulated byte at
`0x3d2ac8`. The length byte was consumed separately, so the covered range is
exactly opcode plus payload. This is an additive checksum, not one of the CRC
classes whose names occur elsewhere in the snapshot.

## Outgoing frame builder

The shared sender at `0x3d2dc4` independently establishes the encoder side of
the same format. The relevant shortened excerpt is:

```asm
0x3d2e30  ldur w1, [x2, 0x13]  ; payload list length (tagged)
0x3d2e38  sbfx x3, x1, 1, 0x1f ; untag payload length N
0x3d2e40  add  x4, x3, 2       ; remaining_length = N + 2
...
0x3d2eb0  sbfiz x0, x3, 1, 0x1f
0x3d2ed0  str   w0, [x25]       ; append remaining_length
...
0x3d2f3c  lsl   x1, x5, 1      ; tag caller opcode
0x3d2f44  stur  w1, [x6, 0xf]  ; append opcode
0x3d2f50  bl    0x276d60       ; growable-list addAll(payload)

0x3d2f58  ldrb  w1, [x0, 0x17] ; uint8 accumulator, initialized to zero
0x3d2f60  add   x3, x1, x2     ; add opcode
0x3d2f64  strb  w3, [x0, 0x17] ; retain low eight bits
...
0x3d2f88  ldrb  w4, [x0, 0x17] ; loop over payload
0x3d2f90  ldrb  w5, [x16, 0x17]
0x3d2f94  add   x6, x4, x5
0x3d2f98  strb  w6, [x0, 0x17] ; modulo 256
...
0x3d3018  lsl   x1, x3, 1      ; tag final checksum byte
0x3d3020  stur  w1, [x3, 0xf]  ; append checksum
...
0x3d3130  bl    0x3d3160       ; hand complete byte list to Ble.send
```

The builder appends the length before initializing/adding to the byte
accumulator, then adds only the opcode and all `N` payload bytes. This is direct
original-application evidence for `[N+2][opcode][payload][sum8]`; the literal
Python vectors are checks of that recovered behavior rather than its sole
basis.

## Generic setter and core opcodes

`save_config` checks whether its value is a string. A string takes the
`Utf8Encoder.convert` path and has a zero byte appended. All other values take
the integer path, which extracts four bytes after shifts 0, 8, 16, and 24.
The caller-supplied opcode is then passed to the shared sender. Its response
path considers a one-byte payload value `1` successful (`0x475e94..0x475ef8`).

The following immediate values are at the target UI call sites:

```asm
; set voltage callback, after double.parse and floatToIntBits
0x487848  mov x2, 7

; set current callback, after double.parse and floatToIntBits
0x487634  mov x2, 8

; output switch callback: current state is toggled, then int32 is sent
0x487490  ldur x1, [x2, #0x4f]  ; telemetry state
0x487494  cmp x1, #1
; 0x487498..0x4874ac selects argument 0 for raw state 1,
; and argument 1 for raw state 0
0x487500  mov x2, #0xc          ; opcode immediate
0x487504  bl 0x475ae0           ; generic four-byte setter
```

Thus the callback toggles raw `1` (OFF) to request value `0` (ON), and raw `0`
(ON) to request value `1` (OFF). The first direction is independently
live-confirmed by the 2026-08-25 incident; the second remains high confidence
because request value `1` has not yet been transmitted to the sampled charger.

Get-config and get-serial similarly load opcodes 5 and 4 in their async
closures (`0x3ea43c` and `0x3ea92c`). Get-values loads opcode 6, and the
firmware default path loads opcode 1. The credential-check path at
`0x3eadcc..0x3eaed8` UTF-8 encodes its supplied string, appends zero, and sends
opcode 2. The supplied string is already a 32-character MD5 digest; the
upstream derivation is recorded below.

The settings page provides these direct label/config-field → closure →
`save_config` call chains:

| Opcode | UI meaning | UI/config evidence | Closure → send call |
| ---: | --- | --- | --- |
| `0x09` | default/offline voltage | `0x478508` | `0x482544` → `0x4825fc` |
| `0x0a` | default/offline current | `0x4788c8` | `0x482330` → `0x4823e8` |
| `0x0b` | power-on output | `0x475fac`, config payload `+16` | `0x483db8` → `0x483e38` |
| `0x0d` | voltage calibration | `0x478c88` | `0x48211c` → `0x4821d4` |
| `0x0e` | voltage-feedback calibration | `0x47902c` | `0x481f08` → `0x481fc0` |
| `0x0f` | current calibration | `0x4793d0` | `0x481cf4` → `0x481dac` |
| `0x10` | current-feedback calibration | `0x479774` | `0x481ae0` → `0x481b98` |
| `0x11` | maximum voltage | `0x479e44` | `0x481194` → `0x48124c` |
| `0x12` | maximum single-module current | `0x47a204` | `0x480f80` → `0x481038` |
| `0x14` | automatic stop | `0x476308`, config payload `+41` | `0x483bb0` → `0x483c30` |
| `0x15` | shutdown current | `0x47665c`, config payload `+42` | `0x48399c` → `0x483a54` |
| `0x17` | temperature protection | `0x47a994` | `0x480b48` → `0x480c00` |
| `0x1a` | protection cut-off temperature | `0x47ad64` | `0x48092c` → `0x4809e4` |
| `0x1b` | fan boost temperature | `0x47b134` | `0x480710` → `0x4807c8` |
| `0x1c` | fan maximum-speed temperature | `0x47b504` | `0x4804f4` → `0x4805ac` |
| `0x20` | two-stage charging | `0x476a98`, config payload `+75` | `0x483794` → `0x483814` |
| `0x21` | secondary voltage | `0x477108` | `0x483344` → `0x4833fc` |
| `0x22` | secondary current | `0x47747c` | `0x483130` → `0x4831e8` |
| `0x23` | offline/button control | `0x47c748`, config payload `+84` | `0x47fa74` → `0x47faf4` |
| `0x26` | soft-start coefficient | `0x47cb34` | `0x47f858` → `0x47f910` |
| `0x27` | power limit | `0x47ce98` | `0x47f63c` → `0x47f6f4` |
| `0x29` | maximum power | `0x47a5c4` | `0x480d64` → `0x480e1c` |
| `0x2a` | display language | config payload `+91`, ASCII[8] | `0x47f2ec` → `0x47f35c` |

The floating-point UI closures use `double.parse` and `floatToIntBits` before
the generic four-byte integer writer. Boolean/integer closures pass their
integer value directly. The language closure passes a string, selecting the
UTF-8-plus-NUL path.

A raw ARM64 search for direct branches to `save_config` (`bl 0x475ae0`) found
46 call sites and this distinct opcode set:

```text
07 08 09 0A 0B 0C 0D 0E 0F 10 11 12 13 14 15 16 17 19
1E 20 21 22 23 24 25 26 27 28 29 2A 2B 2E 2F 30
```

This is the basis for the complete confirmed-call/unknown-semantic inventory
in `docs/commands.md`. In particular, there were no direct calls with `0x18`,
`0x1f`, `0x2c`, or `0x2d`; opcode gaps were not interpolated.

## Login credential derivation

The value sent by opcode `0x02` is not the password-field plaintext. The
closure at `0x4310d8` reads the per-device SharedPreferences JSON object keyed
by `deviceId`, then reads map key `password` at `0x431268..0x431290`. When the
entry is absent, `0x4312cc..0x4312dc` loads the exact uppercase constant:

```text
D41D8CD98F00B204E9800998ECF8427E
```

The selected string travels through the route at `0x4327a8..0x4327ac`, reaches
the handshake field load at `0x3e9094`, and is passed to `check_pass` at
`0x3e90a4`. `check_pass` (`0x3eacf0`, async closure `0x3ead68`) converts that
digest text to UTF-8 and appends NUL at `0x3eade4..0x3eaedc` before sending
opcode `0x02`.

When the operator enters a password, callback `0x3eb834` hashes its UTF-8 bytes
with MD5 at `0x3eb888..0x3eb8ac`; Dart `Digest.toString()` supplies lowercase
hex, and `0x3eb8f4..0x3eb900` retries with that digest. The save callback at
`0x42fad8` performs the same hash and stores the digest in JSON at
`0x42fb20..0x42fbfc`. No MAC address or serial bytes enter either hash.

The exact fallback request is:

```text
23 02 44 34 31 44 38 43 44 39 38 46 30 30 42 32 30 34
45 39 38 30 30 39 39 38 45 43 46 38 34 32 37 45 00 45
```

The final `45` is the additive checksum over opcode plus payload. Accepted and
rejected response frames are respectively `03 02 01 03` and `03 02 00 02`.
Both are static vectors; the accept frame was additionally received later by
monitoring PID 67357, while the reject frame remains uncaptured.

The AOT literal `0000` at object-pool offset `0x194a8` has a sole code xref at
`0x4cb158` inside `_BigIntImpl.toString`. It is unrelated to the HWCDQ
credential route and supplies no evidence of a factory password. The uppercase
fallback above is MD5(empty), but it is a bearer credential, not proof that the
user's plaintext password is blank. The sampled charger accepted this bearer
value live; that does not establish acceptance by another or reconfigured unit.

## Password-change path

`packetUtil.change_pass` is separate from the generic setter. At
`0x475548..0x4755dc`, both supplied strings go through UTF-8 conversion,
`Hash.convert`, and `Digest.toString`. The object-pool hash instance has Dart
class ID 3231, which resolves to `_MD5@123143612` after the VM predefined-class
offset. The resulting two strings populate `CHANGE_PASS`.

`CHANGE_PASS.toByteBloc` at `0x4758cc` calls `ByteBloc.writeString` twice with
Dart Smi immediate `0x24`, i.e. logical length 18. The writer pads or truncates
to that fixed length. The result is 36 bytes: first 18 lowercase MD5 hex
characters for the old password, followed by 18 for the new password. At
`0x4757f4..0x4757f8` the routine sends opcode 3. Its response path checks
opcode 3 and payload value 1.

The settings/device page closure at `0x47597c` passes a text-controller value
to `save_config` with immediate opcode `0x1e` at `0x475a04`. Its association
with the 23-byte configuration device string supports the high-confidence
Bluetooth/device-name interpretation, but that advertisement behavior was not
captured.

## Firmware-update exception

The updater is present but was never run. Its static path is materially
different from ordinary command traffic:

- `0x489c68..0x489d60` writes the firmware byte length as four little-endian
  bytes and sends a normal framed command with opcode 0;
- `0x4896d4..0x4898a4` slices firmware into logical blocks of at most 1024
  bytes and calls `Ble.send` directly, bypassing application framing;
- `Ble.send` still splits each block into MTU−3 writes;
- after each logical block the updater reads one raw byte through `Ble.recv`;
- after upload, the UI emits generic-setter opcode `0x28` with integer zero at
  `0x489348..0x489350`, followed by opcode `0x24`/zero at
  `0x489484..0x48948c`.

The direct calls were recorded so they are not mistaken for ordinary unknown
control packets. They are deliberately absent from the executable codec.

## Telemetry parser and UI mapping

`VALUES.fromByteBloc` consumes 46 bytes. The object-field-to-UI xrefs provide
the semantic map:

| Payload | Parser type | Object field | UI evidence |
| ---: | --- | ---: | --- |
| `+0` | f32le | `+0x07` | input voltage row `0x484220`, load `0x484278` |
| `+4` | f32le | `+0x0f` | input current row `0x484368`, load `0x4843c0` |
| `+8` | f32le | `+0x17` | input frequency row `0x4849c8`, load `0x484a20` |
| `+12` | f32le | `+0x1f` | first temperature row `0x4852a4`, load `0x4852fc` |
| `+16` | f32le | `+0x27` | second temperature row `0x485520`, load `0x485598` |
| `+20` | f32le | `+0x2f` | output voltage row `0x4845e4`, load `0x48464c` |
| `+24` | f32le | `+0x37` | output current row `0x484748`, load `0x4847b0` |
| `+28` | f32le | `+0x3f` | current point row `0x485160`, load `0x4851b8` |
| `+32` | f32le | `+0x47` | efficiency row `0x484b10`, load `0x484b68` |
| `+36` | u8 | `+0x4f` | Open/Close row `0x486a20`, load `0x486ad4` |
| `+37` | f32le | `+0x57` | `Ah` suffix at `0x485aa4` |
| `+41` | f32le | `+0x5f` | `Wh` suffix at `0x485b88` |
| `+45` | u8 | `+0x67` | module count row `0x485678`, load `0x485720` |

The input-power UI multiplies object `+0x07 × +0x0f`; output power multiplies
`+0x2f × +0x37`. Those values are derived, not extra wire fields.

## Execution boundary

The APK/native libraries were not executed, and no emulator or Frida session
was started. Static reconstruction did not access Bluetooth. A later packaged
desktop-client run passively scanned, connected, and discovered the sampled
charger's GATT table. Its obsolete FFE0-only selector stopped
at topology selection before subscribing, authenticating, or transmitting any
application packet. The test suite's telemetry frame remains synthetic and is
labelled as such. Static vectors come from the original builder and are
independently asserted in the codec tests.

A subsequent independent desktop-client incident transmitted the known
opcode-`0x0C` int32le-zero vector and live-confirmed its ON meaning; it did not
execute the APK or its native libraries. That chain and its strict boundary are
preserved in `live-gatt-2026-08-25.md`.
