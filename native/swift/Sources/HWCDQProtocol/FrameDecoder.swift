import Foundation

/// Reassembles arbitrary notification chunks into validated HWCDQ packets.
///
/// The protocol has no sync word. Any malformed complete frame clears buffered
/// state and fails closed instead of guessing where the next packet starts.
public struct IncrementalFrameDecoder: Sendable {
  public let maximumFrameSize: Int
  private var buffer: [UInt8] = []

  public init(maximumFrameSize: Int = 256) throws {
    guard (3...256).contains(maximumFrameSize) else {
      throw HWCDQProtocolError.streamLengthInvalid(nil)
    }
    self.maximumFrameSize = maximumFrameSize
  }

  public var bufferedByteCount: Int { buffer.count }

  public mutating func reset() {
    buffer.removeAll(keepingCapacity: true)
  }

  public mutating func feed(_ chunk: Data) throws -> [DecodedPacket] {
    guard !chunk.isEmpty else { return [] }
    buffer.append(contentsOf: chunk)
    var packets: [DecodedPacket] = []

    do {
      while let first = buffer.first {
        let total = Int(first) + 1
        guard first >= 2, total <= maximumFrameSize else {
          throw HWCDQProtocolError.streamLengthInvalid(first)
        }
        guard buffer.count >= total else { break }
        let frame = Data(buffer.prefix(total))
        buffer.removeFirst(total)
        do {
          packets.append(try HWCDQCodec.decode(frame))
        } catch let error as HWCDQProtocolError {
          throw HWCDQProtocolError.streamFrameInvalid(
            underlyingCode: error.conformanceCode
          )
        }
      }
    } catch {
      reset()
      throw error
    }
    return packets
  }
}
