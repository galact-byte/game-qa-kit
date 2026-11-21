// 独立 ScreenCaptureKit 探针；只在临时云端桌面运行自写窗口。
import AppKit
import ScreenCaptureKit
import CoreMedia
import CoreVideo
import CoreImage

struct ProbeError: Error, CustomStringConvertible {
    let description: String
    init(_ message: String) { description = message }
}

func saveJSON(_ object: [String: Any], _ url: URL) throws {
    try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys]).write(to: url, options: .atomic)
}

func savePNG(_ image: CGImage, _ url: URL) throws {
    guard let png = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]) else {
        throw ProbeError("PNG encoding failed")
    }
    try png.write(to: url, options: .atomic)
}

final class CounterView: NSView {
    var counter = 1
    let cover: Bool
    init(cover: Bool) { self.cover = cover; super.init(frame: .zero) }
    required init?(coder: NSCoder) { fatalError("init(coder:) unsupported") }
    override func draw(_ dirtyRect: NSRect) {
        if cover {
            NSColor(srgbRed: 200/255, green: 30/255, blue: 180/255, alpha: 1).setFill()
            bounds.fill()
            return
        }
        NSColor(srgbRed: 20/255, green: 50/255, blue: 80/255, alpha: 1).setFill()
        bounds.fill()
        for bit in 0..<16 {
            let bright = counter & (1 << bit) != 0
            NSColor(srgbRed: bright ? 240/255 : 20/255,
                    green: bright ? 240/255 : 20/255,
                    blue: bright ? 240/255 : 20/255, alpha: 1).setFill()
            NSRect(x: CGFloat(bit) * bounds.width / 16, y: bounds.height/3,
                   width: bounds.width/16 + 1, height: bounds.height/3).fill()
        }
        let text = "GameQA counter \(counter)"
        text.draw(at: NSPoint(x: 8, y: 8), withAttributes: [.foregroundColor: NSColor.white])
    }
}

final class Fixture: NSObject, NSApplicationDelegate {
    let root: URL
    let cover: Bool
    var window: NSWindow!
    var timer: Timer?
    init(root: URL, cover: Bool) { self.root = root; self.cover = cover }
    func applicationDidFinishLaunching(_ notification: Notification) {
        let view = CounterView(cover: cover)
        let rect = cover ? NSRect(x: 90, y: 90, width: 600, height: 400) : NSRect(x: 100, y: 100, width: 320, height: 180)
        window = NSWindow(contentRect: rect, styleMask: .borderless, backing: .buffered, defer: false)
        window.title = cover ? "GameQA cover" : "GameQA target"
        window.isReleasedWhenClosed = false
        window.backgroundColor = .black
        window.isOpaque = true
        window.hasShadow = false
        window.contentView = view
        if cover { window.level = .floating }
        window.orderFrontRegardless()
        view.display()
        try? saveJSON(["pid": ProcessInfo.processInfo.processIdentifier, "window": window.windowNumber],
                      root.appendingPathComponent(cover ? "cover-ready.json" : "target-ready.json"))
        timer = Timer.scheduledTimer(withTimeInterval: 0.15, repeats: true) { [self] _ in
            if FileManager.default.fileExists(atPath: root.appendingPathComponent("stop").path) {
                timer?.invalidate(); window.close(); NSApp.terminate(nil); return
            }
            if !cover {
                if FileManager.default.fileExists(atPath: root.appendingPathComponent("resize").path) {
                    window.setContentSize(NSSize(width: 400, height: 240))
                }
                if !FileManager.default.fileExists(atPath: root.appendingPathComponent("freeze").path) {
                    view.counter += 1
                    view.needsDisplay = true
                }
            }
        }
    }
}

final class Frames: NSObject, SCStreamOutput, SCStreamDelegate {
    let lock = NSLock()
    let context = CIContext()
    var serial = 0
    var image: CGImage?
    var timestamp = 0.0
    var failure: String?
    func stream(_ stream: SCStream, didStopWithError error: Error) {
        lock.lock(); failure = String(describing: error); lock.unlock()
    }
    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen, sampleBuffer.isValid,
              let array = CMSampleBufferGetSampleAttachmentsArray(sampleBuffer, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
              let rawStatus = array.first?[.status] as? Int,
              SCFrameStatus(rawValue: rawStatus) == .complete,
              let pixelBuffer = sampleBuffer.imageBuffer else { return }
        let ci = CIImage(cvPixelBuffer: pixelBuffer)
        guard let cg = context.createCGImage(ci, from: ci.extent) else { return }
        lock.lock()
        serial += 1; image = cg; timestamp = sampleBuffer.presentationTimeStamp.seconds
        lock.unlock()
    }
    func snapshot() throws -> (Int, CGImage, Double) {
        lock.lock(); defer { lock.unlock() }
        if let failure = failure { throw ProbeError(failure) }
        guard let image = image else { throw ProbeError("No complete ScreenCaptureKit frame") }
        return (serial, image, timestamp)
    }
}

func decode(_ image: CGImage) throws -> Int {
    var pixels = [UInt8](repeating: 0, count: image.width * image.height * 4)
    let ok = pixels.withUnsafeMutableBytes { bytes -> Bool in
        guard let ctx = CGContext(data: bytes.baseAddress, width: image.width, height: image.height,
                                  bitsPerComponent: 8, bytesPerRow: image.width*4,
                                  space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
        return true
    }
    guard ok else { throw ProbeError("Pixel conversion failed") }
    var value = 0
    for bit in 0..<16 {
        let x = Int((Double(bit) + 0.5) * Double(image.width) / 16)
        let i = ((image.height/2)*image.width + x)*4
        let rgb = Array(pixels[i..<(i+3)])
        guard Int(rgb.max()!) - Int(rgb.min()!) <= 10,
              rgb.max()! < 40 || rgb.min()! > 220 else { throw ProbeError("Invalid counter pixel \(rgb)") }
        if rgb.min()! > 220 { value |= 1 << bit }
    }
    return value
}

func pause(_ seconds: Double) async throws {
    try await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
}

func waitReady(_ url: URL, process: Process) async throws -> [String: Any] {
    for _ in 0..<100 {
        if let data = try? Data(contentsOf: url), let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] { return value }
        if !process.isRunning { throw ProbeError("Fixture exited before ready") }
        try await pause(0.1)
    }
    throw ProbeError("Fixture ready timeout")
}

func launch(_ role: String, root: URL) throws -> Process {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: CommandLine.arguments[0])
    p.arguments = [role, root.path]
    try p.run()
    return p
}

func growth(_ rows: [[String: Any]]) -> Bool {
    let counts = rows.compactMap { $0["counter"] as? Int }
    return counts.count >= 4 && zip(counts, counts.dropFirst()).allSatisfy { $1 > $0 }
}

func collect(_ name: String, frames: Frames, root: URL, frozen: Bool = false) async throws -> [[String: Any]] {
    var rows = [[String: Any]]()
    for index in 0..<4 {
        try await pause(0.6)
        let (serial, image, timestamp) = try frames.snapshot()
        let counter = try decode(image)
        let file = "\(name)-\(index).png"
        try savePNG(image, root.appendingPathComponent(file))
        rows.append(["file": file, "counter": counter, "serial": serial, "pts": timestamp,
                     "width": image.width, "height": image.height])
    }
    if frozen {
        guard Set(rows.compactMap { $0["counter"] as? Int }).count == 1, !growth(rows) else {
            throw ProbeError("Frozen counter was accepted as growth")
        }
    } else {
        guard growth(rows) else { throw ProbeError("Counter did not progress: \(rows)") }
        let serials = rows.compactMap { $0["serial"] as? Int }
        let stamps = rows.compactMap { $0["pts"] as? Double }
        guard zip(serials, serials.dropFirst()).allSatisfy({ $1 > $0 }),
              zip(stamps, stamps.dropFirst()).allSatisfy({ $1 > $0 }) else { throw ProbeError("Cached frame sequence or timestamp") }
    }
    return rows
}

func captureDesktop(_ name: String, content: SCShareableContent, root: URL) async throws {
    guard let display = content.displays.first else { throw ProbeError("No display") }
    let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
    let config = SCStreamConfiguration()
    config.width = display.width; config.height = display.height; config.showsCursor = false
    let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
    try savePNG(image, root.appendingPathComponent(name))
}

@main
struct Probe {
    static func main() async {
        guard CommandLine.arguments.count == 3 else { fputs("usage: probe target|cover|capture output-dir\n", stderr); exit(2) }
        let role = CommandLine.arguments[1]
        let root = URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true)
        if role == "target" || role == "cover" {
            let app = NSApplication.shared
            app.setActivationPolicy(.accessory)
            let fixture = Fixture(root: root, cover: role == "cover")
            app.delegate = fixture
            app.run()
            return
        }
        let application = NSApplication.shared
        application.setActivationPolicy(.accessory)
        _ = NSScreen.screens
        var result: [String: Any] = ["passed": false, "architecture": "unknown", "permission_preflight": CGPreflightScreenCaptureAccess(),
            "os": ProcessInfo.processInfo.operatingSystemVersionString, "pid": ProcessInfo.processInfo.processIdentifier,
            "parent_pid": getppid(), "runner_image": ProcessInfo.processInfo.environment["ImageVersion"] ?? "unknown"]
        #if arch(arm64)
        result["architecture"] = "arm64"
        #else
        result["architecture"] = "other"
        #endif
        var children = [Process]()
        var active: SCStream?
        do {
            guard result["architecture"] as? String == "arm64" else { throw ProbeError("Expected native arm64") }
            try saveJSON(result, root.appendingPathComponent("permission-preflight.json"))
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
            result["shareable_query"] = true
            let target = try launch("target", root: root); children.append(target)
            let ready = try await waitReady(root.appendingPathComponent("target-ready.json"), process: target)
            try await pause(0.5)
            let updated = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
            guard let window = updated.windows.first(where: { $0.windowID == UInt32(ready["window"] as! Int) && $0.owningApplication?.processID == target.processIdentifier }) else {
                throw ProbeError("Target not found by pid/window id")
            }
            result["target"] = ready
            let filter = SCContentFilter(desktopIndependentWindow: window)
            let config = SCStreamConfiguration()
            config.width = 320; config.height = 180; config.showsCursor = false
            config.pixelFormat = kCVPixelFormatType_32BGRA
            config.minimumFrameInterval = CMTime(value: 1, timescale: 20)
            config.queueDepth = 3
            config.ignoreShadowsSingleWindow = true
            let frames = Frames()
            let stream = SCStream(filter: filter, configuration: config, delegate: frames)
            active = stream
            try stream.addStreamOutput(frames, type: .screen, sampleHandlerQueue: DispatchQueue(label: "probe.frames"))
            try await stream.startCapture()
            var phases = [String: [[String: Any]]]()
            phases["visible"] = try await collect("visible", frames: frames, root: root)
            let cover = try launch("cover", root: root); children.append(cover)
            result["cover"] = try await waitReady(root.appendingPathComponent("cover-ready.json"), process: cover)
            try await pause(0.6)
            result["frontmost_before"] = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1
            result["window_list"] = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
            try await captureDesktop("desktop-occluded.png", content: content, root: root)
            phases["occluded"] = try await collect("occluded", frames: frames, root: root)
            try Data().write(to: root.appendingPathComponent("freeze"))
            try await pause(0.5)
            phases["frozen"] = try await collect("frozen", frames: frames, root: root, frozen: true)
            try Data().write(to: root.appendingPathComponent("resize"))
            try FileManager.default.removeItem(at: root.appendingPathComponent("freeze"))
            config.width = 400; config.height = 240
            try await stream.updateConfiguration(config)
            try await pause(0.8)
            try await captureDesktop("desktop-resized.png", content: content, root: root)
            phases["resized"] = try await collect("resized", frames: frames, root: root)
            result["phases"] = phases
            result["frontmost_after"] = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1
            guard result["frontmost_before"] as? Int32 == result["frontmost_after"] as? Int32 else { throw ProbeError("Frontmost application changed during capture") }
            try await stream.stopCapture(); active = nil
            try stream.removeStreamOutput(frames, type: .screen)
            result["passed"] = true
        } catch {
            result["error"] = String(describing: error)
            if let stream = active { try? await stream.stopCapture() }
        }
        try? Data().write(to: root.appendingPathComponent("stop"))
        for _ in 0..<30 {
            if children.allSatisfy({ !$0.isRunning }) { break }
            try? await pause(0.1)
        }
        let clean = children.allSatisfy { !$0.isRunning }
        for child in children where child.isRunning { child.terminate() }
        result["cleanup"] = clean
        if !clean { result["passed"] = false }
        do { try saveJSON(result, root.appendingPathComponent("result.json")) }
        catch { fputs("Could not write result: \(error)\n", stderr); exit(1) }
        print(String(data: try! JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]), encoding: .utf8)!)
        exit(result["passed"] as? Bool == true ? 0 : 1)
    }
}
