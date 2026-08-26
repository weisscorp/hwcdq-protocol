import Foundation

/// A validated authentication credential in the charger's on-wire format.
///
/// The digest is deliberately private and never appears in descriptions.
public struct Credential: Equatable, Hashable, Sendable, CustomStringConvertible,
  CustomDebugStringConvertible
{
  public static let apkFallbackDigest = "D41D8CD98F00B204E9800998ECF8427E"

  private let digest: String

  private init(validatedDigest: String) {
    digest =
      validatedDigest.caseInsensitiveCompare(Self.apkFallbackDigest) == .orderedSame
      ? Self.apkFallbackDigest
      : validatedDigest.lowercased()
  }

  /// Reproduce the exact static fallback used by the recovered Android APK.
  public static func apkFallback() -> Credential {
    Credential(validatedDigest: apkFallbackDigest)
  }

  /// Construct a credential from an already-derived 32-character MD5 hex digest.
  public static func fromDigest(_ digest: String) throws -> Credential {
    guard digest.utf8.count == 32 else {
      throw HWCDQProtocolError.credentialLength
    }
    guard
      digest.unicodeScalars.allSatisfy({ scalar in
        switch scalar.value {
        case 48...57, 65...70, 97...102:
          return true
        default:
          return false
        }
      })
    else {
      throw HWCDQProtocolError.credentialNonHex
    }
    return Credential(validatedDigest: digest)
  }

  internal var wireValue: String { digest }

  public var description: String { "Credential([REDACTED])" }
  public var debugDescription: String { description }
}
