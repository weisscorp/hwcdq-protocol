import Foundation

public struct NumericRange: Equatable, Sendable {
  public let minimum: Double
  public let maximum: Double

  public init(minimum: Double, maximum: Double) throws {
    guard
      let minimumWire = wireFloat32(minimum),
      let maximumWire = wireFloat32(maximum),
      maximumWire >= minimumWire
    else {
      throw HWCDQProtocolError.profileValueInvalid
    }
    self.minimum = minimum
    self.maximum = maximum
  }

  public func contains(_ value: Double) -> Bool {
    (try? validate(value)) != nil
  }

  public func validate(_ value: Double) throws {
    guard let candidate = wireFloat32(value),
      let lower = wireFloat32(minimum),
      let upper = wireFloat32(maximum)
    else { throw HWCDQProtocolError.profileValueInvalid }
    guard candidate >= lower && candidate <= upper else {
      throw HWCDQProtocolError.profileOutOfRange
    }
  }
}

public struct EffectiveLimits: Equatable, Sendable {
  public let voltage: NumericRange
  public let current: NumericRange

  public init(voltage: NumericRange, current: NumericRange) {
    self.voltage = voltage
    self.current = current
  }
}

public struct GATTProfile: Equatable, Sendable {
  public let serviceUUID: String
  public let receiveUUID: String
  public let transmitUUID: String

  public init(serviceUUID: String, receiveUUID: String, transmitUUID: String) {
    self.serviceUUID = serviceUUID
    self.receiveUUID = receiveUUID
    self.transmitUUID = transmitUUID
  }
}

public struct ChargerProfile: Equatable, Sendable {
  public let model: String
  public let displayName: String
  public let gatt: GATTProfile
  public let voltage: NumericRange
  public let current: NumericRange

  public init(
    model: String,
    displayName: String,
    gatt: GATTProfile,
    voltage: NumericRange,
    current: NumericRange
  ) {
    self.model = model
    self.displayName = displayName
    self.gatt = gatt
    self.voltage = voltage
    self.current = current
  }

  /// Intersect fresh charger-reported maxima with the model envelope.
  /// Missing, non-finite, non-positive, or below-minimum maxima fail closed.
  public func effectiveLimits(configuration: Configuration?) -> EffectiveLimits? {
    guard let configuration else { return nil }
    return effectiveLimits(
      reportedMaxVoltage: Double(configuration.maxVoltage),
      reportedMaxCurrent: Double(configuration.maxSingleModuleCurrent)
    )
  }

  /// Variant useful to consumers that persist only the two confirmed maxima.
  public func effectiveLimits(
    reportedMaxVoltage: Double?,
    reportedMaxCurrent: Double?
  ) -> EffectiveLimits? {
    try? validatedEffectiveLimits(
      reportedMaxVoltage: reportedMaxVoltage,
      reportedMaxCurrent: reportedMaxCurrent
    )
  }

  public func validatedEffectiveLimits(
    reportedMaxVoltage: Double?,
    reportedMaxCurrent: Double?
  ) throws -> EffectiveLimits {
    guard let reportedMaxVoltage,
      let reportedMaxCurrent,
      reportedMaxVoltage.isFinite,
      reportedMaxCurrent.isFinite,
      reportedMaxVoltage > 0,
      reportedMaxCurrent > 0,
      let voltageMinimumWire = wireFloat32(voltage.minimum),
      let currentMinimumWire = wireFloat32(current.minimum),
      let reportedVoltageWire = wireFloat32(reportedMaxVoltage),
      let reportedCurrentWire = wireFloat32(reportedMaxCurrent),
      let modelVoltageMaximumWire = wireFloat32(voltage.maximum),
      let modelCurrentMaximumWire = wireFloat32(current.maximum)
    else {
      throw HWCDQProtocolError.profileDeviceLimitsInvalid
    }

    var voltageMaximum = min(reportedVoltageWire, modelVoltageMaximumWire)
    var currentMaximum = min(reportedCurrentWire, modelCurrentMaximumWire)
    guard voltageMaximum >= voltageMinimumWire,
      currentMaximum >= currentMinimumWire
    else {
      throw HWCDQProtocolError.profileDeviceLimitsInvalid
    }

    if voltageMaximum == voltageMinimumWire { voltageMaximum = voltage.minimum }
    if currentMaximum == currentMinimumWire { currentMaximum = current.minimum }

    guard
      let voltageRange = try? NumericRange(
        minimum: voltage.minimum,
        maximum: voltageMaximum
      ),
      let currentRange = try? NumericRange(
        minimum: current.minimum,
        maximum: currentMaximum
      )
    else {
      throw HWCDQProtocolError.profileDeviceLimitsInvalid
    }
    return EffectiveLimits(voltage: voltageRange, current: currentRange)
  }
}

public enum HW178P {
  public static let profileID = "pidzoom-hw178p"

  public static let profile: ChargerProfile = {
    // These literals are compile-time-valid and intentionally force-unwrapped.
    let voltage = try! NumericRange(minimum: 50.0, maximum: 178.0)
    let current = try! NumericRange(minimum: 0.01, maximum: 14.0)
    return ChargerProfile(
      model: "HW178P",
      displayName: "Pidzoom Portable charger HW178P",
      gatt: GATTProfile(
        serviceUUID: "FFE1",
        receiveUUID: "FFE2",
        transmitUUID: "FFE3"
      ),
      voltage: voltage,
      current: current
    )
  }()
}

internal func wireFloat32(_ value: Double) -> Double? {
  guard value.isFinite, value > 0 else { return nil }
  let rounded = Float(value)
  guard rounded.isFinite, rounded > 0 else { return nil }
  return Double(rounded)
}
