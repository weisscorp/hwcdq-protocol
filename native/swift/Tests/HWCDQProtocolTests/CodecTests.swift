import Foundation
import HWCDQProtocol
import Testing

@Suite("Named codec and decoder")
struct CodecTests {
  @Test func namedRequestVectors() throws {
    #expect(HWCDQCodec.encodeGetFirmware().hex == "020101")
    #expect(HWCDQCodec.encodeGetSerial().hex == "020404")
    #expect(HWCDQCodec.encodeGetConfiguration().hex == "020505")
    #expect(HWCDQCodec.encodeGetTelemetry().hex == "020606")
    #expect(try HWCDQCodec.encodeSetVoltage(84).hex == "06070000a842f1")
    #expect(try HWCDQCodec.encodeSetVoltage(48).hex == "06070000404289")
    #expect(try HWCDQCodec.encodeSetCurrent(10).hex == "06080000204169")
    #expect(try HWCDQCodec.encodeSetCurrent(20).hex == "06080000a041e9")
    #expect(HWCDQCodec.encodeStart().hex == "060c000000000c")
    #expect(HWCDQCodec.encodeStop().hex == "060c010000000d")
  }

  @Test func credentialsAreValidatedCanonicalAndRedacted() throws {
    let fallback = Credential.apkFallback()
    #expect(
      HWCDQCodec.encodeAuthentication(fallback).hex
        == "230244343144384344393846303042323034453938303039393845434638343237450045"
    )
    let direct = try Credential.fromDigest("098F6BCD4621D373CADE4E832627B4F6")
    #expect(
      HWCDQCodec.encodeAuthentication(direct).hex
        == "2302303938663662636434363231643337336361646534653833323632376234663600ca"
    )
    #expect(String(describing: direct) == "Credential([REDACTED])")
    #expect(String(reflecting: direct) == "Credential([REDACTED])")
    expectErrorCode("credential.non_hex") {
      _ = try Credential.fromDigest("g" + String(repeating: "0", count: 31))
    }
  }

  @Test func directionNeutralDecodeAndOutputPolarity() throws {
    let voltage = try HWCDQCodec.decode(HWCDQCodec.encodeSetVoltage(84))
    #expect(voltage.opcode == 0x07)
    #expect(voltage.payload.hex == "0000a842")
    #expect(voltage.meaning == .setVoltage(84))
    #expect(voltage.acknowledged == nil)

    let start = try HWCDQCodec.decode(HWCDQCodec.encodeStart())
    #expect(start.meaning == .outputControl(state: 0, enabled: true))
    let stop = try HWCDQCodec.decode(HWCDQCodec.encodeStop())
    #expect(stop.meaning == .outputControl(state: 1, enabled: false))

    let ack = try HWCDQCodec.decode(Data(hex: "03070108"))
    #expect(ack.acknowledged == true)
    #expect(ack.meaning == .acknowledgement(true))
  }

  @Test func configurationLayout() throws {
    let packet = Data(
      hex:
        "6905" + "0000803f00000040000040400000804001" + "0000a0400000c0400000e04000000041"
        + "00001041000020410100003041aa01bb503c64"
        + "4142434445464748494a4b4c4d4e4f5051525354555657" + "01000040410000504101cc07f401e803"
        + "656e000000000000ddeefeff36"
    )
    let decoded = try HWCDQCodec.decode(packet)
    guard case .configuration(let config) = decoded.meaning else {
      Issue.record("expected typed configuration")
      return
    }
    #expect(config.targetVoltage == 1)
    #expect(config.targetCurrent == 2)
    #expect(config.maxVoltage == 9)
    #expect(config.maxSingleModuleCurrent == 10)
    #expect(config.rawASCII23 == Data("ABCDEFGHIJKLMNOPQRSTUVW".utf8))
    #expect(config.powerLimit == 500)
    #expect(config.maxPower == 1000)
    #expect(config.displayLanguageRaw.hex == "656e000000000000")
    #expect(config.rawU8At102 == 0xff)
  }

  @Test func telemetryLayoutAndStrictStatusMapping() throws {
    let packet = Data(
      hex:
        "3006" + "0000803f0000004000004040" + "000080400000a0400000c040"
        + "0000e0400000004100001041" + "a500002041000030415aa8"
    )
    let decoded = try HWCDQCodec.decode(packet)
    guard case .telemetry(let telemetry) = decoded.meaning else {
      Issue.record("expected typed telemetry")
      return
    }
    #expect(telemetry.inputVoltage == 1)
    #expect(telemetry.outputCurrent == 7)
    #expect(telemetry.currentOutput == 0xa5)
    #expect(telemetry.outputEnabled == nil)
    #expect(telemetry.inputPowerW == 2)
    #expect(telemetry.outputPowerW == 42)
    #expect(telemetry.moduleCount == 0x5a)
  }

  @Test func derivedPowerPromotesDecodedFloat32OperandsToBinary64() throws {
    var payload = Data(repeating: 0, count: 46)
    payload.replaceSubrange(0..<4, with: Float(1.1).littleEndianData)
    payload.replaceSubrange(4..<8, with: Float(2.2).littleEndianData)
    payload.replaceSubrange(20..<24, with: Float(3.3).littleEndianData)
    payload.replaceSubrange(24..<28, with: Float(4.4).littleEndianData)
    let frame = testFrame(opcode: HWCDQCodec.getTelemetryOpcode, payload: payload)
    guard case .telemetry(let telemetry) = try HWCDQCodec.decode(frame).meaning else {
      Issue.record("expected typed telemetry")
      return
    }

    let expectedInput = Double(Float(1.1)) * Double(Float(2.2))
    let expectedOutput = Double(Float(3.3)) * Double(Float(4.4))
    #expect(telemetry.inputPowerW == expectedInput)
    #expect(telemetry.outputPowerW == expectedOutput)
    #expect(telemetry.inputPowerW != Double(Float(1.1) * Float(2.2)))
    #expect(telemetry.outputPowerW != Double(Float(3.3) * Float(4.4)))
  }

  @Test func unknownOpcodePreservesRawPayload() throws {
    let packet = try HWCDQCodec.decode(Data(hex: "06e1deadbeef19"))
    #expect(
      packet.meaning == .unknown(UnknownPacket(opcode: 0xe1, payload: Data(hex: "deadbeef")))
    )
  }

  @Test func float32AndFrameValidation() throws {
    for value in [0.0, -1.0, -Double.infinity] {
      #expect(throws: (any Error).self) { try HWCDQCodec.encodeSetCurrent(value) }
    }
    expectErrorCode("scalar.non_finite") { _ = try HWCDQCodec.encodeSetVoltage(.nan) }
    expectErrorCode("scalar.not_float32") {
      _ = try HWCDQCodec.encodeSetVoltage(1e100)
    }
    expectErrorCode("scalar.not_float32") {
      _ = try HWCDQCodec.encodeSetVoltage(1e-100)
    }
    expectErrorCode("packet.truncated") {
      _ = try HWCDQCodec.decode(Data(hex: "0204"))
    }
    expectErrorCode("packet.checksum.mismatch") {
      _ = try HWCDQCodec.decode(Data(hex: "020405"))
    }
    #expect(!HWCDQCodec.verifyChecksum(Data(hex: "020405")))
    #expect(HWCDQCodec.verifyChecksum(Data(hex: "020404")))
  }
}

@Suite("Incremental framing")
struct FramingTests {
  @Test func splitAndCoalescedPackets() throws {
    var decoder = try IncrementalFrameDecoder()
    #expect(try decoder.feed(Data(hex: "0201")).isEmpty)
    #expect(decoder.bufferedByteCount == 2)
    let packets = try decoder.feed(Data(hex: "01020404020606"))
    #expect(packets.map(\.raw.hex) == ["020101", "020404", "020606"])
    #expect(decoder.bufferedByteCount == 0)
  }

  @Test func malformedStreamFailsClosedAndResets() throws {
    var decoder = try IncrementalFrameDecoder()
    expectErrorCode("stream.length.invalid") {
      _ = try decoder.feed(Data(hex: "000404"))
    }
    #expect(decoder.bufferedByteCount == 0)
    expectErrorCode("stream.frame.invalid") {
      _ = try decoder.feed(Data(hex: "020405"))
    }
    #expect(decoder.bufferedByteCount == 0)
    #expect(try decoder.feed(Data(hex: "020404")).first?.opcode == 0x04)
  }
}

@Suite("HW178P profile")
struct ProfileTests {
  @Test func constantsAndFloat32AwareBounds() throws {
    let profile = HW178P.profile
    #expect(profile.model == "HW178P")
    #expect(profile.gatt.serviceUUID == "FFE1")
    #expect(profile.gatt.receiveUUID == "FFE2")
    #expect(profile.gatt.transmitUUID == "FFE3")
    #expect(profile.voltage == (try NumericRange(minimum: 50, maximum: 178)))
    #expect(profile.current == (try NumericRange(minimum: 0.01, maximum: 14)))
    #expect(profile.current.contains(0.01))
    #expect(!profile.current.contains(0.009))
  }

  @Test func customRangesMustBeRepresentableAsPositiveFloat32() {
    for limits in [
      (1e-100, 1.0),
      (1.0, 1e100),
      (Double.nan, 1.0),
      (1.0, Double.infinity),
    ] {
      expectErrorCode("profile.value.invalid") {
        _ = try NumericRange(minimum: limits.0, maximum: limits.1)
      }
    }
  }

  @Test func effectiveLimitsIntersectAndFailClosed() throws {
    let profile = HW178P.profile
    let bounded = profile.effectiveLimits(reportedMaxVoltage: 200, reportedMaxCurrent: 10)
    #expect(bounded?.voltage.maximum == 178)
    #expect(bounded?.current.maximum == 10)
    #expect(profile.effectiveLimits(reportedMaxVoltage: nil, reportedMaxCurrent: 10) == nil)
    #expect(profile.effectiveLimits(reportedMaxVoltage: 49, reportedMaxCurrent: 10) == nil)

    let floor = profile.effectiveLimits(
      reportedMaxVoltage: 50,
      reportedMaxCurrent: Double(Float(0.01))
    )
    #expect(floor?.voltage.maximum == 50)
    #expect(floor?.current.maximum == 0.01)
  }
}

private func expectErrorCode(
  _ code: String,
  sourceLocation: SourceLocation = #_sourceLocation,
  _ operation: () throws -> Void
) {
  do {
    try operation()
    Issue.record("expected HWCDQProtocolError \(code)", sourceLocation: sourceLocation)
  } catch let error as HWCDQProtocolError {
    #expect(error.conformanceCode == code, sourceLocation: sourceLocation)
  } catch {
    Issue.record("unexpected error: \(error)", sourceLocation: sourceLocation)
  }
}
