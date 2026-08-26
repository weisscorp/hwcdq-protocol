# Pidzoom Portable charger HW178P

Pidzoom Portable charger HW178P is a native Qt Widgets application. It does not open a
browser, run an HTTP server, contact a vendor cloud, or scan Bluetooth until the
operator presses **Сканировать**.

The desktop frontend imports the same canonical `hwcdq` library available to
other clients. The Qt UI, CLI, preferences, and desktop reconnect orchestration
remain in `hwcdq-control`; packet encoding, parsing, GATT selection, safety
profile, and charger session live in the Qt-free `packages/hwcdq-client`
distribution. See [`library.md`](library.md).

## Modes

| Mode | Invocation | State-changing commands |
|---|---|---|
| Simulation monitoring | `.venv/bin/hwcdq-control --simulate` | All mutations blocked |
| Simulation control | `.venv/bin/hwcdq-control --simulate --enable-output-controls` | Confirmed subset against the in-process fake charger |
| Live monitoring | `Pidzoom Portable charger HW178P.app` through LaunchServices | All mutations blocked; authentication/read requests only |
| Live control | The same bundle with `--enable-output-controls` | V/I/Start/Stop allowed only when their interlocks pass |

Append `--debug` to any invocation to record an opt-in local diagnostic trace.
Append `--debug-log PATH` as well to override the default. The development
launcher defaults to `logs/hwcdq-debug.jsonl` under its current directory; the
frozen bundle defaults to the intentionally retained legacy path
`~/Library/Logs/HWCDQ Bench Control/hwcdq-debug.jsonl`. It remains unchanged so
existing local traces continue in one place after the visible product rename;
it is not the current application name. The custom-path flag is invalid by
itself. The window shows a DEBUG badge with the filename (and the full path in
its tooltip), while the direct development launcher prints the absolute path to
stderr. Debug mode does not change the simulation, monitoring, or control
policy. See [`debugging.md`](debugging.md).

The simulator advertises one device named `HWCDQBLE_NIUB (симулятор)`. Leave
its password field empty: the simulator expects the APK fallback credential
selected by empty input. This tests the same derivation/framing path as live
mode; it is not a claim that a hardware plaintext password is blank. Its frames
are generated and decoded by the same codec and session machinery used in live
mode.

## macOS bundle and Bluetooth permission

Keep using the virtual-environment executable for simulation, development, and
tests. Do not launch a live CoreBluetooth scan with
`.venv/bin/hwcdq-control`. The first attempted live scan under the development
Homebrew `Python.app` was terminated by macOS TCC because that host application
has no `NSBluetoothAlwaysUsageDescription`. Its diagnostic trace ended at
`scan_started`, before any advertisement, connection, authentication, or
charger packet was observed.

Build the application that owns its Bluetooth privacy metadata:

```sh
.venv/bin/python -m pip install --no-deps -e packages/hwcdq-client
.venv/bin/python -m pip install -e '.[macos-app]'
.venv/bin/python tools/build_macos_app.py build
.venv/bin/python tools/build_macos_app.py verify
```

This creates `dist/Pidzoom Portable charger HW178P.app` for arm64 with bundle identifier
`cc.hwcdq.bench-control`, minimum macOS version 26.0, and both
`NSBluetoothAlwaysUsageDescription` and the compatibility
`NSBluetoothPeripheralUsageDescription`. The stated purpose is discovery of
and communication with the HWCDQ charger. The bundle is ad-hoc signed for
local use on the current machine; it is not Developer ID signed, notarized, or
a distributable release. The committed build inputs make it repeatably
buildable in this environment; byte-for-byte-identical bundle output is not
claimed. Because the signature is ad hoc, macOS may ask for Bluetooth consent
again after the bundle is rebuilt. The bundled Homebrew Python runtime requires
macOS 26.0; the artifact is verified on macOS 26.5.2, not on macOS 13–15.

`verify` is deliberately offline. It performs all of the following without
constructing a scanner or accessing a peripheral:

- `plutil` lint and exact `Info.plist` key, value, executable, and identifier
  validation;
- deep/strict code-signature validation, exact signing identifier, ad-hoc
  signature, and absent Team ID checks;
- audit of every bundled Mach-O for thin arm64 and a deployment target no newer
  than the declared macOS 26.0 minimum;
- PyInstaller warning audit for required CoreBluetooth modules;
- frozen `--version` and import-only CoreBluetooth backend self-checks;
- Qt Cocoa platform-plugin presence check;
- offscreen startup of the frozen simulator until the debug journal contains
  `window_shown`, followed by controlled termination.

Passing these checks means the package is a structurally valid candidate. It
does not confirm that the macOS permission prompt, CoreBluetooth radio access,
discovery, or communication with this charger works. Live confirmation requires
the exact LaunchServices run below, accepting the permission prompt, and one
scan that does not abort:

```sh
open -n "$PWD/dist/Pidzoom Portable charger HW178P.app" \
  --args --debug \
  --debug-log "$PWD/logs/charger-live.jsonl" \
  --scan-seconds 8
```

Choose **Allow** when macOS asks for Bluetooth access. Do not add
`--enable-output-controls` to this first monitoring run. Use an absolute debug
path because LaunchServices does not promise the shell's working directory.

## Window layout

- **Рабочая панель** contains device selection, exact telemetry, configuration,
  setpoints, interlock reasons, and one fixed header output control. It offers
  Start only for fresh explicit OFF and becomes immediate Stop for fresh
  explicit ON.
- **GATT и транспорт** shows every discovered service/characteristic, property
  set, the selected sibling FFE2/FFE3 pair, fixed write mode, and WNR chunk
  limit.
- **Журнал пакетов** shows TX/RX direction, opcode, semantic decoding, and raw
  bytes. Authentication contents are removed before the UI receives them and
  are defensively redacted again during copy/export.

## Connection sequence

1. Scan and select an explicit peripheral. After a successful authenticated
   connection, the application remembers only its machine-local CoreBluetooth
   UUID. On later scans an exact UUID match is preselected; the application
   never connects automatically, and a manual selection is never overwritten
   by a later advertisement.
2. Connect and enumerate the complete GATT topology.
3. Accept only one FFE1 service containing exactly one notify-capable FFE2 and
   one writable FFE3. A usable-looking pair under any other parent is rejected;
   RX and TX are never paired across services.
4. Prefer FFE3 `write-without-response` when advertised; use acknowledged
   `write` only as a compatibility fallback. The choice stays fixed until
   reconnect and is never changed in reaction to a timeout.
5. Subscribe to FFE2 and preserve the recovered one-second settling delay.
6. Derive/send the MD5 credential, then perform firmware, serial,
   configuration, and telemetry requests serially.
7. Maintain read-only state only while no operator transaction owns the
   protocol slot: wait five seconds after each completed maintenance exchange,
   normally request telemetry, and substitute a configuration request when its
   60-second refresh is due. Do not send a config+telemetry burst back-to-back.

The APK and sampled charger agree on FFE1. Live diagnostic PID 73156, sequence
166 first found FFE2 and FFE3 as siblings under FFE1; FFE2 advertised
indicate/notify/read/write/WNR and FFE3 advertised write/WNR, both with a
CoreBluetooth WNR maximum of 253. That obsolete build rejected the table at
sequence 173 because it still demanded FFE0. A corrected monitoring build then
completed FFE2 subscription and application authentication in PID 67357 and
received firmware, serial, 103-byte configuration, and 127 valid 46-byte
telemetry payloads. The client still fails closed on missing or ambiguous
topology.

That live build selected acknowledged writes. On its final telemetry request,
the FFE3 write completion callback failed to arrive within three seconds even
though this same session had repeatedly received valid FFE2 replies. Since the
sampled FFE3 explicitly offers WNR and these requests fit within the reported
253-byte maximum, the rebuilt client prefers WNR to avoid depending on that
CoreBluetooth acknowledgement callback. This is an evidence-based transport
policy for the independent client, not proof of the original Android ATT type.
Real-device WNR durability is still **awaiting a bounded monitoring-only
soak**; it is not claimed verified.

The operator enters plaintext only at the local dialog. The client derives
lowercase MD5 hex from nonempty UTF-8 input, then sends those 32 ASCII hex
characters plus NUL. Empty input intentionally selects the APK's exact
uppercase fallback `D41D8CD98F00B204E9800998ECF8427E`. That fallback is a
bearer credential, not evidence that a hardware password is blank.

The dialog is deliberately concise: title `Доступ к HWCDQ`, explanation
`Пароль приложения (не Bluetooth PIN). Пусто — ключ из APK. Не сохраняется.`,
and buttons `Подключиться` / `Отмена`. The application stores no plaintext
password or derived credential. Its machine-local `QSettings` preference
contains only the last successfully authenticated real peripheral's
CoreBluetooth UUID under key `ble/lastDeviceIdentifier`; serial, advertised
name, telemetry, and other device data are not persisted. Simulator
connections do not read or replace that preference. Because CoreBluetooth UUIDs
are machine-local and can become stale, a missing exact match merely leaves the
operator to choose a current scan result; it never triggers auto-connect. A
failed connection or rejected credential also leaves the prior preference
unchanged.

If framing fails while CoreBluetooth still reports a live link, the session is
marked as an error and authentication is invalidated. Scan and Connect remain
blocked, while Disconnect stays available until the physical BLE link closes.
The same rule applies when native connection cancellation or teardown fails:
logical completion never hides a still-live CoreBluetooth link, and each
explicit Disconnect retries native teardown.

## HW178P model envelope

The visible application is deliberately model-specific:

| Target | HW178P application envelope | Provenance |
| --- | --- | --- |
| Voltage | `50.00…178.00 V` | exact model and `50 V` minimum supplied by the owner on 2026-08-25; sampled live `0x05` configuration independently reports the `178 V` maximum |
| Current | `0.01…14.00 A` | sampled live `0x05` configuration reports the `14 A` maximum; `0.01 A` is a conservative positive client floor, not a hardware-proven minimum |

For each connected session, the effective upper bound is the smaller of the
model maximum and the valid maximum reported by opcode `0x05`. A narrower
device maximum always wins; a larger device value cannot expand the HW178P
envelope. The protocol carries no model-name or minimum-voltage field, so the
UI must not present the owner-supplied identity or `50 V` floor as decoded BLE
data.

The canonical reusable package is `hwcdq`. The desktop and deprecated
compatibility names remain `hwcdq_control`, `hwcdq-control`, and HWCDQ. The
macOS bundle identifier remains
`cc.hwcdq.bench-control` as a best-effort continuity measure. Because the local
bundle is ad-hoc signed, macOS may still request Bluetooth permission again
after a rebuild; continuity is not guaranteed.

## Control interlocks

Voltage/current/Start require all of the following:

- process started with `--enable-output-controls`;
- connected and application-layer authenticated session;
- fresh, finite configuration and telemetry;
- valid positive device maxima;
- voltage within `50.00…min(max_voltage, 178.00) V`;
- current within `0.01…min(max_single_module_current, 14.00) A`, without
  multiplying by module count;
- no unresolved state-changing command;
- successful setter ACK followed by configuration readback;
- Start dialog confirming the displayed exact V/I and safe physical load
  connection. This dialog is the operator's confirmation; there is no separate
  V/I acceptance button.

Accepting that dialog does not immediately trust its earlier snapshot. In the
same serialized transaction that may transmit Start, the session rereads
configuration and telemetry, compares the confirmed V/I to the current device
targets by their canonical little-endian IEEE-754 binary32 bytes, reruns the
full limits and telemetry safety checks, and requires fresh explicit
`output_enabled == false`. A failed recheck sends no Start (`0x0C`) write.

Stop requires explicit `--enable-output-controls`, an authenticated live
connection, and fresh telemetry explicitly reporting output ON. It does not
require fresh configuration, Start readiness, valid setpoint limits, or a modal
confirmation. It cancels polling and takes the next priority transaction slot.
Monitoring, stale/unknown telemetry, and already-OFF telemetry disable both the
button and shortcut. If delivery or readback cannot be established, the UI
reports an unknown or undelivered result.

Queue wait is recorded separately from protocol execution. Once a transaction
owns the serializer, its native write phase has a three-second deadline. Only
after every write chunk completes does a fresh eight-second application
response deadline begin. A mutation ACK and its verifying readback are two
separate exchanges, so each receives its own write/response deadlines.

If a native write fails, times out/resists cancellation, or if a transmitted
request receives no response by its deadline, the BLE client is marked
unusable. The frame has no sequence field, so a late same-opcode response could
otherwise be mistaken for a later request. The session therefore invalidates
the callback generation before best-effort teardown, discards late fragments,
and publishes another snapshot after the native link closes. No second
application write is allowed on that client, including a queued Stop.

For a read-only maintenance failure, the controller visibly attempts a clean
reconnect after 1, 2, and 5 seconds, stopping after three failures. A successful
attempt creates a new client and reruns authentication, firmware/serial,
configuration, and telemetry before polling resumes. Explicit Disconnect and
application shutdown atomically cancel the recovery intent. Mutations are
never replayed and never cause this automatic continuation; their outcome
remains unknown until independently established.

This reconnect supervisor belongs to the desktop controller, not to
`hwcdq.ChargerSession`. The reusable session represents one connection and
never persists a device identifier, scans for an absent charger, or reconnects
on its own. A future Home Assistant coordinator must own those policies and
must preserve the same no-mutation-replay rule.

A manual Connect first disconnects the prior logical session, then visibly
waits until its native teardown and every cancellation-resistant write have
finished. Only then may it construct another CoreBluetooth client. This gate
also applies after an ambiguous mutation, so a fresh handshake can never
overlap the old command.

## Threading model

Qt remains on the main macOS thread. A dedicated Python thread owns one asyncio
event loop, Bleak scanner/client objects, the charger session, and all packet
sequencing. Cross-thread results enter Qt through queued signals. There is only
one outstanding application opcode because the protocol has no sequence
number.

## Diagnostic mode

Diagnostic mode records semantic UI actions, controller state, the transaction
queue, GATT selection, BLE framing, writes and notifications, ACK/readback
decisions, maintenance cadence, reconnect scheduling/backoff/results,
discarded stale-generation callbacks, ambiguous outcomes, errors, and
shutdown. It is a thread-safe JSONL
trace, not a raw keyboard recorder: printable characters, password input,
password focus/length, and clipboard contents are never recorded. Password
opcode `0x02` events retain only `opcode: 2` and `redacted: "[REDACTED]"` in
their `details` object.

The active file rotates at 5 MiB with three backups, and every event is flushed
as a complete line. On POSIX systems the direct log directory must be private
mode `0700`; log files and rotated backups are mode `0600`. Existing regular
files are appended to. Symlinks, non-regular targets, unsafe directory modes,
and write failures disable diagnostics without changing BLE sequencing or
output-control behavior. The DEBUG badge exposes a logging failure so the
operator does not mistake a partial trace for complete evidence. Full schema,
event inventory, and collection guidance are in [`debugging.md`](debugging.md).

## Known live-validation gap

The sampled charger has now confirmed macOS Bluetooth access, its FFE1
advertisement, connection, complete FFE1/FFE2/FFE3 topology, FFE2 notification
subscription, fallback-credential acceptance, firmware/serial/configuration
replies, checksum framing, and repeated telemetry. A representative live frame
with `payload[36] = 1` coincided with the user's physical display and explicit
confirmation that output was OFF; together with the AOT Open/Close branch this
corrects that byte to raw `0`=ON, `1`=OFF, other=unknown. A later accidental
monitoring-mode transmission live-confirmed opcode `0x0C` int32le `0` as
Start/ON. AOT toggle analysis gives int32le `1` as Stop/OFF with high
confidence, but that value has not yet been transmitted to the sampled charger.

No live command has yet supplied the required two voltage vectors, two current
vectors, or Stop readback evidence. The rebuilt WNR transport
policy also has not completed its bounded user-run read-only soak. Simulator
success and offline bundle verification do not close either gap.
