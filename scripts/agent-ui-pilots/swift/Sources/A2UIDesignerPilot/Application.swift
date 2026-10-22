import AppKit
import DesignerPilot
import Foundation
import SwiftUI

@main
enum Main {
  @MainActor static func main() throws {
    let arguments = Array(CommandLine.arguments.dropFirst())
    guard let index = arguments.firstIndex(of: "--fixture"), arguments.indices.contains(index + 1)
    else {
      throw NSError(
        domain: "A2UIPilot", code: 2,
        userInfo: [
          NSLocalizedDescriptionKey:
            "Usage: a2ui-designer-pilot --fixture PATH [--verify | --snapshot PATH]"
        ])
    }
    let pilot = try DesignerPilot(
      fixture: WorkbenchFixture.load(URL(fileURLWithPath: arguments[index + 1])))
    if arguments.contains("--verify") {
      FileHandle.standardOutput.write(try pilot.proposalData())
      return
    }
    if let index = arguments.firstIndex(of: "--snapshot"), arguments.indices.contains(index + 1) {
      _ = NSApplication.shared
      let view = NSHostingView(rootView: DesignerPanel(pilot: pilot))
      view.frame = NSRect(origin: .zero, size: view.fittingSize)
      view.layoutSubtreeIfNeeded()
      guard let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else {
        throw NSError(
          domain: "A2UIPilot", code: 3,
          userInfo: [NSLocalizedDescriptionKey: "SwiftUI snapshot failed"])
      }
      view.cacheDisplay(in: view.bounds, to: bitmap)
      guard
        let png = bitmap.representation(using: .png, properties: [:])
      else {
        throw NSError(
          domain: "A2UIPilot", code: 3,
          userInfo: [NSLocalizedDescriptionKey: "SwiftUI snapshot failed"])
      }
      let destination = URL(fileURLWithPath: arguments[index + 1])
      try FileManager.default.createDirectory(
        at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
      try png.write(to: destination, options: .withoutOverwriting)
      print("Rendered native SwiftUI A2UI surface: \(destination.path)")
      return
    }
    let app = NSApplication.shared
    app.setActivationPolicy(.regular)
    let window = NSWindow(
      contentViewController: NSHostingController(rootView: DesignerPanel(pilot: pilot)))
    window.title = "A2UI Designer — Offline Pilot"
    window.setContentSize(NSSize(width: 640, height: 660))
    window.styleMask = [.titled, .closable, .miniaturizable, .resizable]
    window.center()
    window.makeKeyAndOrderFront(nil)
    app.activate(ignoringOtherApps: true)
    withExtendedLifetime(window) { app.run() }
  }
}
