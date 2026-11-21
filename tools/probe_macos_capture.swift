// 独立 ScreenCaptureKit 探针；只在临时云端桌面运行自写窗口。
import AppKit
import ScreenCaptureKit
import CoreMedia
import CoreVideo
import CoreImage
import QuartzCore
import MetalKit

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

protocol CounterRendering: AnyObject {
    var counter: Int { get set }
    var draws: Int { get }
    var rendererInfo: [String: Any] { get }
    func render()
}

final class CounterView: NSView, CounterRendering {
    var counter = 1
    var draws = 0
    var rendererInfo: [String: Any] { ["renderer": "appkit"] }
    func render() { needsDisplay = true; display(); CATransaction.flush() }
    let cover: Bool
    init(cover: Bool) { self.cover = cover; super.init(frame: .zero) }
    required init?(coder: NSCoder) { fatalError("init(coder:) unsupported") }
    override func draw(_ dirtyRect: NSRect) {
        draws += 1
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

final class MetalCounterView: MTKView, MTKViewDelegate, CounterRendering {
    var counter = 1
    var draws = 0
    let commandQueue: MTLCommandQueue
    let pipeline: MTLRenderPipelineState
    let gpuLock = NSLock()
    var completedCommands = 0
    var gpuFailure = ""
    var rendererInfo: [String: Any] {
        gpuLock.lock(); defer { gpuLock.unlock() }
        return ["renderer": "metal", "gpu": device?.name ?? "unknown",
                "completed_commands": completedCommands, "gpu_error": gpuFailure]
    }

    init(probeGPU gpu: MTLDevice) throws {
        guard let queue = gpu.makeCommandQueue() else {
            throw ProbeError("Metal command queue unavailable")
        }
        let source = """
        #include <metal_stdlib>
        using namespace metal;
        vertex float4 vertex_main(uint id [[vertex_id]]) {
            const float2 vertices[] = {float2(-1,-1),float2(3,-1),float2(-1,3)};
            return float4(vertices[id],0,1);
        }
        fragment float4 fragment_main(float4 pixel [[position]],
                constant uint &counter [[buffer(0)]], constant float2 &size [[buffer(1)]]) {
            if (pixel.y > size.y/3 && pixel.y < size.y*2/3) {
                uint bit = min(uint(pixel.x/size.x*16), 15u);
                float level = (counter & (1u << bit)) ? 240.0/255.0 : 20.0/255.0;
                return float4(level,level,level,1);
            }
            return float4(20.0/255.0,50.0/255.0,80.0/255.0,1);
        }
        """
        let library = try gpu.makeLibrary(source: source, options: nil)
        let descriptor = MTLRenderPipelineDescriptor()
        descriptor.vertexFunction = library.makeFunction(name: "vertex_main")
        descriptor.fragmentFunction = library.makeFunction(name: "fragment_main")
        descriptor.colorAttachments[0].pixelFormat = .bgra8Unorm
        pipeline = try gpu.makeRenderPipelineState(descriptor: descriptor)
        commandQueue = queue
        super.init(frame: .zero, device: gpu)
        colorPixelFormat = .bgra8Unorm
        isPaused = true
        enableSetNeedsDisplay = false
        delegate = self
    }
    required init(coder: NSCoder) { fatalError("init(coder:) unsupported") }
    func render() { draw() }
    func mtkView(_ view: MTKView, drawableSizeWillChange size: CGSize) {}
    func draw(in view: MTKView) {
        guard let pass = currentRenderPassDescriptor, let drawable = currentDrawable,
              let command = commandQueue.makeCommandBuffer(),
              let encoder = command.makeRenderCommandEncoder(descriptor: pass) else { return }
        var value = UInt32(counter)
        var size = SIMD2<Float>(Float(drawableSize.width), Float(drawableSize.height))
        encoder.setRenderPipelineState(pipeline)
        encoder.setFragmentBytes(&value, length: MemoryLayout<UInt32>.size, index: 0)
        encoder.setFragmentBytes(&size, length: MemoryLayout<SIMD2<Float>>.size, index: 1)
        encoder.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 3)
        encoder.endEncoding()
        command.present(drawable)
        command.addCompletedHandler { [weak self] completed in
            guard let self = self else { return }
            self.gpuLock.lock(); defer { self.gpuLock.unlock() }
            if completed.status == .completed { self.completedCommands += 1 }
            if let error = completed.error { self.gpuFailure = error.localizedDescription }
        }
        command.commit()
        draws += 1
    }
}

final class Fixture: NSObject, NSApplicationDelegate {
    let root: URL
    let cover: Bool
    var window: NSWindow!
    var timer: Timer?
    init(root: URL, cover: Bool) { self.root = root; self.cover = cover }
    func applicationDidFinishLaunching(_ notification: Notification) {
        let view: NSView
        let renderer: CounterRendering
        do {
            if !cover && ProcessInfo.processInfo.environment["GAMEQA_PROBE_RENDERER"] == "metal" {
                guard let gpu = MTLCreateSystemDefaultDevice() else { throw ProbeError("Metal device unavailable") }
                let metal = try MetalCounterView(probeGPU: gpu)
                view = metal; renderer = metal
            } else {
                let appkit = CounterView(cover: cover)
                appkit.wantsLayer = true
                view = appkit; renderer = appkit
            }
        } catch {
            try? saveJSON(["error": String(describing: error)], root.appendingPathComponent("target-render-error.json"))
            NSApp.terminate(nil); return
        }
        let rect = cover ? NSRect(x: 90, y: 100, width: 600, height: 400) : NSRect(x: 100, y: 200, width: 320, height: 180)
        window = NSWindow(contentRect: rect, styleMask: .borderless, backing: .buffered, defer: false)
        window.title = cover ? "GameQA cover" : "GameQA target"
        window.isReleasedWhenClosed = false
        window.backgroundColor = .black
        window.isOpaque = true
        window.hasShadow = false
        window.contentView = view
        if cover { window.level = .floating }
        window.orderFrontRegardless()
        renderer.render()
        try? saveJSON(["pid": ProcessInfo.processInfo.processIdentifier, "window": window.windowNumber,
                       "rendering": renderer.rendererInfo],
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
                    renderer.counter += 1
                    renderer.render()
                }
                try? saveJSON(["counter": renderer.counter, "draws": renderer.draws,
                               "rendering": renderer.rendererInfo,
                               "occlusion_visible": window.occlusionState.contains(.visible),
                               "width": window.frame.width, "height": window.frame.height],
                              root.appendingPathComponent("target-state.json"))
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
    var statuses = [String: Int]()
    var snapshotLatencies = [Double]()
    func acceptSnapshot(_ snapshot: CGImage, elapsed: Double) {
        lock.lock(); defer { lock.unlock() }
        serial += 1; image = snapshot; timestamp = ProcessInfo.processInfo.systemUptime
        snapshotLatencies.append(elapsed * 1000)
    }
    func diagnostics() -> [String: Any] {
        lock.lock(); defer { lock.unlock() }
        return ["complete_frames": serial, "statuses": statuses, "failure": failure ?? "",
                "snapshot_latencies_ms": snapshotLatencies]
    }
    func stream(_ stream: SCStream, didStopWithError error: Error) {
        lock.lock(); failure = String(describing: error); lock.unlock()
    }
    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen, sampleBuffer.isValid,
              let array = CMSampleBufferGetSampleAttachmentsArray(sampleBuffer, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
              let rawStatus = array.first?[.status] as? Int else { return }
        lock.lock(); statuses[String(rawStatus), default: 0] += 1; lock.unlock()
        guard SCFrameStatus(rawValue: rawStatus) == .complete,
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

func collect(_ name: String, frames: Frames, root: URL, frozen: Bool = false,
             sample: (() async throws -> Void)? = nil) async throws -> [[String: Any]] {
    var rows = [[String: Any]]()
    for index in 0..<4 {
        try await pause(0.6)
        if let sample = sample { try await sample() }
        let (serial, image, timestamp) = try frames.snapshot()
        let counter = try decode(image)
        let file = "\(name)-\(index).png"
        try savePNG(image, root.appendingPathComponent(file))
        let stateData = try Data(contentsOf: root.appendingPathComponent("target-state.json"))
        let state = try JSONSerialization.jsonObject(with: stateData)
        rows.append(["file": file, "counter": counter, "serial": serial, "pts": timestamp,
                     "width": image.width, "height": image.height, "target_state": state])
        try saveJSON(["rows": rows], root.appendingPathComponent("\(name)-samples.json"))
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

@discardableResult
func captureDesktop(_ name: String, content: SCShareableContent, root: URL,
                    target: CGWindowID? = nil) async throws -> [String: Int] {
    guard let display = content.displays.first else { throw ProbeError("No display") }
    let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
    let config = SCStreamConfiguration()
    config.width = display.width; config.height = display.height; config.showsCursor = false
    let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
    try savePNG(image, root.appendingPathComponent(name))
    guard let target = target else { return [:] }
    guard let rows = CGWindowListCopyWindowInfo(.optionIncludingWindow, target) as? [[String: Any]],
          let bounds = rows.first?[kCGWindowBounds as String] as? [String: Any],
          let rect = CGRect(dictionaryRepresentation: bounds as CFDictionary),
          let crop = image.cropping(to: rect),
          crop.width == Int(rect.width), crop.height == Int(rect.height) else {
        throw ProbeError("Cannot inspect target desktop region")
    }
    let pixels = NSBitmapImageRep(cgImage: crop)
    var mismatches = 0
    for row in 0..<crop.height {
        for column in 0..<crop.width {
            guard let color = pixels.colorAt(x: column, y: row)?.usingColorSpace(.sRGB) else {
                throw ProbeError("Cannot decode desktop pixel")
            }
            if abs(color.redComponent * 255 - 200) > 8 ||
               abs(color.greenComponent * 255 - 30) > 8 ||
               abs(color.blueComponent * 255 - 180) > 8 { mismatches += 1 }
        }
    }
    return ["checked_pixels": crop.width * crop.height, "mismatched_pixels": mismatches]
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
        let mode = ProcessInfo.processInfo.environment["GAMEQA_CAPTURE_MODE"] ?? "stream"
        var result: [String: Any] = ["passed": false, "capture_mode": mode, "architecture": "unknown", "permission_preflight": CGPreflightScreenCaptureAccess(),
            "os": ProcessInfo.processInfo.operatingSystemVersionString, "pid": ProcessInfo.processInfo.processIdentifier,
            "parent_pid": getppid(), "runner_image": ProcessInfo.processInfo.environment["ImageVersion"] ?? "unknown"]
        #if arch(arm64)
        result["architecture"] = "arm64"
        #else
        result["architecture"] = "other"
        #endif
        var children = [Process]()
        var active: SCStream?
        var diagnosticFilter: SCContentFilter?
        let frames = Frames()
        do {
            guard result["architecture"] as? String == "arm64" else { throw ProbeError("Expected native arm64") }
            guard mode == "stream" || mode == "snapshot" else { throw ProbeError("Unknown capture mode") }
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
            diagnosticFilter = filter
            let config = SCStreamConfiguration()
            config.width = 320; config.height = 180; config.showsCursor = false
            config.pixelFormat = kCVPixelFormatType_32BGRA
            config.minimumFrameInterval = CMTime(value: 1, timescale: 20)
            config.queueDepth = 3
            config.ignoreShadowsSingleWindow = true
            let sample: (() async throws -> Void)?
            if mode == "snapshot" {
                sample = {
                    let started = ProcessInfo.processInfo.systemUptime
                    let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
                    frames.acceptSnapshot(image, elapsed: ProcessInfo.processInfo.systemUptime - started)
                }
                try await sample?()
            } else {
                sample = nil
                let stream = SCStream(filter: filter, configuration: config, delegate: frames)
                active = stream
                try stream.addStreamOutput(frames, type: .screen, sampleHandlerQueue: DispatchQueue(label: "probe.frames"))
                try await stream.startCapture()
                for attempt in 0..<30 {
                    do { _ = try frames.snapshot(); break }
                    catch {
                        if attempt == 29 { throw error }
                        try await pause(0.1)
                    }
                }
            }
            var phases = [String: [[String: Any]]]()
            phases["visible"] = try await collect("visible", frames: frames, root: root, sample: sample)
            let cover = try launch("cover", root: root); children.append(cover)
            result["cover"] = try await waitReady(root.appendingPathComponent("cover-ready.json"), process: cover)
            try await pause(0.6)
            result["frontmost_before"] = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1
            result["window_list"] = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
            phases["occluded"] = try await collect("occluded", frames: frames, root: root, sample: sample)
            let occludedCoverage = try await captureDesktop("desktop-occluded.png", content: content, root: root, target: window.windowID)
            try Data().write(to: root.appendingPathComponent("freeze"))
            try await pause(0.5)
            phases["frozen"] = try await collect("frozen", frames: frames, root: root, frozen: true, sample: sample)
            try Data().write(to: root.appendingPathComponent("resize"))
            try FileManager.default.removeItem(at: root.appendingPathComponent("freeze"))
            config.width = 400; config.height = 240
            if let stream = active { try await stream.updateConfiguration(config) }
            try await pause(0.8)
            result["resized_window_list"] = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
            phases["resized"] = try await collect("resized", frames: frames, root: root, sample: sample)
            let resizedCoverage = try await captureDesktop("desktop-resized.png", content: content, root: root, target: window.windowID)
            result["desktop_coverage"] = ["occluded": occludedCoverage, "resized": resizedCoverage]
            result["phases"] = phases
            result["frontmost_after"] = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1
            guard result["frontmost_before"] as? Int32 == result["frontmost_after"] as? Int32 else { throw ProbeError("Frontmost application changed during capture") }
            if let stream = active {
                try await stream.stopCapture(); active = nil
                try stream.removeStreamOutput(frames, type: .screen)
            }
            result["passed"] = true
        } catch {
            result["error"] = String(describing: error)
            if let stream = active { try? await stream.stopCapture() }
            if let filter = diagnosticFilter {
                do {
                    let config = SCStreamConfiguration()
                    config.width = 320; config.height = 180; config.showsCursor = false
                    config.ignoreShadowsSingleWindow = true
                    var pulled = [[String: Any]]()
                    for index in 0..<4 {
                        try await pause(0.6)
                        let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
                        let file = "pull-after-stop-\(index).png"
                        try savePNG(image, root.appendingPathComponent(file))
                        let state = try JSONSerialization.jsonObject(with: Data(contentsOf: root.appendingPathComponent("target-state.json")))
                        pulled.append(["file": file, "counter": try decode(image), "target_state": state])
                    }
                    result["pull_after_stop_growth"] = growth(pulled)
                    try saveJSON(["rows": pulled], root.appendingPathComponent("pull-after-stop.json"))
                } catch { result["pull_after_stop_error"] = String(describing: error) }
            }
        }
        result["frontmost_at_stream_end"] = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1
        if !FileManager.default.fileExists(atPath: root.appendingPathComponent("desktop-occluded.png").path),
           let content = try? await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false) {
            do { try await captureDesktop("desktop-occluded.png", content: content, root: root) }
            catch { result["desktop_evidence_error"] = String(describing: error) }
        }
        result["frame_diagnostics"] = frames.diagnostics()
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
