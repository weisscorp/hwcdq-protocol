# HWCDQProtocol for Swift

Pure Foundation wire codec for the HWCDQ/Pidzoom HW178P protocol. It contains
no CoreBluetooth transport, reconnect policy, user interface, FFI, or runtime
dependency. Add `native/swift` as a local or remote Swift Package dependency and
import `HWCDQProtocol` from an iOS or macOS target.

```swift
import HWCDQProtocol

let setVoltage = try HWCDQCodec.encodeSetVoltage(84.0)
let stop = HWCDQCodec.encodeStop()
let packet = try HWCDQCodec.decode(notificationData)
```

The public API offers named, recovered commands only. It intentionally has no
public arbitrary-opcode frame encoder. A mobile application supplies its own
CoreBluetooth transport and enforces `HW178P.profile.effectiveLimits(...)`
before sending mutations.

Transport policy remains consumer-owned: subscribe to FFE2 notifications,
write complete application frames to FFE3, split only at the platform write
limit, prefer CoreBluetooth `.withoutResponse`, and use `.withResponse` as the
fallback described by `contract/v1/gatt.json`.

Run the shared-contract and local tests with:

```sh
swift test
```

Some standalone Apple CommandLineTools installations do not add their bundled
Swift Testing framework to SwiftPM's compiler/runtime paths. In that specific
environment, use the same test command with the toolchain paths made explicit:

```sh
swift test \
  -Xswiftc -F \
  -Xswiftc /Library/Developer/CommandLineTools/Library/Developer/Frameworks \
  -Xlinker -F/Library/Developer/CommandLineTools/Library/Developer/Frameworks \
  -Xlinker -framework -Xlinker Testing \
  -Xlinker -rpath \
  -Xlinker /Library/Developer/CommandLineTools/Library/Developer/Frameworks \
  -Xlinker -rpath \
  -Xlinker /Library/Developer/CommandLineTools/Library/Developer/usr/lib
```

Tests read the repository's canonical JSON vectors directly from
`contract/v1/vectors/`; they do not carry a second copy of the contract.
