// swift-tools-version: 6.0

import PackageDescription

let package = Package(
  name: "HWCDQProtocol",
  platforms: [
    .iOS(.v13),
    .macOS(.v12),
  ],
  products: [
    .library(name: "HWCDQProtocol", targets: ["HWCDQProtocol"])
  ],
  targets: [
    .target(name: "HWCDQProtocol"),
    .testTarget(
      name: "HWCDQProtocolTests",
      dependencies: ["HWCDQProtocol"]
    ),
  ]
)
