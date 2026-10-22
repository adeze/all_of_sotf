// swift-tools-version: 6.4
import PackageDescription

let package = Package(
  name: "A2UIDesignerPilot",
  platforms: [.macOS(.v15)],
  products: [.executable(name: "a2ui-designer-pilot", targets: ["A2UIDesignerPilot"])],
  dependencies: [.package(path: ".sdk/a2ui")],
  targets: [
    .target(
      name: "DesignerPilot",
      dependencies: [
        .product(name: "A2UISwiftCore", package: "a2ui"),
        .product(name: "BasicCatalog", package: "a2ui"),
        .product(name: "A2UISwiftUI", package: "a2ui"),
        .product(name: "BasicCatalogSwiftUI", package: "a2ui"),
      ]),
    .executableTarget(name: "A2UIDesignerPilot", dependencies: ["DesignerPilot"]),
    .testTarget(name: "DesignerPilotTests", dependencies: ["DesignerPilot"]),
  ]
)
