import AppKit
import CoreGraphics
import ScreenCaptureKit

func save(_ value: [String: Any], _ url: URL) throws {
    try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .prettyPrinted]).write(to: url)
}

final class Cover: NSObject, NSApplicationDelegate {
    let root: URL
    let pid: Int32
    var window: NSWindow!
    var timer: Timer?
    init(root: URL, pid: Int32) { self.root = root; self.pid = pid }
    func applicationDidFinishLaunching(_ notification: Notification) {
        do {
            guard let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]],
                  let target = windows.first(where: { ($0[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid && ($0[kCGWindowLayer as String] as? Int) == 0 }),
                  let bounds = target[kCGWindowBounds as String] as? [String: Any],
                  let rect = CGRect(dictionaryRepresentation: bounds as CFDictionary), let screen = NSScreen.screens.first else {
                throw NSError(domain: "GameQA", code: 1, userInfo: [NSLocalizedDescriptionKey: "Godot window not found"])
            }
            let frame = NSRect(x: rect.minX - 5, y: screen.frame.maxY - rect.maxY - 5,
                               width: rect.width + 10, height: rect.height + 10)
            window = NSWindow(contentRect: frame, styleMask: .borderless, backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            window.level = .floating
            window.backgroundColor = NSColor(srgbRed: 200/255, green: 30/255, blue: 180/255, alpha: 1)
            window.isOpaque = true; window.hasShadow = false
            window.title = "GameQA Godot cover"
            window.orderFrontRegardless()
            try save(["pid": ProcessInfo.processInfo.processIdentifier, "window": window.windowNumber,
                      "target": target, "frontmost": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1], root.appendingPathComponent("cover-ready.json"))
            timer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [self] _ in
                if FileManager.default.fileExists(atPath: root.appendingPathComponent("cover-stop").path) {
                    timer?.invalidate(); window.close(); NSApp.terminate(nil)
                }
            }
        } catch {
            try? save(["error": String(describing: error)], root.appendingPathComponent("cover-error.json"))
            NSApp.terminate(nil)
        }
    }
}

@main
struct GodotProbe {
    static func main() async {
        guard CommandLine.arguments.count == 4, let pid = Int32(CommandLine.arguments[3]) else { exit(2) }
        let role = CommandLine.arguments[1]
        let root = URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true)
        let app = NSApplication.shared; app.setActivationPolicy(.accessory)
        if role == "cover" {
            let delegate = Cover(root: root, pid: pid); app.delegate = delegate; app.run(); return
        }
        guard role == "proof" else { exit(2) }
        do {
            let ready = try JSONSerialization.jsonObject(with: Data(contentsOf: root.appendingPathComponent("cover-ready.json"))) as! [String: Any]
            let target = ready["target"] as! [String: Any]
            let bounds = target[kCGWindowBounds as String] as! [String: Any]
            guard let rect = CGRect(dictionaryRepresentation: bounds as CFDictionary) else { exit(1) }
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
            guard let display = content.displays.first(where: { $0.frame.contains(rect) }) else { exit(1) }
            let config = SCStreamConfiguration(); config.width = display.width; config.height = display.height; config.showsCursor = false
            let image = try await SCScreenshotManager.captureImage(contentFilter: SCContentFilter(display: display, excludingApplications: [], exceptingWindows: []), configuration: config)
            let bitmap = NSBitmapImageRep(cgImage: image)
            try bitmap.representation(using: .png, properties: [:])!.write(to: root.appendingPathComponent("desktop-covered.png"))
            let sx = CGFloat(image.width) / display.frame.width, sy = CGFloat(image.height) / display.frame.height
            let pixelRect = CGRect(x: (rect.minX - display.frame.minX) * sx, y: (rect.minY - display.frame.minY) * sy, width: rect.width * sx, height: rect.height * sy)
            guard let crop = image.cropping(to: pixelRect) else { exit(1) }
            let pixels = NSBitmapImageRep(cgImage: crop)
            try pixels.representation(using: .png, properties: [:])!.write(to: root.appendingPathComponent("target-covered.png"))
            var mismatch = 0
            for y in 0..<pixels.pixelsHigh {
                for x in 0..<pixels.pixelsWide {
                    guard let color = pixels.colorAt(x: x, y: y)?.usingColorSpace(.sRGB) else { exit(1) }
                    if abs(color.redComponent * 255 - 200) > 8 || abs(color.greenComponent * 255 - 30) > 8 || abs(color.blueComponent * 255 - 180) > 8 { mismatch += 1 }
                }
            }
            let frontmost = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1
            let originalFrontmost = (ready["frontmost"] as? NSNumber)?.int32Value
            let ok = mismatch == 0 && frontmost == originalFrontmost && frontmost != pid
            try save(["ok": ok, "checked_pixels": pixels.pixelsWide * pixels.pixelsHigh, "mismatched_pixels": mismatch, "frontmost": frontmost, "target_pid": pid], root.appendingPathComponent("proof.json"))
            if !ok { exit(1) }
        } catch { fputs("\(error)\n", stderr); exit(1) }
    }
}
