import A2UICore
import A2UISwiftUI
import BasicCatalog
import BasicCatalogSwiftUI
import Foundation
import SwiftUI
import Synchronization

public enum PilotError: Error {
  case invalidFixture
  case invalidLimits
  case schema(String)
  case missingSurface
}

public struct DesignerLimits: Codable, Equatable, Sendable {
  public var minFrequency: Double
  public var maxFrequency: Double
  public var maxCutDB: Double
  public var maxBoostDB: Double

  private enum CodingKeys: String, CodingKey {
    case minFrequency = "min_freq"
    case maxFrequency = "max_freq"
    case maxCutDB = "max_cut_db"
    case maxBoostDB = "max_boost_db"
  }

  public func validate() throws {
    guard [minFrequency, maxFrequency, maxCutDB, maxBoostDB].allSatisfy(\.isFinite),
      (20...20000).contains(minFrequency), (20...20000).contains(maxFrequency),
      minFrequency < maxFrequency, (0...25).contains(maxCutDB),
      (0...25).contains(maxBoostDB)
    else { throw PilotError.invalidLimits }
  }
}

public struct WorkbenchFixture: Decodable, Sendable {
  public let revision: String
  public let designer: DesignerLimits
  public let chatEnabled: Bool
  public let offlineOnly: Bool

  private enum CodingKeys: String, CodingKey {
    case revision, designer
    case chatEnabled = "chat_enabled"
    case offlineOnly = "offline_only"
  }

  public static func load(_ url: URL) throws -> Self {
    let fixture = try JSONDecoder().decode(Self.self, from: Data(contentsOf: url))
    guard !fixture.chatEnabled, fixture.offlineOnly, !fixture.revision.isEmpty else {
      throw PilotError.invalidFixture
    }
    try fixture.designer.validate()
    return fixture
  }
}

public struct DesignerProposal: Encodable, Sendable {
  public let operation = "update_designer"
  public let expectedRevision: String
  public let patch: DesignerLimits

  private enum CodingKeys: String, CodingKey {
    case operation, patch
    case expectedRevision = "expected_revision"
  }
}

private final class ValidationErrors: ActionHandling, Sendable {
  private let storage = Mutex<[String]>([])
  var errors: [String] { storage.withLock { $0 } }
  func handle(action: ResolvedAction, from surfaceID: String) {}
  func handle(error: RendererError, from surfaceID: String) {
    storage.withLock { $0.append(String(describing: error)) }
  }
}

/// SDK-owned data binding is a draft; only an explicit proposal leaves this pilot.
@MainActor
public final class DesignerPilot {
  public let processor: MessageProcessor
  public let surface: SurfaceViewModel
  public let fixture: WorkbenchFixture
  private let errors: ValidationErrors

  public init(fixture: WorkbenchFixture) throws {
    self.fixture = fixture
    let errors = ValidationErrors()
    self.errors = errors
    let processor = MessageProcessor(
      catalogs: [BasicCatalog.makeCatalog(version: .v091)],
      actionHandler: errors, validationConfig: .strict
    )
    self.processor = processor
    let values = try JSONSerialization.jsonObject(with: JSONEncoder().encode(fixture.designer))
    let controls: [(String, String, Double, Double)] = [
      ("min_freq", "Minimum frequency (Hz)", 20, 20000),
      ("max_freq", "Maximum frequency (Hz)", 20, 20000),
      ("max_cut_db", "Maximum cut (dB)", 0, 25),
      ("max_boost_db", "Maximum boost (dB)", 0, 25),
    ]
    let components: [[String: Any]] =
      [
        ["id": "root", "component": "Column", "children": ["title"] + controls.map(\.0)],
        ["id": "title", "component": "Text", "text": "Offline Designer", "variant": "h2"],
      ]
      + controls.map { key, label, low, high in
        [
          "id": key, "component": "Slider", "label": label,
          "min": low, "max": high, "value": ["path": "/designer/\(key)"],
        ]
      }
    let messages: [[String: Any]] = [
      [
        "version": "v0.9.1",
        "createSurface": [
          "surfaceId": "designer", "catalogId": BasicCatalog.catalogURI(version: .v091),
        ],
      ],
      [
        "version": "v0.9.1",
        "updateDataModel": [
          "surfaceId": "designer", "path": "/", "value": ["designer": values],
        ],
      ],
      [
        "version": "v0.9.1",
        "updateComponents": [
          "surfaceId": "designer", "components": components,
        ],
      ],
    ]
    for message in messages {
      let data = try JSONSerialization.data(withJSONObject: message)
      processor.process(message: try MessageParser().decode(jsonData: data))
    }
    if let error = errors.errors.first { throw PilotError.schema(error) }
    guard let surface = processor.surfaceGroupModel["designer"], surface.rootNode != nil else {
      throw PilotError.missingSurface
    }
    self.surface = surface
  }

  public func proposal() throws -> DesignerProposal {
    guard let value = surface.dataModel.get("/designer") else { throw PilotError.invalidLimits }
    let limits = try JSONDecoder().decode(DesignerLimits.self, from: JSONEncoder().encode(value))
    try limits.validate()
    return DesignerProposal(expectedRevision: fixture.revision, patch: limits)
  }

  public func proposalData() throws -> Data {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    return try encoder.encode(proposal())
  }

  public var validationErrors: [String] { errors.errors }
}

public struct DesignerPanel: View {
  private let pilot: DesignerPilot
  @State private var proposal = ""

  public init(pilot: DesignerPilot) { self.pilot = pilot }

  public var body: some View {
    VStack(alignment: .leading, spacing: 20) {
      Surface(viewModel: pilot.surface, catalogs: BasicCatalogImplementation.allCatalogs)
      Text("Synthetic fixture · chat disabled · no receiver or network connection")
        .font(.caption).foregroundStyle(.secondary)
      Button("Preview supported parameter proposal") {
        do { proposal = String(decoding: try pilot.proposalData(), as: UTF8.self) } catch {
          proposal = "Proposal rejected: \(error)"
        }
      }
      if !proposal.isEmpty {
        Text(proposal).font(.caption.monospaced()).textSelection(.enabled)
      }
    }
    .padding(24)
    .frame(width: 640)
    .background(Color(nsColor: .windowBackgroundColor))
  }
}
