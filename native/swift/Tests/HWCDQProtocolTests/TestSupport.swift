import Foundation

extension Data {
  init(hex: String) {
    precondition(hex.count.isMultiple(of: 2))
    var bytes: [UInt8] = []
    bytes.reserveCapacity(hex.count / 2)
    var index = hex.startIndex
    while index < hex.endIndex {
      let next = hex.index(index, offsetBy: 2)
      bytes.append(UInt8(hex[index..<next], radix: 16)!)
      index = next
    }
    self.init(bytes)
  }

  var hex: String { map { String(format: "%02x", $0) }.joined() }
}

extension Float {
  var littleEndianData: Data {
    let bits = bitPattern
    return Data([
      UInt8(truncatingIfNeeded: bits),
      UInt8(truncatingIfNeeded: bits >> 8),
      UInt8(truncatingIfNeeded: bits >> 16),
      UInt8(truncatingIfNeeded: bits >> 24),
    ])
  }
}

func testFrame(opcode: UInt8, payload: Data) -> Data {
  precondition(payload.count <= 253)
  let checksum = payload.reduce(opcode) { $0 &+ $1 }
  var frame = Data([UInt8(payload.count + 2), opcode])
  frame.append(payload)
  frame.append(checksum)
  return frame
}
