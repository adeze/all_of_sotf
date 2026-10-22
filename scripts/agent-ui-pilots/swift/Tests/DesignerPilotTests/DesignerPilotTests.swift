import A2UICore
import DesignerPilot
import Foundation
import Testing

@MainActor
struct DesignerPilotTests {
  private var fixtureURL: URL {
    URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
      .deletingLastPathComponent().deletingLastPathComponent()
      .appendingPathComponent("fixtures/workbench.json")
  }

  @Test func sharedFixtureUsesSDKBindingsAndProducesOnlySupportedCommand() throws {
    let fixture = try WorkbenchFixture.load(fixtureURL)
    let pilot = try DesignerPilot(fixture: fixture)
    let controls = try #require(pilot.surface.rootNode).children(for: "children")
    #expect(controls.filter { $0.type == "Slider" }.count == 4)
    let minimum = try #require(controls.first { $0.id == "min_freq" })
    let binding: DataBinding<Double> = try #require(minimum.dataBinding(for: "value"))
    #expect(binding.value == fixture.designer.minFrequency)
    binding.set(77)
    #expect(try pilot.proposal().patch.minFrequency == 77)
    let payload = try #require(
      JSONSerialization.jsonObject(with: pilot.proposalData()) as? [String: Any])
    #expect(payload["operation"] as? String == "update_designer")
    #expect(payload["expected_revision"] as? String == fixture.revision)
    #expect(pilot.validationErrors.isEmpty)
  }

  @Test func unknownComponentsAndInvalidDraftAreRejected() throws {
    let pilot = try DesignerPilot(fixture: WorkbenchFixture.load(fixtureURL))
    pilot.processor.process(
      message: try MessageParser().parse(
        jsonString: """
          {"version":"v0.9.1","updateComponents":{"surfaceId":"designer","components":[{"id":"intruder","component":"ExecuteCode"}]}}
          """))
    #expect(!pilot.validationErrors.isEmpty)
    #expect(pilot.surface.componentsModel.components["intruder"] == nil)
    try pilot.surface.dataModel.setThrowing("/designer/min_freq", value: 2000)
    try pilot.surface.dataModel.setThrowing("/designer/max_freq", value: 100)
    #expect(throws: PilotError.self) { try pilot.proposal() }
  }
}
