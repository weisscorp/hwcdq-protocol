# First live-device testing checklist

This checklist is intentionally staged. Do not fuzz, brute-force, send unknown
opcodes, or modify charger firmware. Change only one parameter at a time.

## 0. Prove the application offline

```sh
.venv/bin/hwcdq-control --simulate --enable-output-controls --debug
```

Leave the simulator password field empty so it selects the exact APK fallback
credential. This is a protocol fixture, not a claim of a blank hardware
password. Verify scan, connect, GATT view, telemetry,
set-voltage readback, set-current readback, the one header button's OFF→Start
and ON→Stop states, exact V/I confirmation in the Start dialog, and immediate
Stop. The launcher must print the diagnostic path and the window
must show a healthy DEBUG badge. Export the packet journal and inspect the
diagnostic JSONL plus any rotated `.1`–`.3` files. Confirm that the fallback
credential `D41D8CD98F00B204E9800998ECF8427E`, its lowercase form, and its ASCII
hex rendering do not appear, and that opcode `0x02` diagnostic details contain
only `opcode` and `[REDACTED]`.

## 0.1 Build and verify the live-device candidate

The direct virtual-environment launcher remains appropriate for the simulator,
development, and tests, but it must not be used for live CoreBluetooth. On the
first attempted live scan, macOS TCC terminated the Homebrew `Python.app`
because it has no `NSBluetoothAlwaysUsageDescription`. The trace reached
`scan_started` but contained no advertisement, connection, authentication, or
charger packet.

Build a local application bundle that owns the required Bluetooth privacy
metadata:

```sh
.venv/bin/python -m pip install --no-deps -e packages/hwcdq-client
.venv/bin/python -m pip install -e '.[macos-app]'
.venv/bin/python tools/build_macos_app.py build
.venv/bin/python tools/build_macos_app.py verify
```

The output is the arm64, ad-hoc-signed
`dist/Pidzoom Portable charger HW178P.app` for this machine, with identifier
`cc.hwcdq.bench-control` and minimum macOS version 26.0. It is not notarized,
Developer ID signed, or intended for distribution. A rebuild may cause macOS
to request Bluetooth consent again because the signature is ad hoc. The
bundled Homebrew Python sets this minimum; the artifact is verified on macOS
26.5.2 and is not compatible with macOS 13–15.

The verifier is an offline structural test: it lints and validates the
`Info.plist` Bluetooth keys and identifier, verifies the code signature
deep/strict plus identifier/ad-hoc/no-Team-ID properties, audits every Mach-O
as thin arm64 with deployment target at most macOS 26.0, audits PyInstaller
warnings, runs frozen version and import-only CoreBluetooth backend checks,
finds the Qt Cocoa platform plugin, and starts the frozen simulator offscreen
through `window_shown`. It does not instantiate a BLE scanner or contact the
charger. Passing it does not yet prove that live Bluetooth permission or
discovery works.

## 1. Passive discovery — completed once

1. Keep the charger output physically de-energized or connected only to a load
   setup the operator has independently judged safe.
2. Launch the packaged application through LaunchServices in monitoring mode,
   with no `--enable-output-controls`:

   ```sh
   open -n "$PWD/dist/Pidzoom Portable charger HW178P.app" \
     --args --debug \
     --debug-log "$PWD/logs/charger-live.jsonl" \
     --scan-seconds 8
   ```

   Use this exact absolute bundle and log path for the first retry. Do not
   substitute `.venv/bin/hwcdq-control`.
3. When macOS first asks for Bluetooth access, choose **Allow**. Denial is a
   permission result, not protocol evidence.
4. Press **Сканировать** once and record name, CoreBluetooth identifier, advertised
   services, and RSSI.
5. Do not connect if the selected peripheral identity is uncertain.

Scanning sends no application packet. The 2026-08-25 packaged run completed
this phase: the selected charger advertised FFE1, the app remained alive, and
CoreBluetooth connected successfully. Preserve future repeats as additional
device/firmware evidence rather than overwriting the original log.

The packaging/TCC path and scanning are therefore live-confirmed on this Mac.
That result alone does not validate an application packet. Preserve the JSONL
even when no matching advertisement appears, and distinguish `scan_failed`
from a process abort.

## 2. Monitoring connection — read-only path completed once

The first attempt reached GATT discovery but stopped before authentication: log
PID 73156 sequence 166 found FFE1 with sibling FFE2/FFE3, and the obsolete
FFE0-only selector rejected it at sequence 173. The corrected build then
completed FFE2 subscription and fallback authentication in PID 67357, received
firmware/serial/configuration, and decoded 127 checksum-valid telemetry frames.
No voltage/current/Start/Stop command was sent.

1. Select the charger and press **Подключить…** once. For the current
   password-unknown run, leave the authentication field empty and submit it
   once. The client then sends the APK's exact uppercase fallback
   `D41D8CD98F00B204E9800998ECF8427E`; it does not send a blank plaintext
   password. This fallback attempt is not proof of a blank factory password.
2. If the fallback is rejected, the response is malformed, or the request
   times out, disconnect/quit and stop this run. Do not retry another value and
   do not brute-force it. If the credential was changed, recover the saved
   digest from the owner's original Android app data or a passive opcode-`0x02`
   capture. On a later run with a genuinely known application password, the
   client hashes nonempty UTF-8 input to lowercase MD5 hex before sending.
3. In **GATT и транспорт**, record:
   - actual parent service UUID;
   - all FFE1/FFE2/FFE3 properties;
   - selected write mode;
   - WNR maximum size if relevant.
   Require one FFE1 parent, a notify-capable FFE2 RX, and writable FFE3 TX. The
   sampled device advertises both `write` and WNR on FFE3, so the rebuilt
   reference client fixes WNR for this connection; acknowledged write is only
   the fallback when WNR is absent. Any different or ambiguous topology is
   evidence to preserve and a reason to stop.
4. Confirm the UI receives firmware, serial, 103-byte configuration, and
   46-byte telemetry responses with valid checksums.
5. Compare displayed voltage/current/temperatures against independent
   instruments or the charger display where practical.
6. Export the redacted journal and annotate it as a live capture.
7. Quit normally, then preserve the diagnostic file and all rotated siblings
   (`.1` through `.3`) together. Record the wall-clock interval and whether the
   UI ever displayed a DEBUG logging error.

The next run is specifically a **bounded 10-minute read-only WNR soak**. Leave
`--enable-output-controls` absent. Confirm telemetry remains approximately five
seconds after the previous completed maintenance exchange and that
configuration is reread around once per 60 seconds. If a read-only response
times out, the old sequence-less link must be torn down and the UI may visibly
retry after 1, 2, and 5 seconds, no more than three attempts. A recovered
session must repeat authentication → firmware/serial → configuration →
telemetry before reporting ready. Pressing Disconnect or quitting during
backoff must cancel every remaining attempt.

If the operator presses Connect again after an error, the UI may display that
it is waiting for the previous BLE client. No new handshake may begin until
the old native teardown and any unfinished write have completed; do not quit or
repeat Connect merely to bypass that visible wait.

This WNR soak has **not yet been performed**. The current evidence proves only
the characteristic's advertised WNR capability and the earlier acknowledged
write/application traffic; do not label WNR durability as hardware-verified
until the bounded run succeeds and its diagnostic is preserved.

If framing, checksum, topology, or response matching fails, use **Отключить**
and preserve the log. Do not guess a new byte boundary or switch write type
automatically. In a protocol-error state, the UI keeps Scan/Connect blocked and
Disconnect available for as long as CoreBluetooth reports the link as live.

If either the three-second native-write deadline or the separate eight-second
application-response deadline expires, the current BLE client is invalidated.
The application will not send another command on that link; late callbacks are
discarded. Read-only maintenance may create a clean client through the bounded
reconnect sequence above. A mutation is never replayed and never triggers that
automatic continuation. For a failed Stop, the operator must treat Stop as
**not transmitted** and use the established physical safe-stop procedure.

Monitoring mode must not expose or transmit any mutation, including Stop. If a
`0x07`, `0x08`, or `0x0C` TX frame appears without
`--enable-output-controls`, close that build and preserve the trace as a safety
failure; do not continue testing with it.

## 3. Setpoint readback with output off

Only proceed after phase 2 is repeatable and the displayed device limits are
finite and credible.

1. Restart the same packaged `.app` through LaunchServices with
   `--enable-output-controls --debug` (and an absolute private `--debug-log`
   path, following the same policy used during monitoring). Do not use the
   direct virtual-environment executable for live control.
2. Confirm telemetry reports the output off.
3. Choose a voltage within both the operator's independently safe range and
   the application range `50.00…min(max_voltage, 178.00) V`. Write it once and
   require matching configuration readback.
4. Change voltage to a second safe value and repeat.
5. Choose a current within `0.01…min(max_single_module_current, 14.00) A` and
   no greater than the operator's independently safe value. Do not multiply the
   single-module maximum by module count. Write it once and require readback.
6. Change current to a second safe value and repeat.

A timeout/disconnect after transmission means **unknown outcome**. Do not press
the setter again. Use readback or reconnect to establish state.

## 4. Energized output-control validation

This phase requires the operator's own safe battery/load, appropriate fusing,
independent voltage/current observation, and knowledge of the charger's limits.
The application cannot determine whether the physical connection is safe.

The preserved PID 7487 incident already live-confirmed exact frame
`06 0C 00 00 00 00 0C` as Start/ON: acceptance ACK was followed by telemetry
raw zero and an output-voltage ramp to 134.112 V. Do not repeat Start merely to
reconfirm that polarity.

Stop frame `06 0C 01 00 00 00 0D` remains high confidence from the AOT toggle
callback and has not yet been live-transmitted. Any staged Stop validation must
use only a rebuilt/offline-verified package and this sequence:

1. Launch with `--enable-output-controls`; monitoring mode must keep Stop
   disabled.
2. Establish a physically safe ON state independently and require fresh
   telemetry raw zero. Stop does not require setpoint validity or Start
   readiness.
3. Press **Остановить выход** once. It has no confirmation dialog.
4. Preserve the exact TX and `03 0C 01 0D` acceptance ACK, if returned.
5. Require later `0x06` telemetry raw one/OFF and verify de-energization
   independently before upgrading Stop to confirmed.

If BLE disconnects, the UI cannot claim Stop was delivered. Use the charger's
physical controls or remove energy using the operator's established safe
procedure; do not rely on a disconnected desktop application.

## Evidence to bring back into the specification

- Raw redacted TX/RX frames with action timestamps.
- Repeated service/characteristic hierarchy and properties, compared with the
  already preserved FFE1/FFE2/FFE3 capture.
- Write-with-response versus WNR behavior.
- Notification chunk boundaries and response latency.
- Two safe voltage values and their readbacks.
- Two safe current values and their readbacks.
- Preserve the existing Start incident chain and add a Stop ACK plus telemetry
  confirmation before upgrading Stop confidence.
- Any difference between the charger display and decoded telemetry.

These observations should update confidence labels in `docs/commands.md` and
`docs/protocol.md`; simulator packets alone must not upgrade them.

The packaged app, macOS permission path, FFE1 parent, FFE2 RX notifications,
FFE3 TX, credential ACK, firmware/serial/configuration, and telemetry are
hardware-verified on the sampled charger. The live state byte `1` is confirmed
OFF by AOT plus the charger display/owner observation. Opcode `0x0C` int32le
zero is live-confirmed Start/ON; int32le one is high-confidence Stop/OFF from
AOT but awaits live ACK/readback. WNR long-run durability, setters, and Stop
electrical behavior remain unverified. A
simulator trace proves instrumentation and state transitions only.
