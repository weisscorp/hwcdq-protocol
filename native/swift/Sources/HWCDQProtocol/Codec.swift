import Foundation

public enum HWCDQCodec {
  public static let maximumPayloadLength = 253

  public static let getFirmwareOpcode: UInt8 = 0x01
  public static let checkPasswordOpcode: UInt8 = 0x02
  public static let getSerialOpcode: UInt8 = 0x04
  public static let getConfigurationOpcode: UInt8 = 0x05
  public static let getTelemetryOpcode: UInt8 = 0x06
  public static let setVoltageOpcode: UInt8 = 0x07
  public static let setCurrentOpcode: UInt8 = 0x08
  public static let outputControlOpcode: UInt8 = 0x0C

  public static func encodeGetFirmware() -> Data {
    encodePacket(opcode: getFirmwareOpcode)
  }

  public static func encodeAuthentication(_ credential: Credential) -> Data {
    var payload = Data(credential.wireValue.utf8)
    payload.append(0)
    return encodePacket(opcode: checkPasswordOpcode, payload: payload)
  }

  public static func encodeAPKFallbackAuthentication() -> Data {
    encodeAuthentication(.apkFallback())
  }

  public static func encodeGetSerial() -> Data {
    encodePacket(opcode: getSerialOpcode)
  }

  public static func encodeGetConfiguration() -> Data {
    encodePacket(opcode: getConfigurationOpcode)
  }

  public static func encodeGetTelemetry() -> Data {
    encodePacket(opcode: getTelemetryOpcode)
  }

  /// Encodes a positive finite binary32. Model/device limits remain a caller concern.
  public static func encodeSetVoltage(_ volts: Double) throws -> Data {
    encodePacket(opcode: setVoltageOpcode, payload: try positiveFloat32(volts))
  }

  /// Encodes a positive finite binary32. Model/device limits remain a caller concern.
  public static func encodeSetCurrent(_ amps: Double) throws -> Data {
    encodePacket(opcode: setCurrentOpcode, payload: try positiveFloat32(amps))
  }

  /// Output-control polarity recovered from the app and confirmed live: 0 = ON.
  public static func encodeStart() -> Data {
    encodePacket(opcode: outputControlOpcode, payload: littleEndian(Int32(0)))
  }

  /// AOT-derived high-confidence polarity: 1 = OFF. Live confirmation is pending.
  public static func encodeStop() -> Data {
    encodePacket(opcode: outputControlOpcode, payload: littleEndian(Int32(1)))
  }

  /// Modulo-256 sum over opcode and payload; the length byte is excluded.
  public static func checksum(opcode: UInt8, payload: Data = Data()) -> UInt8 {
    payload.reduce(opcode) { partial, byte in partial &+ byte }
  }

  public static func verifyChecksum(_ data: Data) -> Bool {
    (try? decode(data)) != nil
  }

  /// Validate and decode exactly one complete, direction-neutral application frame.
  public static func decode(_ data: Data) throws -> DecodedPacket {
    let bytes = [UInt8](data)
    guard bytes.count >= 3 else { throw HWCDQProtocolError.packetTruncated }

    let declaredLength = bytes[0]
    guard declaredLength >= 2 else { throw HWCDQProtocolError.packetLengthMinimum }
    let expectedTotal = Int(declaredLength) + 1
    guard bytes.count == expectedTotal else {
      throw HWCDQProtocolError.lengthMismatch(
        declared: expectedTotal,
        actual: bytes.count
      )
    }

    let opcode = bytes[1]
    let payload = Data(bytes[2..<(bytes.count - 1)])
    let actualChecksum = bytes[bytes.count - 1]
    let expectedChecksum = checksum(opcode: opcode, payload: payload)
    guard actualChecksum == expectedChecksum else {
      throw HWCDQProtocolError.checksumMismatch(
        expected: expectedChecksum,
        actual: actualChecksum
      )
    }

    let acknowledgement: Bool? = confirmedAcknowledgement(
      opcode: opcode,
      payload: payload
    )
    return DecodedPacket(
      raw: data,
      declaredLength: declaredLength,
      opcode: opcode,
      payload: payload,
      checksum: actualChecksum,
      meaning: meaning(opcode: opcode, payload: payload, acknowledgement: acknowledgement),
      acknowledged: acknowledgement
    )
  }

  // Intentionally internal: callers may send only frames from evidence-backed named encoders.
  internal static func encodePacket(opcode: UInt8, payload: Data = Data()) -> Data {
    precondition(payload.count <= maximumPayloadLength)
    var result = Data([UInt8(payload.count + 2), opcode])
    result.append(payload)
    result.append(checksum(opcode: opcode, payload: payload))
    return result
  }

  private static func positiveFloat32(_ value: Double) throws -> Data {
    guard value.isFinite else { throw HWCDQProtocolError.scalarNonFinite }
    guard value > 0 else { throw HWCDQProtocolError.scalarNonPositive }
    let rounded = Float(value)
    guard rounded.isFinite, rounded > 0 else {
      throw HWCDQProtocolError.scalarNotFloat32
    }
    return littleEndian(rounded.bitPattern)
  }

  private static func confirmedAcknowledgement(opcode: UInt8, payload: Data) -> Bool? {
    guard
      [checkPasswordOpcode, setVoltageOpcode, setCurrentOpcode, outputControlOpcode]
        .contains(opcode), payload.count == 1
    else {
      return nil
    }
    switch payload[payload.startIndex] {
    case 0: return false
    case 1: return true
    default: return nil
    }
  }

  private static func meaning(
    opcode: UInt8,
    payload: Data,
    acknowledgement: Bool?
  ) -> PacketMeaning {
    switch opcode {
    case getFirmwareOpcode:
      return payload.isEmpty ? .getFirmware : .firmwareResponse(payload)
    case checkPasswordOpcode:
      if let acknowledgement { return .authenticationAcknowledgement(acknowledgement) }
      if payload.last == 0 {
        let credential = payload.dropLast()
        let valid = credential.count == 32 && credential.allSatisfy(isASCIIHex)
        return .authenticationRequest(credentialFormatValid: valid)
      }
      return .authenticationUnknownPayload(payload)
    case getSerialOpcode:
      return payload.isEmpty ? .getSerial : .serialResponse(payload)
    case getConfigurationOpcode:
      if payload.isEmpty { return .getConfiguration }
      if payload.count == 103 { return .configuration(parseConfiguration(payload)) }
      return .configurationRaw(payload)
    case getTelemetryOpcode:
      if payload.isEmpty { return .getTelemetry }
      if payload.count == 46 { return .telemetry(parseTelemetry(payload)) }
      return .telemetryRaw(payload)
    case setVoltageOpcode:
      if let acknowledgement { return .acknowledgement(acknowledgement) }
      if payload.count == 4 { return .setVoltage(readFloat32(payload, at: 0)) }
      return .commandRaw(opcode: opcode, payload: payload)
    case setCurrentOpcode:
      if let acknowledgement { return .acknowledgement(acknowledgement) }
      if payload.count == 4 { return .setCurrent(readFloat32(payload, at: 0)) }
      return .commandRaw(opcode: opcode, payload: payload)
    case outputControlOpcode:
      if let acknowledgement { return .acknowledgement(acknowledgement) }
      if payload.count == 4 {
        let state = Int32(bitPattern: readUInt32(payload, at: 0))
        let enabled: Bool? =
          switch state {
          case 0: true
          case 1: false
          default: nil
          }
        return .outputControl(state: state, enabled: enabled)
      }
      return .commandRaw(opcode: opcode, payload: payload)
    default:
      return .unknown(UnknownPacket(opcode: opcode, payload: payload))
    }
  }

  private static func parseConfiguration(_ payload: Data) -> Configuration {
    Configuration(
      targetVoltage: readFloat32(payload, at: 0),
      targetCurrent: readFloat32(payload, at: 4),
      offlineVoltage: readFloat32(payload, at: 8),
      offlineCurrent: readFloat32(payload, at: 12),
      powerOnOutput: byte(payload, at: 16),
      voltageCalibration: readFloat32(payload, at: 17),
      voltageFeedbackCalibration: readFloat32(payload, at: 21),
      currentCalibration: readFloat32(payload, at: 25),
      currentFeedbackCalibration: readFloat32(payload, at: 29),
      maxVoltage: readFloat32(payload, at: 33),
      maxSingleModuleCurrent: readFloat32(payload, at: 37),
      autoStop: byte(payload, at: 41),
      shutdownCurrent: readFloat32(payload, at: 42),
      rawU8At46: byte(payload, at: 46),
      temperatureProtection: byte(payload, at: 47),
      rawU8At48: byte(payload, at: 48),
      protectionCutoffTemperature: byte(payload, at: 49),
      fanBoostTemperature: byte(payload, at: 50),
      fanMaxTemperature: byte(payload, at: 51),
      rawASCII23: payload.subdata(in: 52..<75),
      twoStageCharging: byte(payload, at: 75),
      secondaryVoltage: readFloat32(payload, at: 76),
      secondaryCurrent: readFloat32(payload, at: 80),
      offlineControl: byte(payload, at: 84),
      rawU8At85: byte(payload, at: 85),
      softStartCoefficient: byte(payload, at: 86),
      powerLimit: readUInt16(payload, at: 87),
      maxPower: readUInt16(payload, at: 89),
      displayLanguageRaw: payload.subdata(in: 91..<99),
      rawU8At99: byte(payload, at: 99),
      rawU8At100: byte(payload, at: 100),
      rawU8At101: byte(payload, at: 101),
      rawU8At102: byte(payload, at: 102)
    )
  }

  private static func parseTelemetry(_ payload: Data) -> Telemetry {
    Telemetry(
      inputVoltage: readFloat32(payload, at: 0),
      inputCurrent: readFloat32(payload, at: 4),
      inputFrequency: readFloat32(payload, at: 8),
      temperature1: readFloat32(payload, at: 12),
      temperature2: readFloat32(payload, at: 16),
      outputVoltage: readFloat32(payload, at: 20),
      outputCurrent: readFloat32(payload, at: 24),
      currentPoint: readFloat32(payload, at: 28),
      efficiency: readFloat32(payload, at: 32),
      currentOutput: byte(payload, at: 36),
      accumulatedCapacityAh: readFloat32(payload, at: 37),
      accumulatedEnergyWh: readFloat32(payload, at: 41),
      moduleCount: byte(payload, at: 45)
    )
  }

  private static func isASCIIHex(_ byte: UInt8) -> Bool {
    switch byte {
    case 48...57, 65...70, 97...102: true
    default: false
    }
  }

  private static func byte(_ data: Data, at offset: Int) -> UInt8 {
    data[data.index(data.startIndex, offsetBy: offset)]
  }

  private static func readUInt16(_ data: Data, at offset: Int) -> UInt16 {
    UInt16(byte(data, at: offset)) | (UInt16(byte(data, at: offset + 1)) << 8)
  }

  private static func readUInt32(_ data: Data, at offset: Int) -> UInt32 {
    UInt32(byte(data, at: offset))
      | (UInt32(byte(data, at: offset + 1)) << 8)
      | (UInt32(byte(data, at: offset + 2)) << 16)
      | (UInt32(byte(data, at: offset + 3)) << 24)
  }

  private static func readFloat32(_ data: Data, at offset: Int) -> Float {
    Float(bitPattern: readUInt32(data, at: offset))
  }

  private static func littleEndian(_ value: UInt32) -> Data {
    Data([
      UInt8(truncatingIfNeeded: value),
      UInt8(truncatingIfNeeded: value >> 8),
      UInt8(truncatingIfNeeded: value >> 16),
      UInt8(truncatingIfNeeded: value >> 24),
    ])
  }

  private static func littleEndian(_ value: Int32) -> Data {
    littleEndian(UInt32(bitPattern: value))
  }
}
