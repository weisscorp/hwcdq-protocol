# Cross-platform protocol libraries

The repository uses a language-neutral wire contract plus independent native
implementations. It does not use a shared Rust core, FFI, embedded Python, or
generated application code.

```text
contract/v1 (wire facts, profile, GATT, golden vectors)
    ├── packages/hwcdq-client       Python codec, transport contracts, session
    ├── native/swift                Swift codec and framing
    └── native/kotlin/hwcdq-protocol Kotlin codec and framing
```

This split keeps each application idiomatic on its platform while making byte
compatibility mechanically testable. The JSON files are the common contract;
the prose specification remains the evidence-backed explanation of those
facts.

## Contract v1

`contract/v1` is the normative interoperability fixture set for evidence-backed
behavior, with confidence recorded per command and vector:

- `wire.json` defines framing, checksum, scalar encodings, confirmed opcodes,
  the 103-byte configuration payload, and the 46-byte telemetry payload;
- `gatt.json` defines the FFE1 service, FFE2 notification path, FFE3 write
  path, and transport fragmentation rules;
- `profiles/hw178p.json` defines the HW178P application envelope and
  fail-closed device-limit intersection;
- `vectors/*.json` contains positive and negative codec, decoder, framing, and
  profile cases used by every language implementation;
- `manifest.json` pins the exact SHA-256 of every other v1 contract file.

The manifest hashes the exact file bytes. Paths are relative POSIX paths,
sorted lexicographically, and `manifest.json` does not hash itself. Published
`v1` facts and vectors are immutable: a breaking semantic change requires a
new contract directory. Evidence corrections must first be reflected in the
wire-level documentation and confidence labels.

Validate the contract and its manifest from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/validate_contract.py --check
```

The checked-in `contract.schema.json` is documentary. The validator performs
the normative cross-file and semantic checks explicitly, including resolution
of every repository-local evidence file and Markdown heading anchor; it is not
presented as a general-purpose JSON Schema validator.

The dotted error identifiers in the contract are native/conformance codes.
Swift and Kotlin expose them directly. The Python conformance harness maps the
existing `ProtocolError` results to those identifiers without changing the
established Python exception API or relying on the mapping as a consumer API.

## Python

The existing `hwcdq-client` distribution and `hwcdq` import namespace remain
the complete reusable Python client. They provide the codec, incremental
framing, GATT selection, transport interfaces, authenticated session, safety
interlocks, diagnostics, Bleak adapter, and deterministic fakes. The desktop
UI imports this same library, and a future Home Assistant integration can do
the same.

No Python runtime API or behavior is replaced by the native wrappers. In
particular, the historical Python plaintext-password compatibility factory is
retained for existing consumers. New integrations for the sampled charger
should normally use `Credential.apk_fallback()` or an already-derived digest.
Across the native wrappers, a non-fallback digest is canonicalized to lowercase
hex, while the APK fallback preserves its exact recovered uppercase spelling.

Run the Python conformance suite from the repository root:

```sh
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m unittest discover -s tests -v
```

## Swift

`native/swift` is a Swift Package Manager library named `HWCDQProtocol`. It
uses Foundation only and provides:

- named encoders for the executable evidence-backed operations (including the
  high-confidence, not-yet-live-confirmed Stop variant);
- exact packet decoding and checksum verification;
- incremental notification framing;
- typed configuration, telemetry, credential, and HW178P profile models.

It intentionally does not import CoreBluetooth or own a connection/session.
An iOS application supplies CoreBluetooth discovery and transport, enables
FFE2 notifications, writes encoded frames to FFE3, and feeds every
notification chunk into the incremental decoder.

```sh
cd native/swift
swift test
```

That is the normal Xcode/Linux command. A CommandLineTools-only Apple install
may need the explicit framework/rpath form documented in
[`native/swift/README.md`](../native/swift/README.md); the package's runtime
target remains Foundation-only.

## Kotlin

`native/kotlin/hwcdq-protocol` is a pure Kotlin/JVM library. It exposes the
same confirmed codec, decoder, incremental framing, credential, telemetry,
configuration, and HW178P profile concepts without depending on the Android
SDK. It builds with JDK 21 but emits Java 17-compatible class files for use by
current Android toolchains.

It intentionally does not import `BluetoothGatt` or own reconnect/session
policy. An Android application supplies scanning and GATT lifecycle code,
subscribes to FFE2, writes named encoder output to FFE3, and feeds received
chunks into the incremental decoder.

```sh
cd native/kotlin/hwcdq-protocol
./gradlew test
```

## Safety and API boundary

The low-level codecs validate representation: voltage/current must become a
positive finite IEEE-754 binary32 value. They do not declare every
representable value safe for a particular charger.

Before a consumer sends voltage, current, or Start, it must separately enforce
the HW178P profile (`50.00...178.00 V`, `0.01...14.00 A`), intersect it with a
fresh valid opcode-`0x05` device maximum, and follow the session/readback rules
in [`protocol.md`](protocol.md). Stop is still a state-changing command and
must never be replayed after an ambiguous write result.

Unknown packets remain decodable as raw opcode and payload for diagnostics,
but the native public APIs do not expose an arbitrary-opcode encoder. Unknown
or tentative commands in the contract are non-executable.

## Adding or correcting a protocol fact

Keep all implementations synchronized through evidence and vectors:

1. preserve the APK offset, disassembly, or passive/live packet evidence;
2. update the prose specification and confidence label;
3. add or change the language-neutral contract vector;
4. implement the behavior independently in Python, Swift, and Kotlin;
5. run all three conformance suites;
6. regenerate and verify the contract manifest.

Do not add a command to only one wrapper and treat that implementation as the
protocol source of truth.
