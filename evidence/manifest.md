# Evidence manifest

All offsets below are hexadecimal file offsets unless explicitly identified as a
virtual address. The original APK and expanded binaries are retained locally
under `artifacts/` and intentionally ignored by Git.

## Acquisition

- Acquisition completed: `2026-08-24T10:07:09Z` (APK file mtime after the
  completed transfer).
- Source page: `https://niub.cc/connect?type=hwcdq&mac=<redacted>&sn=<redacted>`.
- Android link exposed by that page:
  `https://niub.cc/index/download/index/type/app`.
- Saved path: `artifacts/original/hwcdq.apk`.
- Size: `9,460,472` bytes.
- SHA-256:
  `c7ee52a5e348982a0a4f51b6640b325cfe03c2f4b65a02860c2198a681c9263f`.
- `unzip -t` result: no errors in compressed data.
- The query-bearing landing page, redirects, cookies, and device identifiers
  were not persisted.

## APK identity

`aapt2 dump badging` reports:

- package: `com.y.hw.charge`;
- application label: `HW Smart Charger` (`HW智能充电器` in Chinese resources);
- version code/name: `1` / `1.7.5`;
- compile/target SDK: `33` / `33`;
- minimum SDK: `26`;
- launch activity: `com.example.flutterapp.MainActivity`;
- native ABIs advertised by the package: `arm64-v8a`, `armeabi-v7a`.

The APK Signing Block begins at `0x901cac`, is 8,192 bytes including the first
size word, and contains an APK Signature Scheme v2 entry (`0x7109871a`). The
first signer certificate is 843-byte DER with:

- SHA-256 fingerprint:
  `06:DC:E8:AC:8E:20:DD:09:20:89:85:B6:32:19:79:69:31:9C:A6:70:ED:52:95:5F:20:41:D4:4D:00:C9:B3:06`;
- subject/issuer: `C=jzy, ST=jzy, L=jzy, O=jzy, OU=jzy, CN=jzy`;
- serial: `1F619AB7`;
- validity: `2018-05-01T14:19:14Z` through `2043-04-25T14:19:14Z`.

The fingerprint was calculated directly over the DER certificate extracted
from the v2 signer block, then independently printed by OpenSSL 3.6.3.

## Primary binaries

| Path | Size | SHA-256 | ELF build ID |
| --- | ---: | --- | --- |
| `classes.dex` | 1,870,332 | `10a831e42d575115c4cb9269a2ccb560f76ee90a545462145c45cdf791882f8d` | n/a |
| `lib/arm64-v8a/libapp.so` | 6,423,472 | `d5a6cf5bead911401b47f3ca96c94193eb872cf8150cea8342fdccb21a01e02f` | `083986ec7431003d208427162202564c` |
| `lib/arm64-v8a/libflutter.so` | 10,601,880 | `1de52bc02299f1bab74d529ae294f5a3a66d0ce6769085963fe783eb3c85f942` | `ad074c0ef7b18624be1126cf4eb7343ec826e01c` |
| `lib/arm64-v8a/libscannative.so` | 489,704 | `82cd975080532471754075fa32943467ae6c20692c191a69149640397dd690ee` | `f82564650a021f74ac15ca843a708b261c7fb734` |
| `lib/armeabi-v7a/libscannative.so` | 308,968 | `f6a5abd1149e510aee02f1afedbdd2e7ae205bf9208bd0e2f58e414bdc2dc990` | `a051aef0ff807983120e7ff653a9017fadbdb92b` |

Only `libscannative.so` has an `armeabi-v7a` copy. The Flutter application and
engine snapshots are ARM64 only.

## Flutter asset inventory

`assets/flutter_assets/` is present and was retained in the ignored unpacked
APK. It contains only the generated manifests/notices, the standard Material
and Cupertino icon fonts, and Flutter's stock ink-sparkle shader:

| Relative path | Size |
| --- | ---: |
| `AssetManifest.bin` | 117 |
| `AssetManifest.json` | 109 |
| `FontManifest.json` | 208 |
| `NOTICES.Z` | 86,629 |
| `fonts/MaterialIcons-Regular.otf` | 1,645,184 |
| `packages/cupertino_icons/assets/CupertinoIcons.ttf` | 283,452 |
| `shaders/ink_sparkle.frag` | 17,304 |

`AssetManifest.json` names only the Cupertino icon font. There is no bundled
configuration, packet table, firmware image, or other application data asset;
the protocol implementation resides in the AOT `libapp.so` snapshot.

## Flutter/Dart identification

`libflutter.so` contains engine revision
`55eae6864b296dd9f43b2cc7577ec256e5c32a8d` at file offset `0x1da8b0`.
The official Flutter `3.22.1` tag pins exactly that revision in
`bin/internal/engine.version`. That engine revision pins Dart SDK commit
`b0e7f171c7c9ab82944ffd6918b80a0df36e0549`; the SDK's official
`tools/VERSION` identifies it as Dart `3.4.1`. Snapshot parsing additionally
reported snapshot hash `d20a1be77c3d3c41b2a5accaee1ce549`, Android ARM64,
compressed pointers, and sound null safety.

Confidence: **confirmed** for the engine revision and Dart commit; **high** for
the Flutter marketing version because the tag-to-engine mapping is exact.

## High-value AOT strings

The following strings are in `libapp.so` `.rodata` (whose VMA equals its file
offset in this ELF):

| Offset | String |
| ---: | --- |
| `0x296cc` | `package:hwcdq/Utils/ble.dart` |
| `0x2f91a` | `package:hwcdq/modules/hwcdq/Utils/packetUtil.dart` |
| `0x30351` | `HWCDQBLE_NIUB` |
| `0x3f1d1` | `0000ffe0-0000-1000-8000-00805f9b34fb` |
| `0x66756` | `0000ffe1-0000-1000-8000-00805f9b34fb` |
| `0x3b2c5` | `0000ffe2-0000-1000-8000-00805f9b34fb` |
| `0x39c88` | `0000ffe3-0000-1000-8000-00805f9b34fb` |
| `0x67a71` | `appendCRC` |
| `0x7f593` | `package:hwcdq/Utils/CRCUtil.dart` |
| `0x384b8` | `Crc32IsoHdlc` |
| `0x55683` | `Crc32Xz` |

These strings establish presence, not behavior. Characteristic roles and CRC
parameters require call-site/object-pool evidence recorded separately in the
protocol report.

## Android BLE bridge

`classes.dex` contains the Quick Blue Flutter plugin, registered as
`com.example.quick_blue.QuickBluePlugin`. Its channels are:

- `quick_blue/method` (DEX string offset `0x190f17`);
- `quick_blue/event.scanResult` (`0x190edc`);
- `quick_blue/message.connector` (`0x190ef9`).

The application-specific FFE UUID strings do not occur in DEX; they occur only
in the Dart AOT snapshot. Relevant DEX disassembly offsets include:

- `0x137fc8`: `BluetoothGattCallback.onCharacteristicChanged`;
- `0x13844c`: `onServicesDiscovered`;
- `0x1389b8`: CCCD UUID `00002902-0000-1000-8000-00805f9b34fb`;
- `0x1389dc`: notification/indication CCCD selection and
  `setCharacteristicNotification`;
- `0x13910a`: `writeValue` MethodChannel branch;
- `0x1391d8`: characteristic `setValue(byte[])`;
- `0x1391de`: legacy `BluetoothGatt.writeCharacteristic`.

The bytecode shape, channel inventory, `Pair<String,String>` helper, and method
branches match Quick Blue `0.0.1` source commit
[`86541edfcd2147cb5740a125dcd2e2c1336dce43`](https://github.com/woodemi/quick.flutter/blob/86541edfcd2147cb5740a125dcd2e2c1336dce43/packages/quick_blue/android/src/main/kotlin/com/example/quick_blue/QuickBluePlugin.kt)
with high confidence. The native bridge has no queue or application
fragmentation layer. It supports both notification and indication CCCD values,
as selected by the Dart argument. Its `writeValue` branch accepts the Dart
output-property argument but only calls `setValue(byte[])` followed by legacy
`BluetoothGatt.writeCharacteristic`; it never calls `setWriteType`. Therefore
`withoutResponse` is proven as Dart intent, not as the effective Android ATT
write type. The latter depends on the discovered characteristic's properties
and runtime default. The sampled FFE3 characteristic is now known to advertise
both write modes, but a passive Android HCI capture is still required to prove
which one the original app actually used.

## Live BLE evidence

The public evidence intentionally withholds the sampled charger's serial
number, advertised name, MAC/CoreBluetooth identifier, and raw configuration
identity field. Identity-bearing frames remain in the ignored local diagnostic
only; the checked-in contract uses explicit `synthetic_layout` replacements
where a complete field-layout vector is required. This is a provenance
boundary, not a claim that the synthetic bytes were captured from hardware.

An initial packaged macOS-client run reached the user-owned charger far enough
to scan, connect, and enumerate GATT. The ignored local diagnostic
`logs/charger-live-20260825.jsonl` was first preserved at 66,716 bytes with
SHA-256
`10897556b7d11348aca1cf8bb8a16551e6d8aee7cd84b57d9194d047565e25a2`.
Process PID 73156 recorded:

- sequence 135: the selected HWCDQ advertisement included service FFE1;
- sequence 166: one FFE1 parent contained sibling FFE2 and FFE3;
- FFE2 properties: indicate, notify, read, write, WNR; WNR maximum 253;
- FFE3 properties: write, WNR; WNR maximum 253;
- sequences 171/173: the old reference client's obsolete FFE0-only selector
  rejected the table.

That rejection happened before CCCD setup, authentication, or an FFE3 write.
A corrected monitoring-only packaged process PID 67357 was then appended to
the same file. The exact appended snapshot used for analysis was 1,619,823
bytes with SHA-256
`6e86a3893d6c2513d1effc3a788d6ad2321ba3060c836966c771c811babff523`.
It recorded:

- sequence 197: unique FFE1/FFE2/FFE3 topology selected;
- sequence 200: FFE2 notification setup completed;
- sequence 225: exact redacted opcode-`0x02` authentication success;
- sequences 242/262/282: checksum-valid firmware, serial, and configuration;
- sequence 282 configuration payload offsets 33/37: f32le
  `00 00 32 43` = `178.0 V` maximum voltage and
  `00 00 60 41` = `14.0 A` maximum single-module current;
- 127 checksum-valid opcode-`0x06` telemetry frames, including sequence 3176;
- sequence 3261: acknowledged FFE3 write completion missed its native
  three-second deadline;
- sequence 3276: best-effort teardown completed with the transport disconnected.

Sequence 3176 carried telemetry state byte `payload[36] = 1` while the physical
charger was OFF. The owner-supplied main-display photograph
`3E5C49F3-1428-4559-B2BA-70FCEAF87775_4_5005_c.jpeg` is 67,867 bytes with
SHA-256
`bb559474f7cb13acc83d84df9ec3386f2bf13df020a69ccc6a87708f9e0f0296`;
it shows 4.076 V / 0.000 A and the inactive output indicator. Together with the
AOT Open/Close branch, this confirms telemetry raw `1`=OFF and raw `0`=ON.
The earlier inference that opcode `0x0C` used the opposite polarity was wrong.
The callback's opcode immediate is at `0x487500` and its generic-setter call is
at `0x487504`; it maps current raw `1` to request argument `0`, and current raw
`0` to argument `1`.

A later ignored diagnostic
`logs/charger-live-20260825-wnr-final.jsonl` was inspected at exactly 363,345
bytes, SHA-256
`53c30f5a909ce10a0cc0a6483ee676cb9fc0bd6759acd0201c97b0b502087c7a`,
PID 7487. Startup sequence 1 recorded monitoring mode
(`output_controls_enabled:false`). Sequence 331 showed raw `1`/OFF at about
4.08 V; sequence 349 transmitted `06 0C 00 00 00 00 0C`; sequence 359 returned
acceptance ACK `03 0C 01 0D`; sequence 378 changed to raw `0`/ON; sequences 432
and 462 showed the output rising through 17.819 V to 134.112 V. Thus int32le
zero = Start/ON is live-confirmed. The callback's complementary int32le-one =
Stop/OFF mapping is high confidence but not yet live-transmitted. The ACK byte
means acceptance, not state; telemetry is required for readback. The raw log is
not tracked because it contains local identifiers.

The corrected client now prefers WNR on FFE3 and keeps acknowledged write as
fallback, but this was implemented after the captured run. Long-run WNR
durability remains awaiting a bounded read-only hardware soak and is not
claimed verified. The full redacted event chain, frame bytes, attachment
hashes, and scope are in
[`live-gatt-2026-08-25.md`](live-gatt-2026-08-25.md).

The owner identified the sampled product on 2026-08-25 as **Pidzoom Portable
charger HW178P** and supplied its `50…178 V` voltage range and `14 A` current
maximum. Only the two upper maxima are independently represented in the live
opcode-`0x05` packet. The exact model identity and `50 V` minimum are not
protocol fields and retain owner-supplied provenance. The desktop profile's
`0.01 A` positive floor is conservative application policy, not hardware
evidence.

## Public-source boundary

The [official application page](https://niub.cc/index/article/detail/id/4.html)
provides the APK link, and the
[official product page](https://niub.cc/index/article/detail/id/2.html)
identifies the HW Bluetooth control board for Huawei 4850/4875 chargers.
Public searches found no product-specific packet bytes, opcode table, checksum
routine, source repository, or indexed occurrence of `HWCDQBLE_NIUB` or the two
target Dart paths. Generic FFE UART examples were excluded as unrelated.

Product screenshots corroborate the existence of voltage/current setpoints,
output control, telemetry, Ah/Wh, module count, and settings pages, but they are
not used as evidence for any opcode, offset, encoding, or checksum.

## Analysis tooling

- macOS `/usr/bin/strings`, `/usr/bin/nm`, `/usr/bin/objdump`: Apple LLVM
  21.0.0 toolchain.
- Python: CPython 3.14.6.
- OpenSSL: 3.6.3.
- Android Build Tools 35.0.1, obtained from Google's official repository XML.
  Published archive SHA-1:
  `f4dda6855ddf1ea1a51ee3ab6587104bd0c1d727`; observed archive SHA-256:
  `c01e4b763da96ae5ef67e8bdf2abc94fb6cb3e73a42209581feb6a7019a51b9c`.
  Only compact required tools were retained locally after verification:
  `aapt2` SHA-256 `2ed636477a40fbc88670837c3ead484ce68b5da410eb408036416fd3ef2517d6`
  and `dexdump` SHA-256
  `66fe25a502eba288ed93bac50d76b037bddaccbcde4b8ce82e30f16307f9a4ab`.
- r2flutter `0.2.0`, pinned source commit
  `66b294ab7cd454f4d8be11f3739fcf1562bb4b14`, built locally against radare2.
- radare2 `6.2.0` (`git.6.2.0`, ABI 132). The pinned official Homebrew ARM64
  bottle SHA-256 is
  `533d4f4051043a7901fc99cded9f64bbb30c3333603021db028c75dc2ef3dba2`.
- Blutter source was inspected at pinned commit
  `4a60ac648bf448c5a7596437243bcd0b9376fdf0`; its Dart build was abandoned
  before completion because of local disk pressure. It contributed only the
  compact `extract_dart_info` snapshot metadata, not protocol conclusions.

Downloaded analysis sources, build products, and tool binaries remain under
ignored `research/`. No system-wide installation was performed. The
protocol-to-address evidence ledger is [`aot-findings.md`](aot-findings.md).

## Reproduction command outline

Run from this repository. Tool paths below are the retained ignored copies;
they may be replaced by equivalent pinned tools. The redacted landing URL is
not needed after the direct application link has been identified.

```sh
curl --fail --location \
  https://niub.cc/index/download/index/type/app \
  --output artifacts/original/hwcdq.apk
shasum -a 256 artifacts/original/hwcdq.apk
unzip -t artifacts/original/hwcdq.apk
unzip -q artifacts/original/hwcdq.apk -d artifacts/unpacked

research/tooling/android-minimal/aapt2 dump badging artifacts/original/hwcdq.apk
research/tooling/android-minimal/dexdump -d artifacts/unpacked/classes.dex
strings -a -t x artifacts/unpacked/lib/arm64-v8a/libapp.so

export DYLD_LIBRARY_PATH="$PWD/research/aot/radare2-local/radare2/6.2.0/lib"
research/aot/r2flutter-src/bin/r2flutter -HH \
  artifacts/unpacked/lib/arm64-v8a/libapp.so
research/aot/r2flutter-src/bin/r2flutter -O pp+0x10ce0 \
  artifacts/unpacked/lib/arm64-v8a/libapp.so
research/aot/r2flutter-src/bin/r2flutter -O pp+0x10ce8 \
  artifacts/unpacked/lib/arm64-v8a/libapp.so
research/aot/r2flutter-src/bin/r2flutter -O pp+0x132a0 \
  artifacts/unpacked/lib/arm64-v8a/libapp.so
research/aot/radare2-local/radare2/6.2.0/bin/radare2 \
  -e bin.relocs.apply=true artifacts/unpacked/lib/arm64-v8a/libapp.so
```

Representative radare2 seeks/disassemblies are `s 0x3d2920; pd 220` for the
receiver/checksum, `s 0x3e85a4; pd 100` for target UUID initialization, and the
function addresses listed in `aot-findings.md`. r2flutter's internal snapshot
layout compatibility label must not be used as a Dart SDK marketing version;
the official engine-to-Dart pin above is authoritative.

During static reconstruction, no APK or bundled native library was executed
and no emulator, vendor account, BLE adapter, charger, fuzzing, or command
transmission was used. The first packaged-client monitoring described above did
use CoreBluetooth to scan, connect, subscribe, authenticate, and send only the
documented read-only application queries. It did not send a voltage/current,
Start/Stop, calibration, factory, password-change, firmware-update, or unknown
command. The later PID 7487 incident separately sent only the known opcode
`0x0C` int32le-zero vector; it is preserved because its electrical readback
corrected the command polarity and exposed the monitoring-authorization bug.

## Desktop application visual evidence

Both screenshots were rendered at 1220×800 with Qt's offscreen platform and
an injected deterministic simulator snapshot. They prove layout/rendering only;
they are not live-device evidence and did not initialize Bleak or Bluetooth.

| Path | SHA-256 | Purpose |
| --- | --- | --- |
| `evidence/gui-simulation.png` | `5e17a0e002410ade2b2900b4bb351a0381e3bc791162f3417b60c1be1662f1b6` | Baseline HW178P-profile simulation workbench. |
| `evidence/gui-debug-simulation.png` | `dc15e54a183cddaa4cea43713cc7804bd58422d20db0a4443780d86d9f0eaa4f` | Healthy DEBUG badge and HW178P-profile instrument state. |
