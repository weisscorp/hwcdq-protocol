# HWCDQ Protocol for Kotlin/JVM

Pure Kotlin/JVM framing, encoding, decoding, and HW178P profile validation for
the versioned contract in [`contract/v1`](../../../contract/v1). The module uses
Kotlin 2.4.10, builds with a Java 21 toolchain, emits Java 17-compatible bytecode,
and has no runtime dependency beyond the Kotlin and JDK standard libraries.

## Scope

The public API provides:

- named encoders for firmware, credential, serial, configuration, telemetry,
  voltage, current, Start, and Stop packets;
- strict packet length/checksum validation and direction-neutral typed decoding;
- incremental framing for split and coalesced byte chunks;
- typed 103-byte configuration and 46-byte telemetry payloads;
- direct-digest credentials with the exact uppercase APK fallback;
- the HW178P 50–178 V / 0.01–14 A profile and device-maximum narrowing policy;
- GATT UUID and write-policy constants, without any Bluetooth implementation;
- stable dotted conformance codes through `HwcdqProtocolException`.

It deliberately contains no Android SDK, `BluetoothGatt`, BLE session, FFI,
Rust, Python embedding, plaintext-password hashing, or arbitrary raw-opcode
encoder. Unknown decoded opcodes and their payloads remain available as
`UnknownPacket`, but cannot be re-encoded through the public API.

## Usage

```kotlin
import cc.hwcdq.protocol.Credential
import cc.hwcdq.protocol.Hw178pProfile
import cc.hwcdq.protocol.HwcdqCodec
import cc.hwcdq.protocol.IncrementalFrameDecoder
import cc.hwcdq.protocol.ReportedDeviceLimits

val credential = Credential.fromDigest("abcdef0123456789abcdef0123456789")
val authenticationFrame = HwcdqCodec.encodeCheckCredential(credential)

// Setter codecs validate only positive, finite binary32 representation.
val voltageFrame = HwcdqCodec.encodeSetVoltage(84.0)

// Apply model and fresh device limits separately before a real mutation.
val reported = ReportedDeviceLimits(maxVoltage = 178.0f, maxSingleModuleCurrent = 14.0f)
val limits = Hw178pProfile.effectiveLimits(reported)
val safeVoltage = Hw178pProfile.validateVoltage(84.0, reported)
check(safeVoltage in limits.voltage)

val decoded = HwcdqCodec.decode(voltageFrame)
check(decoded.command == "set_voltage")

val stream = IncrementalFrameDecoder()
val packets = stream.append(authenticationFrame.copyOfRange(0, 10)) +
    stream.append(authenticationFrame.copyOfRange(10, authenticationFrame.size))
check(packets.single().packet.opcode == 0x02)
```

`Credential` never exposes its digest or wire bytes. Supply only an already
derived 32-character hexadecimal digest, or use `Credential.apkFallback`.

`encodeStop()` implements the high-confidence int32-le value `1` vector from
the contract. It does not imply a BLE delivery or safety policy.

## Build and test

Set `JAVA_HOME` to a Java 21 JDK, then run:

```sh
./gradlew test
```

The checked-in Gradle 9.7.1 wrapper pins the distribution SHA-256 to
`acd53f1edaf02f1a8ff99879f8a34b302661a057d9b063ae9e35b552f804d20a`.
Tests load the shared JSON files in `contract/v1/vectors` at runtime; golden
packets and error expectations are not copied into Kotlin fixtures. `org.json`
and the Kotlin/JUnit test libraries are test-only dependencies. The suite also
asserts class-file major version 61 so an accidental switch back to Java 21
bytecode fails CI before the package reaches an Android consumer.
