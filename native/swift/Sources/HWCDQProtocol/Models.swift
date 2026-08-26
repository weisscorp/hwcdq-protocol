import Foundation

public struct Configuration: Equatable, Sendable {
  public let targetVoltage: Float
  public let targetCurrent: Float
  public let offlineVoltage: Float
  public let offlineCurrent: Float
  public let powerOnOutput: UInt8
  public let voltageCalibration: Float
  public let voltageFeedbackCalibration: Float
  public let currentCalibration: Float
  public let currentFeedbackCalibration: Float
  public let maxVoltage: Float
  public let maxSingleModuleCurrent: Float
  public let autoStop: UInt8
  public let shutdownCurrent: Float
  public let rawU8At46: UInt8
  public let temperatureProtection: UInt8
  public let rawU8At48: UInt8
  public let protectionCutoffTemperature: UInt8
  public let fanBoostTemperature: UInt8
  public let fanMaxTemperature: UInt8
  public let rawASCII23: Data
  public let twoStageCharging: UInt8
  public let secondaryVoltage: Float
  public let secondaryCurrent: Float
  public let offlineControl: UInt8
  public let rawU8At85: UInt8
  public let softStartCoefficient: UInt8
  public let powerLimit: UInt16
  public let maxPower: UInt16
  public let displayLanguageRaw: Data
  public let rawU8At99: UInt8
  public let rawU8At100: UInt8
  public let rawU8At101: UInt8
  public let rawU8At102: UInt8
}

public struct Telemetry: Equatable, Sendable {
  public let inputVoltage: Float
  public let inputCurrent: Float
  public let inputFrequency: Float
  public let temperature1: Float
  public let temperature2: Float
  public let outputVoltage: Float
  public let outputCurrent: Float
  public let currentPoint: Float
  public let efficiency: Float
  public let currentOutput: UInt8
  public let accumulatedCapacityAh: Float
  public let accumulatedEnergyWh: Float
  public let moduleCount: UInt8

  /// `nil` preserves unknown state bytes rather than inventing an ON state.
  public var outputEnabled: Bool? {
    switch currentOutput {
    case 0: return true
    case 1: return false
    default: return nil
    }
  }

  /// Derived in binary64 from the decoded binary32 operands, matching Python.
  public var inputPowerW: Double { Double(inputVoltage) * Double(inputCurrent) }
  public var outputPowerW: Double { Double(outputVoltage) * Double(outputCurrent) }
}

public struct UnknownPacket: Equatable, Sendable {
  public let opcode: UInt8
  public let payload: Data

  public init(opcode: UInt8, payload: Data) {
    self.opcode = opcode
    self.payload = payload
  }
}

/// Direction-neutral semantic classification; requests and responses reuse opcodes.
public enum PacketMeaning: Equatable, Sendable {
  case getFirmware
  case firmwareResponse(Data)
  case authenticationRequest(credentialFormatValid: Bool)
  case authenticationAcknowledgement(Bool)
  case authenticationUnknownPayload(Data)
  case getSerial
  case serialResponse(Data)
  case getConfiguration
  case configuration(Configuration)
  case configurationRaw(Data)
  case getTelemetry
  case telemetry(Telemetry)
  case telemetryRaw(Data)
  case setVoltage(Float)
  case setCurrent(Float)
  case outputControl(state: Int32, enabled: Bool?)
  case acknowledgement(Bool)
  case commandRaw(opcode: UInt8, payload: Data)
  case unknown(UnknownPacket)
}

public struct DecodedPacket: Equatable, Sendable {
  public let raw: Data
  public let declaredLength: UInt8
  public let opcode: UInt8
  public let payload: Data
  public let checksum: UInt8
  public let meaning: PacketMeaning

  /// Present only for the four opcodes whose one-byte Boolean ACK is confirmed.
  public let acknowledged: Bool?

  /// Stable command spelling shared with the language-neutral contract.
  public var command: String {
    switch meaning {
    case .getFirmware: "get_firmware"
    case .firmwareResponse: "firmware_response"
    case .authenticationRequest: "check_password"
    case .authenticationAcknowledgement: "check_password_ack"
    case .authenticationUnknownPayload: "check_password_unknown_payload"
    case .getSerial: "get_serial"
    case .serialResponse: "serial_response"
    case .getConfiguration: "get_config"
    case .configuration, .configurationRaw: "config_response"
    case .getTelemetry: "get_telemetry"
    case .telemetry: "telemetry_response"
    case .telemetryRaw: "telemetry_opcode_unknown_payload"
    case .setVoltage: "set_voltage"
    case .setCurrent: "set_current"
    case .outputControl: "output_control"
    case .acknowledgement:
      switch opcode {
      case HWCDQCodec.checkPasswordOpcode: "check_password_ack"
      case HWCDQCodec.setVoltageOpcode: "set_voltage"
      case HWCDQCodec.setCurrentOpcode: "set_current"
      case HWCDQCodec.outputControlOpcode: "output_control"
      default: "unknown"
      }
    case .commandRaw:
      switch opcode {
      case HWCDQCodec.setVoltageOpcode: "set_voltage"
      case HWCDQCodec.setCurrentOpcode: "set_current"
      case HWCDQCodec.outputControlOpcode: "output_control"
      default: "unknown"
      }
    case .unknown: "unknown"
    }
  }
}
