// Inspect only the release desktop's PID-owned normal window; never request TCC grants.
import AppKit
import ApplicationServices
import CoreGraphics
import Foundation

func fail(_ message: String) -> Never {
    fputs("\(message)\n", stderr)
    exit(1)
}

func ownedWindow(pid: Int32) -> CGWindowID? {
    guard let windows = CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID)
        as? [[String: Any]] else { return nil }
    let matches = windows.compactMap { window -> CGWindowID? in
        guard (window[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid,
              (window[kCGWindowLayer as String] as? NSNumber)?.intValue == 0,
              let number = window[kCGWindowNumber as String] as? NSNumber,
              let bounds = window[kCGWindowBounds as String] as? [String: Any],
              ((bounds["Width"] as? NSNumber)?.doubleValue ?? 0) > 600,
              ((bounds["Height"] as? NSNumber)?.doubleValue ?? 0) > 400 else { return nil }
        return number.uint32Value
    }
    return matches.count == 1 ? matches[0] : nil
}

func attribute(_ element: AXUIElement, _ name: CFString) -> CFTypeRef? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name, &value) == .success else { return nil }
    return value
}

let args = CommandLine.arguments
guard args.count >= 2 else { fail("usage: desktop_runtime_macos_window prereq|desktop-window|desktop-ax|stats") }
switch args[1] {
case "prereq":
    print("accessibility_preflight=\(AXIsProcessTrusted())")
    print("screen_capture_preflight=\(CGPreflightScreenCaptureAccess())")
case "desktop-window":
    guard args.count == 3, let pid = Int32(args[2]) else { fail("desktop-window requires PID") }
    guard let window = ownedWindow(pid: pid) else {
        if let windows = CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID)
            as? [[String: Any]] {
            for item in windows where (item[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid {
                fputs("owned-window pid=\(pid) id=\(item[kCGWindowNumber as String] ?? "missing") layer=\(item[kCGWindowLayer as String] ?? "missing") bounds=\(item[kCGWindowBounds as String] ?? "missing")\n", stderr)
            }
        }
        fail("expected exactly one visible normal desktop window owned by PID \(pid)")
    }
    print(window)
case "desktop-ax":
    guard args.count == 3, let pid = Int32(args[2]) else { fail("desktop-ax requires PID") }
    let app = AXUIElementCreateApplication(pid)
    guard let windows = attribute(app, kAXWindowsAttribute as CFString) as? [AXUIElement], windows.count == 1,
          let role = attribute(windows[0], kAXRoleAttribute as CFString) as? String, role == "AXWindow",
          let title = attribute(windows[0], kAXTitleAttribute as CFString) as? String,
          title == "SotF" else {
        fail("expected one SotF AXWindow owned by PID \(pid)")
    }
    let json: [String: Any] = ["pid": pid, "role": role, "title": title]
    guard let data = try? JSONSerialization.data(withJSONObject: json),
          let text = String(data: data, encoding: .utf8) else { fail("cannot encode AX evidence") }
    print(text)
case "stats":
    guard args.count == 3,
          let data = try? Data(contentsOf: URL(fileURLWithPath: args[2])),
          let image = NSBitmapImageRep(data: data),
          image.pixelsWide > 600, image.pixelsHigh > 400 else {
        fail("desktop screenshot is missing or too small")
    }
    var sum = 0.0
    var squares = 0.0
    for row in 0..<32 {
        for column in 0..<32 {
            let x = column * (image.pixelsWide - 1) / 31
            let y = row * (image.pixelsHigh - 1) / 31
            guard let color = image.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB) else {
                fail("cannot inspect desktop screenshot pixels")
            }
            let luminance = 0.2126 * color.redComponent
                + 0.7152 * color.greenComponent + 0.0722 * color.blueComponent
            sum += luminance
            squares += luminance * luminance
        }
    }
    let mean = sum / 1024.0
    let variance = squares / 1024.0 - mean * mean
    print(String(format: "width=%d height=%d mean=%.6f variance=%.6f",
                 image.pixelsWide, image.pixelsHigh, mean, variance))
    guard mean > 0.02, mean < 0.98, variance > 0.0005 else {
        fail("desktop screenshot is blank or unpainted")
    }
default:
    fail("unknown mode: \(args[1])")
}
