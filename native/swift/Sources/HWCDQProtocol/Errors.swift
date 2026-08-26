import Foundation

/// Fail-closed errors emitted by the wire codec and stream decoder.
///
/// `conformanceCode` is stable across releases and is intended for shared
/// cross-language conformance vectors. Human-readable descriptions may evolve.
public enum HWCDQProtocolError: Error, Equatable, Sendable {
  case credentialLength
  case credentialNonHex
  case credentialType
  case scalarType
  case scalarNonFinite
  case scalarNonPositive
  case scalarNotFloat32
  case packetTruncated
  case packetLengthMinimum
  case lengthMismatch(declared: Int, actual: Int)
  case checksumMismatch(expected: UInt8, actual: UInt8)
  case streamLengthInvalid(UInt8?)
  case streamFrameInvalid(underlyingCode: String)
  case profileValueInvalid
  case profileOutOfRange
  case profileDeviceLimitsInvalid

  public var conformanceCode: String {
    switch self {
    case .credentialLength:
      return "credential.length"
    case .credentialNonHex:
      return "credential.non_hex"
    case .credentialType:
      return "credential.type"
    case .scalarType:
      return "scalar.type"
    case .scalarNonFinite:
      return "scalar.non_finite"
    case .scalarNonPositive:
      return "scalar.non_positive"
    case .scalarNotFloat32:
      return "scalar.not_float32"
    case .packetTruncated:
      return "packet.truncated"
    case .packetLengthMinimum:
      return "packet.length.minimum"
    case .lengthMismatch:
      return "packet.length.mismatch"
    case .checksumMismatch:
      return "packet.checksum.mismatch"
    case .streamLengthInvalid:
      return "stream.length.invalid"
    case .streamFrameInvalid:
      return "stream.frame.invalid"
    case .profileValueInvalid:
      return "profile.value.invalid"
    case .profileOutOfRange:
      return "profile.out_of_range"
    case .profileDeviceLimitsInvalid:
      return "profile.device_limits.invalid"
    }
  }
}

extension HWCDQProtocolError: CustomStringConvertible {
  public var description: String {
    switch self {
    case .credentialLength:
      return "credential must be exactly 32 ASCII hexadecimal characters"
    case .credentialNonHex:
      return "credential must contain only ASCII hexadecimal characters"
    case .credentialType:
      return "credential must be text"
    case .scalarType:
      return "scalar must be a real number"
    case .scalarNonFinite:
      return "value must be finite"
    case .scalarNonPositive:
      return "value must be greater than zero"
    case .scalarNotFloat32:
      return "value is not representable as a positive finite float32"
    case .packetTruncated:
      return "packet is truncated; at least 3 bytes are required"
    case .packetLengthMinimum:
      return "length byte must include at least opcode and checksum"
    case .lengthMismatch(let declared, let actual):
      return "length mismatch: header declares \(declared) total bytes, received \(actual)"
    case .checksumMismatch(let expected, let actual):
      return String(
        format: "checksum mismatch: packet has 0x%02X, expected 0x%02X",
        actual,
        expected
      )
    case .streamLengthInvalid(let value):
      guard let value else { return "maximum frame size must be in 3...256" }
      return String(format: "invalid frame length byte 0x%02X", value)
    case .streamFrameInvalid(let underlyingCode):
      return "invalid frame in notification stream (\(underlyingCode))"
    case .profileValueInvalid:
      return "profile value must be a positive finite binary32"
    case .profileOutOfRange:
      return "value is outside the charger profile range"
    case .profileDeviceLimitsInvalid:
      return "device-reported limits are missing or invalid"
    }
  }
}
