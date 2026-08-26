import Foundation
import HWCDQProtocol
import Testing

@Suite("Shared contract: codec")
struct ContractCodecVectorTests {
  @Test func allApplicableEncodeCases() throws {
    let document = try contractJSON("codec.json")
    let cases = try array(document, "encode_cases")
    var executed = 0
    for vector in cases {
      let id = try string(vector, "id")
      let operation = try string(vector, "operation")
      let arguments = try object(vector, "arguments")
      let expected = try object(vector, "expected")
      let frame: Data
      switch operation {
      case "encode_get_firmware":
        frame = HWCDQCodec.encodeGetFirmware()
      case "encode_get_serial":
        frame = HWCDQCodec.encodeGetSerial()
      case "encode_get_config":
        frame = HWCDQCodec.encodeGetConfiguration()
      case "encode_get_telemetry":
        frame = HWCDQCodec.encodeGetTelemetry()
      case "encode_authentication_apk_fallback":
        frame = HWCDQCodec.encodeAPKFallbackAuthentication()
      case "encode_authentication_credential":
        frame = HWCDQCodec.encodeAuthentication(
          try Credential.fromDigest(try string(arguments, "credential"))
        )
      case "encode_set_voltage":
        frame = try HWCDQCodec.encodeSetVoltage(try decimalArgument(arguments))
      case "encode_set_current":
        frame = try HWCDQCodec.encodeSetCurrent(try decimalArgument(arguments))
      case "encode_start":
        frame = HWCDQCodec.encodeStart()
      case "encode_stop":
        frame = HWCDQCodec.encodeStop()
      default:
        Issue.record("unknown codec operation \(operation) in \(id)")
        continue
      }
      executed += 1
      #expect(frame.hex == (try string(expected, "frame_hex")), "vector \(id)")
      let decoded = try HWCDQCodec.decode(frame)
      #expect(Int(decoded.opcode) == (try integer(expected, "opcode")), "vector \(id)")
      #expect(decoded.payload.hex == (try string(expected, "payload_hex")), "vector \(id)")
      #expect(
        String(format: "%02x", decoded.checksum) == (try string(expected, "checksum_hex")),
        "vector \(id)")
    }
    #expect(executed == cases.count)
  }

  @Test func allApplicableCredentialCases() throws {
    let cases = try array(try contractJSON("codec.json"), "credential_cases")
    var executed = 0
    for vector in cases {
      let id = try string(vector, "id")
      let operation = try string(vector, "operation")
      let arguments = try object(vector, "arguments")
      let expected = try object(vector, "expected")
      switch operation {
      case "apk_fallback_credential":
        let frame = HWCDQCodec.encodeAPKFallbackAuthentication()
        let expectedText = try string(expected, "credential_text")
        #expect(
          Data(frame.dropFirst(2).dropLast(2)).hex == Data(expectedText.utf8).hex, "vector \(id)")
        executed += 1
      case "canonicalize_direct_credential":
        let credential = try Credential.fromDigest(try string(arguments, "digest"))
        let frame = HWCDQCodec.encodeAuthentication(credential)
        #expect(frame.hex == (try string(expected, "frame_hex")), "vector \(id)")
        executed += 1
      default:
        Issue.record("unknown credential operation \(operation) in \(id)")
      }
    }
    #expect(executed == cases.count)
  }

  @Test func publicCodecDoesNotExposeArbitraryOpcodeFraming() throws {
    let source = try String(
      contentsOf: packageRoot.appendingPathComponent("Sources/HWCDQProtocol/Codec.swift"),
      encoding: .utf8
    )
    #expect(!source.contains("public static func encodePacket"))
    #expect(source.contains("internal static func encodePacket"))
  }
}

@Suite("Shared contract: decoder")
struct ContractDecodeVectorTests {
  @Test func everyDecodeVector() throws {
    let cases = try array(try contractJSON("decode.json"), "decode_cases")
    for vector in cases {
      let id = try string(vector, "id")
      let packet = try HWCDQCodec.decode(Data(hex: try string(vector, "packet_hex")))
      let expected = try object(vector, "expected")
      #expect(Int(packet.opcode) == (try integer(expected, "opcode")), "vector \(id)")
      #expect(packet.command == (try string(expected, "command")), "vector \(id)")
      #expect(packet.payload.count == (try integer(expected, "payload_length")), "vector \(id)")
      for field in try array(expected, "fields") {
        let path = try string(field, "path")
        let actual = try decodedField(path: path, packet: packet)
        let expectedValue = try expectedField(field)
        #expect(actual == expectedValue, "vector \(id), field \(path)")
      }
    }
  }
}

@Suite("Shared contract: invalid inputs")
struct ContractInvalidVectorTests {
  @Test func stableErrorTaxonomyMatchesWireContract() throws {
    let document = try contractRootJSON("wire.json")
    let expected = Set(try stringArray(document, "conformance_codes"))
    let actual = Set([
      HWCDQProtocolError.credentialLength.conformanceCode,
      HWCDQProtocolError.credentialNonHex.conformanceCode,
      HWCDQProtocolError.credentialType.conformanceCode,
      HWCDQProtocolError.scalarType.conformanceCode,
      HWCDQProtocolError.scalarNonFinite.conformanceCode,
      HWCDQProtocolError.scalarNonPositive.conformanceCode,
      HWCDQProtocolError.scalarNotFloat32.conformanceCode,
      HWCDQProtocolError.packetTruncated.conformanceCode,
      HWCDQProtocolError.packetLengthMinimum.conformanceCode,
      HWCDQProtocolError.lengthMismatch(declared: 0, actual: 0).conformanceCode,
      HWCDQProtocolError.checksumMismatch(expected: 0, actual: 0).conformanceCode,
      HWCDQProtocolError.streamLengthInvalid(nil).conformanceCode,
      HWCDQProtocolError.streamFrameInvalid(underlyingCode: "").conformanceCode,
      HWCDQProtocolError.profileValueInvalid.conformanceCode,
      HWCDQProtocolError.profileOutOfRange.conformanceCode,
      HWCDQProtocolError.profileDeviceLimitsInvalid.conformanceCode,
    ])
    #expect(actual == expected)
  }

  @Test func everyStaticallyApplicableInvalidVector() throws {
    let cases = try array(try contractJSON("invalid.json"), "invalid_cases")
    var skippedStaticTypeCases = 0
    for vector in cases {
      let id = try string(vector, "id")
      let operation = try string(vector, "operation")
      let arguments = try object(vector, "arguments")
      let expectedCode = try string(vector, "expected_code")
      if isPythonOnly(vector) {
        // Swift's typed public signatures make these Python-only inputs unrepresentable.
        skippedStaticTypeCases += 1
        continue
      }
      expectErrorCode(expectedCode) {
        switch operation {
        case "decode_packet":
          _ = try HWCDQCodec.decode(Data(hex: try string(arguments, "packet_hex")))
        case "encode_set_voltage":
          _ = try HWCDQCodec.encodeSetVoltage(try scalarArgument(arguments))
        case "encode_set_current":
          _ = try HWCDQCodec.encodeSetCurrent(try scalarArgument(arguments))
        case "encode_authentication_credential":
          _ = try Credential.fromDigest(try string(arguments, "credential"))
        default:
          Issue.record("unknown invalid operation \(operation) in \(id)")
        }
      }
    }
    #expect(skippedStaticTypeCases == 2)
  }
}

@Suite("Shared contract: notification framing")
struct ContractFramingVectorTests {
  @Test func everyFramingVector() throws {
    for vector in try array(try contractJSON("framing.json"), "cases") {
      let id = try string(vector, "id")
      let maximum = (vector["maximum_frame_size"] as? NSNumber)?.intValue ?? 256
      var decoder = try IncrementalFrameDecoder(maximumFrameSize: maximum)
      let expectedCode = vector["expected_code"] as? String
      var frames: [Data] = []
      var caught: HWCDQProtocolError?
      for chunkHex in try stringArray(vector, "chunks_hex") {
        do {
          frames.append(contentsOf: try decoder.feed(Data(hex: chunkHex)).map(\.raw))
        } catch let error as HWCDQProtocolError {
          caught = error
          break
        }
      }
      if let expectedCode {
        #expect(caught?.conformanceCode == expectedCode, "vector \(id)")
      } else {
        #expect(caught == nil, "vector \(id)")
      }
      #expect(frames.map(\.hex) == (try stringArray(vector, "expected_frames_hex")), "vector \(id)")
      #expect(
        decoder.bufferedByteCount == (try integer(vector, "expected_buffered_bytes")),
        "vector \(id)")

      if let postChunks = vector["post_error_chunks_hex"] as? [String] {
        var postFrames: [Data] = []
        for chunk in postChunks {
          postFrames.append(contentsOf: try decoder.feed(Data(hex: chunk)).map(\.raw))
        }
        #expect(
          postFrames.map(\.hex) == (try stringArray(vector, "post_error_frames_hex")),
          "vector \(id), post-error")
      }
    }
  }
}

@Suite("Shared contract: HW178P profile")
struct ContractProfileVectorTests {
  @Test func canonicalProfileAndGATTDocuments() throws {
    let profileDocument = try contractRootJSON("profiles/hw178p.json")
    let gattDocument = try contractRootJSON("gatt.json")
    let profile = HW178P.profile

    #expect(HW178P.profileID == (try string(profileDocument, "profile_id")))
    #expect(profile.model == (try string(profileDocument, "model")))
    #expect(profile.displayName == (try string(profileDocument, "display_name")))

    let voltage = try object(profileDocument, "voltage")
    let current = try object(profileDocument, "current")
    #expect(profile.voltage.minimum == (try decimal(try object(voltage, "minimum"))))
    #expect(profile.voltage.maximum == (try decimal(try object(voltage, "maximum"))))
    #expect(profile.current.minimum == (try decimal(try object(current, "minimum"))))
    #expect(profile.current.maximum == (try decimal(try object(current, "maximum"))))

    let service = try object(gattDocument, "service")
    let receive = try object(gattDocument, "rx")
    let transmit = try object(gattDocument, "tx")
    #expect(profile.gatt.serviceUUID.lowercased() == (try string(service, "short_uuid")))
    #expect(profile.gatt.receiveUUID.lowercased() == (try string(receive, "short_uuid")))
    #expect(profile.gatt.transmitUUID.lowercased() == (try string(transmit, "short_uuid")))
  }

  @Test func everyContainsVector() throws {
    let document = try contractJSON("profile.json")
    for vector in try array(document, "contains_cases") {
      let id = try string(vector, "id")
      let range =
        try string(vector, "quantity") == "voltage"
        ? HW178P.profile.voltage
        : HW178P.profile.current
      let value = try scalarValue(try object(vector, "value"))
      let accepted = try boolean(vector, "accepted")
      #expect(range.contains(value) == accepted, "vector \(id)")
      if let expectedCode = vector["expected_code"] as? String {
        expectErrorCode(expectedCode) { try range.validate(value) }
      } else {
        try range.validate(value)
      }
    }
  }

  @Test func everyEffectiveLimitsVector() throws {
    let document = try contractJSON("profile.json")
    for vector in try array(document, "effective_limits_cases") {
      let id = try string(vector, "id")
      let config = try object(vector, "config")
      let voltage = try optionalScalar(config["max_voltage"])
      let current = try optionalScalar(config["max_single_module_current"])
      if vector["expected"] is NSNull {
        let expectedCode = try string(vector, "expected_code")
        expectErrorCode(expectedCode) {
          _ = try HW178P.profile.validatedEffectiveLimits(
            reportedMaxVoltage: voltage,
            reportedMaxCurrent: current
          )
        }
        #expect(
          HW178P.profile.effectiveLimits(
            reportedMaxVoltage: voltage,
            reportedMaxCurrent: current
          ) == nil,
          "vector \(id)"
        )
        continue
      }
      let expected = try object(vector, "expected")
      let actual = try HW178P.profile.validatedEffectiveLimits(
        reportedMaxVoltage: voltage,
        reportedMaxCurrent: current
      )
      #expect(
        actual.voltage.minimum == Double(try string(expected, "voltage_minimum")), "vector \(id)")
      #expect(
        actual.voltage.maximum == Double(try string(expected, "voltage_maximum")), "vector \(id)")
      #expect(
        actual.current.minimum == Double(try string(expected, "current_minimum")), "vector \(id)")
      #expect(
        actual.current.maximum == Double(try string(expected, "current_maximum")), "vector \(id)")
    }
  }
}

private enum FieldValue: Equatable {
  case float32Hex(String)
  case number(Double)
  case integer(Int64)
  case boolean(Bool)
  case bytes(String)
  case null
}

private func decodedField(path: String, packet: DecodedPacket) throws -> FieldValue {
  if path == "payload" { return .bytes(packet.payload.hex) }
  if path == "credential_format_valid" {
    guard case .authenticationRequest(let valid) = packet.meaning else {
      throw VectorTestError.invalid("credential_format_valid requires an authentication request")
    }
    return .boolean(valid)
  }
  if path == "acknowledged" {
    return packet.acknowledged.map(FieldValue.boolean) ?? .null
  }
  if case .outputControl(let state, let enabled) = packet.meaning {
    switch path {
    case "state": return .integer(Int64(state))
    case "state_valid": return .boolean(enabled != nil)
    case "enabled": return enabled.map(FieldValue.boolean) ?? .null
    default: break
    }
  }
  if case .configuration(let value) = packet.meaning {
    switch path {
    case "config.target_voltage": return .float32Hex(value.targetVoltage.leHex)
    case "config.target_current": return .float32Hex(value.targetCurrent.leHex)
    case "config.offline_voltage": return .float32Hex(value.offlineVoltage.leHex)
    case "config.offline_current": return .float32Hex(value.offlineCurrent.leHex)
    case "config.power_on_output": return .integer(Int64(value.powerOnOutput))
    case "config.voltage_calibration": return .float32Hex(value.voltageCalibration.leHex)
    case "config.voltage_feedback_calibration":
      return .float32Hex(value.voltageFeedbackCalibration.leHex)
    case "config.current_calibration": return .float32Hex(value.currentCalibration.leHex)
    case "config.current_feedback_calibration":
      return .float32Hex(value.currentFeedbackCalibration.leHex)
    case "config.max_voltage": return .float32Hex(value.maxVoltage.leHex)
    case "config.max_single_module_current": return .float32Hex(value.maxSingleModuleCurrent.leHex)
    case "config.auto_stop": return .integer(Int64(value.autoStop))
    case "config.shutdown_current": return .float32Hex(value.shutdownCurrent.leHex)
    case "config.raw_u8_46": return .integer(Int64(value.rawU8At46))
    case "config.temperature_protection": return .integer(Int64(value.temperatureProtection))
    case "config.raw_u8_48": return .integer(Int64(value.rawU8At48))
    case "config.protection_cutoff_temperature":
      return .integer(Int64(value.protectionCutoffTemperature))
    case "config.fan_boost_temperature": return .integer(Int64(value.fanBoostTemperature))
    case "config.fan_max_temperature": return .integer(Int64(value.fanMaxTemperature))
    case "config.raw_ascii_23": return .bytes(value.rawASCII23.hex)
    case "config.two_stage_charging": return .integer(Int64(value.twoStageCharging))
    case "config.secondary_voltage": return .float32Hex(value.secondaryVoltage.leHex)
    case "config.secondary_current": return .float32Hex(value.secondaryCurrent.leHex)
    case "config.offline_control": return .integer(Int64(value.offlineControl))
    case "config.raw_u8_85": return .integer(Int64(value.rawU8At85))
    case "config.soft_start_coefficient": return .integer(Int64(value.softStartCoefficient))
    case "config.power_limit": return .integer(Int64(value.powerLimit))
    case "config.max_power": return .integer(Int64(value.maxPower))
    case "config.display_language_raw": return .bytes(value.displayLanguageRaw.hex)
    case "config.raw_u8_99": return .integer(Int64(value.rawU8At99))
    case "config.raw_u8_100": return .integer(Int64(value.rawU8At100))
    case "config.raw_u8_101": return .integer(Int64(value.rawU8At101))
    case "config.raw_u8_102": return .integer(Int64(value.rawU8At102))
    default: break
    }
  }
  if case .telemetry(let value) = packet.meaning {
    switch path {
    case "telemetry.input_voltage": return .float32Hex(value.inputVoltage.leHex)
    case "telemetry.input_current": return .float32Hex(value.inputCurrent.leHex)
    case "telemetry.input_frequency": return .float32Hex(value.inputFrequency.leHex)
    case "telemetry.temperature_1": return .float32Hex(value.temperature1.leHex)
    case "telemetry.temperature_2": return .float32Hex(value.temperature2.leHex)
    case "telemetry.output_voltage": return .float32Hex(value.outputVoltage.leHex)
    case "telemetry.output_current": return .float32Hex(value.outputCurrent.leHex)
    case "telemetry.current_point": return .float32Hex(value.currentPoint.leHex)
    case "telemetry.efficiency": return .float32Hex(value.efficiency.leHex)
    case "telemetry.current_output": return .integer(Int64(value.currentOutput))
    case "telemetry.output_enabled": return value.outputEnabled.map(FieldValue.boolean) ?? .null
    case "telemetry.accumulated_capacity_ah": return .float32Hex(value.accumulatedCapacityAh.leHex)
    case "telemetry.accumulated_energy_wh": return .float32Hex(value.accumulatedEnergyWh.leHex)
    case "telemetry.module_count": return .integer(Int64(value.moduleCount))
    case "telemetry.input_power_w": return .number(value.inputPowerW)
    case "telemetry.output_power_w": return .number(value.outputPowerW)
    default: break
    }
  }
  throw VectorTestError.invalid("unsupported decoded field \(path)")
}

private func expectedField(_ field: [String: Any]) throws -> FieldValue {
  switch try string(field, "type") {
  case "f32": return .float32Hex(try string(field, "f32le_hex"))
  case "number":
    guard let value = Double(try string(field, "decimal")) else {
      throw VectorTestError.invalid("invalid expected number")
    }
    return .number(value)
  case "integer": return .integer(Int64(try integer(field, "value")))
  case "boolean": return .boolean(try boolean(field, "value"))
  case "bytes": return .bytes(try string(field, "hex"))
  case "null": return .null
  default: throw VectorTestError.invalid("unknown field type")
  }
}

private func decimalArgument(_ arguments: [String: Any]) throws -> Double {
  try scalarValue(try object(arguments, "value"))
}

private func decimal(_ value: [String: Any]) throws -> Double {
  guard let result = Double(try string(value, "decimal")) else {
    throw VectorTestError.invalid("invalid decimal")
  }
  return result
}

private func scalarArgument(_ arguments: [String: Any]) throws -> Double {
  try scalarValue(try object(arguments, "value"))
}

private func scalarValue(_ value: [String: Any]) throws -> Double {
  if let decimal = value["decimal"] as? String, let result = Double(decimal) { return result }
  switch value["kind"] as? String {
  case "nan": return .nan
  case "positive_infinity": return .infinity
  case "negative_infinity": return -.infinity
  case "decimal":
    guard let decimal = value["decimal"] as? String, let result = Double(decimal) else {
      throw VectorTestError.invalid("invalid decimal scalar")
    }
    return result
  default: throw VectorTestError.invalid("unsupported scalar")
  }
}

private func optionalScalar(_ value: Any?) throws -> Double? {
  guard let value else { return nil }
  guard let object = value as? [String: Any] else {
    throw VectorTestError.invalid("scalar is not an object")
  }
  return try scalarValue(object)
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

private enum VectorTestError: Error {
  case invalid(String)
}

private let packageRoot = URL(fileURLWithPath: #filePath)
  .deletingLastPathComponent()
  .deletingLastPathComponent()
  .deletingLastPathComponent()

private let repositoryRoot =
  packageRoot
  .deletingLastPathComponent()
  .deletingLastPathComponent()

private func contractJSON(_ filename: String) throws -> [String: Any] {
  let url = repositoryRoot.appendingPathComponent("contract/v1/vectors/\(filename)")
  return try jsonObject(at: url, filename: filename)
}

private func contractRootJSON(_ filename: String) throws -> [String: Any] {
  let url = repositoryRoot.appendingPathComponent("contract/v1/\(filename)")
  return try jsonObject(at: url, filename: filename)
}

private func jsonObject(at url: URL, filename: String) throws -> [String: Any] {
  let data = try Data(contentsOf: url)
  guard let value = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
    throw VectorTestError.invalid("\(filename) is not a JSON object")
  }
  return value
}

private func array(_ object: [String: Any], _ key: String) throws -> [[String: Any]] {
  guard let value = object[key] as? [[String: Any]] else {
    throw VectorTestError.invalid("\(key) is not an object array")
  }
  return value
}

private func object(_ object: [String: Any], _ key: String) throws -> [String: Any] {
  guard let value = object[key] as? [String: Any] else {
    throw VectorTestError.invalid("\(key) is not an object")
  }
  return value
}

private func string(_ object: [String: Any], _ key: String) throws -> String {
  guard let value = object[key] as? String else {
    throw VectorTestError.invalid("\(key) is not a string")
  }
  return value
}

private func stringArray(_ object: [String: Any], _ key: String) throws -> [String] {
  guard let value = object[key] as? [String] else {
    throw VectorTestError.invalid("\(key) is not a string array")
  }
  return value
}

private func integer(_ object: [String: Any], _ key: String) throws -> Int {
  guard let value = object[key] as? NSNumber else {
    throw VectorTestError.invalid("\(key) is not an integer")
  }
  return value.intValue
}

private func boolean(_ object: [String: Any], _ key: String) throws -> Bool {
  guard let value = object[key] as? Bool else {
    throw VectorTestError.invalid("\(key) is not a boolean")
  }
  return value
}

private func isPythonOnly(_ vector: [String: Any]) -> Bool {
  (vector["shared_native_requirement"] as? Bool) == false
    && (vector["implementations"] as? [String]) == ["python"]
}

extension Float {
  fileprivate var leHex: String {
    let bits = bitPattern
    return Data([
      UInt8(truncatingIfNeeded: bits),
      UInt8(truncatingIfNeeded: bits >> 8),
      UInt8(truncatingIfNeeded: bits >> 16),
      UInt8(truncatingIfNeeded: bits >> 24),
    ]).hex
  }
}
