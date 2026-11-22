// 被动单窗口采集组件；标准输出仅传输 IPC，不激活或控制目标应用。
import AppKit
import ScreenCaptureKit
import CoreVideo

struct CaptureFailure: Error {
    let code: String
}

func diagnostic(_ stage: String) {
    guard ProcessInfo.processInfo.environment["GAMEQAKIT_NATIVE_DIAGNOSTICS"] == "1" else { return }
    let text = "capture-helper pid=\(ProcessInfo.processInfo.processIdentifier) t=\(ProcessInfo.processInfo.systemUptime) stage=\(stage)\n"
    try? FileHandle.standardError.write(contentsOf: Data(text.utf8))
}

func emit(_ metadata: [String: Any], png: Data = Data()) throws {
    var row = metadata
    row["version"] = 1
    row["payload_bytes"] = png.count
    let json = try JSONSerialization.data(withJSONObject: row, options: [.sortedKeys])
    guard json.count <= 65536, png.count <= 64 * 1024 * 1024 else {
        throw CaptureFailure(code: "output_limit")
    }
    var length = UInt32(json.count).bigEndian
    let output = FileHandle.standardOutput
    try output.write(contentsOf: withUnsafeBytes(of: &length) { Data($0) })
    try output.write(contentsOf: json)
    if !png.isEmpty { try output.write(contentsOf: png) }
}

func readRequest() throws -> [String: Any]? {
    var data = Data()
    while let next = try FileHandle.standardInput.read(upToCount: 1), !next.isEmpty {
        if next[0] == 10 {
            guard let row = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  (row["version"] as? Int) == 1 else { throw CaptureFailure(code: "invalid_request") }
            return row
        }
        data.append(next)
        guard data.count <= 8192 else { throw CaptureFailure(code: "request_limit") }
    }
    if !data.isEmpty { throw CaptureFailure(code: "truncated_request") }
    return nil
}

func asyncReadRequest() async throws -> [String: Any]? {
    try await withCheckedThrowingContinuation { continuation in
        DispatchQueue.global(qos: .userInitiated).async {
            do { continuation.resume(returning: try readRequest()) }
            catch { continuation.resume(throwing: error) }
        }
    }
}

@available(macOS 14.0, *)
func windowIdentity(_ window: SCWindow) throws -> [String: Any] {
    guard let app = window.owningApplication else { throw CaptureFailure(code: "missing_owner") }
    var seconds: UInt64 = 0
    var microseconds: UInt64 = 0
    let pathCapacity = gameqa_process_path_capacity()
    var path = [CChar](repeating: 0, count: Int(pathCapacity))
    guard gameqa_process_identity(app.processID, &seconds, &microseconds, &path, pathCapacity) == 1 else {
        throw CaptureFailure(code: "process_identity_unavailable")
    }
    let filter = SCContentFilter(desktopIndependentWindow: window)
    let scale = Double(filter.pointPixelScale)
    return ["window_id": Int(window.windowID), "pid": Int(app.processID),
            "started_at": "\(seconds).\(microseconds)",
            "title": window.title ?? "", "name": app.applicationName,
            "exe_path": String(cString: path),
            "size": [Int((filter.contentRect.width * scale).rounded(.up)),
                     Int((filter.contentRect.height * scale).rounded(.up))]]
}

@available(macOS 14.0, *)
func execute(_ request: [String: Any], sequence: inout Int) async throws {
    if request["op"] as? String == "process_identity" {
        guard let pid = request["pid"] as? Int, pid > 0, pid <= Int(Int32.max) else {
            throw CaptureFailure(code: "invalid_pid")
        }
        var seconds: UInt64 = 0
        var microseconds: UInt64 = 0
        let capacity = gameqa_process_path_capacity()
        var path = [CChar](repeating: 0, count: Int(capacity))
        if gameqa_process_identity(pid_t(pid), &seconds, &microseconds, &path, capacity) == 1 {
            try emit(["ok": true, "identity": ["pid": pid, "started_at": "\(seconds).\(microseconds)",
                                              "exe_path": String(cString: path)]])
        } else {
            try emit(["ok": true, "identity": NSNull()])
        }
        return
    }
    guard CGPreflightScreenCaptureAccess() else {
        if request["op"] as? String == "list" { _ = CGRequestScreenCaptureAccess() }
        throw CaptureFailure(code: "permission_denied")
    }
    diagnostic("query.begin")
    let content: SCShareableContent
    do { content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: false) }
    catch {
        if !CGPreflightScreenCaptureAccess() { throw CaptureFailure(code: "permission_denied") }
        throw CaptureFailure(code: "window_query_failed")
    }
    diagnostic("query.end")
    switch request["op"] as? String {
    case "list":
        var windows: [[String: Any]] = []
        var unavailable: [[String: Any]] = []
        for window in content.windows where window.windowLayer == 0 && window.frame.width > 1 && window.frame.height > 1 &&
            window.owningApplication?.processID != ProcessInfo.processInfo.processIdentifier {
            do { windows.append(try windowIdentity(window)) }
            catch let error as CaptureFailure {
                unavailable.append(["window_id": Int(window.windowID), "pid": Int(window.owningApplication?.processID ?? -1),
                                    "reason": error.code])
            }
        }
        try emit(["ok": true, "windows": windows, "unavailable": unavailable])
    case "capture":
        guard let wanted = request["source"] as? [String: Any],
              let id = wanted["window_id"] as? Int,
              let pid = wanted["pid"] as? Int,
              let stamp = wanted["started_at"] as? String,
              let window = content.windows.first(where: { Int($0.windowID) == id && Int($0.owningApplication?.processID ?? -1) == pid }) else {
            throw CaptureFailure(code: "window_closed")
        }
        var identity = try windowIdentity(window)
        guard identity["started_at"] as? String == stamp,
              identity["exe_path"] as? String == wanted["exe_path"] as? String else {
            throw CaptureFailure(code: "source_changed")
        }
        guard let rows = CGWindowListCopyWindowInfo(.optionIncludingWindow, window.windowID) as? [[String: Any]],
              let record = rows.first, (record[kCGWindowOwnerPID as String] as? Int) == pid else {
            throw CaptureFailure(code: "window_closed")
        }
        let filter = SCContentFilter(desktopIndependentWindow: window)
        let scale = Double(filter.pointPixelScale)
        let width = Int((filter.contentRect.width * scale).rounded(.up))
        let height = Int((filter.contentRect.height * scale).rounded(.up))
        guard width > 0, height > 0, width <= 32768, height <= 32768,
              width * height <= 32 * 1024 * 1024 else { throw CaptureFailure(code: "pixel_limit") }
        let config = SCStreamConfiguration()
        config.width = width
        config.height = height
        config.showsCursor = false
        config.ignoreShadowsSingleWindow = true
        config.pixelFormat = kCVPixelFormatType_32BGRA
        let started = ProcessInfo.processInfo.systemUptime
        diagnostic("image.begin")
        let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
        diagnostic("image.end")
        guard CGPreflightScreenCaptureAccess() else { throw CaptureFailure(code: "permission_denied") }
        diagnostic("recheck.begin")
        let refreshed = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: false)
        diagnostic("recheck.end")
        guard let same = refreshed.windows.first(where: { $0.windowID == window.windowID && $0.owningApplication?.processID == pid_t(pid) }) else {
            throw CaptureFailure(code: "source_changed")
        }
        let rechecked = try windowIdentity(same)
        guard rechecked["started_at"] as? String == stamp,
              rechecked["exe_path"] as? String == wanted["exe_path"] as? String else {
            throw CaptureFailure(code: "source_changed")
        }
        guard let png = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]) else {
            throw CaptureFailure(code: "png_encode_failed")
        }
        identity["size"] = [image.width, image.height]
        sequence += 1
        let format = ISO8601DateFormatter()
        format.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        try emit(["ok": true, "source": identity, "sequence": sequence,
                  "captured_at": format.string(from: Date()),
                  "capture_ms": (ProcessInfo.processInfo.systemUptime - started) * 1000], png: png)
    default:
        throw CaptureFailure(code: "unsupported_operation")
    }
}

final class ManagedFocusMonitor: NSObject, NSApplicationDelegate {
    let root: URL
    let pid: Int32
    let identity: String
    var baseline: Int32 = -1
    var frontmost: Int32 = -1
    var changed = false
    var targetActivated = false
    var samples = 0
    var timer: Timer?
    var observer: NSObjectProtocol?
    var events: [[String: Any]] = []
    init(root: URL, pid: Int32, identity: String) {
        self.root = root; self.pid = pid; self.identity = identity
    }
    func save(_ name: String, stopped: Bool = false) throws {
        let row: [String: Any] = ["target_pid": pid, "target_started_at": identity,
            "original_frontmost": baseline, "frontmost": frontmost,
            "target_activated": targetActivated, "frontmost_changed": changed,
            "samples": samples, "updated_at": Date().timeIntervalSince1970,
            "stopped": stopped, "events": events]
        try JSONSerialization.data(withJSONObject: row, options: [.sortedKeys])
            .write(to: root.appendingPathComponent(name), options: .atomic)
    }
    func record(_ value: Int32, source: String) {
        if value != baseline { changed = true }
        if value == pid { targetActivated = true }
        if value != frontmost || source == "activation" {
            events.append(["pid": value, "source": source, "at": Date().timeIntervalSince1970])
        }
        frontmost = value
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        baseline = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1
        frontmost = baseline
        guard baseline > 0, baseline != pid else { exit(1) }
        observer = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main
        ) { [self] notification in
            guard let app = notification.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication else { return }
            record(app.processIdentifier, source: "activation")
            do { try save("focus-state.json") } catch { exit(1) }
        }
        samples = 1
        do { try save("focus-state.json"); try save("focus-ready.json") } catch { exit(1) }
        timer = Timer.scheduledTimer(withTimeInterval: 0.02, repeats: true) { [self] _ in
            record(NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1, source: "poll")
            samples += 1
            do {
                try save("focus-state.json")
                if FileManager.default.fileExists(atPath: root.appendingPathComponent("focus-stop").path) {
                    timer?.invalidate()
                    if let observer { NSWorkspace.shared.notificationCenter.removeObserver(observer) }
                    try save("focus-final.json", stopped: true)
                    NSApp.terminate(nil)
                }
            } catch { exit(1) }
        }
    }
}

@main
struct CaptureHelper {
    static func main() async {
        let application = NSApplication.shared
        application.setActivationPolicy(.accessory)
        if CommandLine.arguments.count == 5, CommandLine.arguments[1] == "--focus-monitor",
           let pid = Int32(CommandLine.arguments[3]), pid > 0 {
            let monitor = ManagedFocusMonitor(root: URL(fileURLWithPath: CommandLine.arguments[2]),
                                              pid: pid, identity: CommandLine.arguments[4])
            application.delegate = monitor
            withExtendedLifetime(monitor) { application.run() }
            return
        }
        _ = NSScreen.screens
        guard #available(macOS 14.0, *) else {
            try? emit(["ok": false, "error": "unsupported_os"])
            return
        }
        var sequence = 0
        do {
            while true {
                let request: [String: Any]?
                if ProcessInfo.processInfo.environment["GAMEQAKIT_NATIVE_ASYNC_STDIN"] == "1" {
                    request = try await asyncReadRequest()
                } else {
                    request = try readRequest()
                }
                guard let request else { break }
                let watchdog = DispatchSource.makeTimerSource(queue: .global())
                watchdog.schedule(deadline: .now() + 4)
                watchdog.setEventHandler { _exit(124) }
                watchdog.resume()
                defer { watchdog.cancel() }
                do { try await execute(request, sequence: &sequence) }
                catch {
                    let code = (error as? CaptureFailure)?.code ?? "capture_failed"
                    try emit(["ok": false, "error": code])
                    return
                }
            }
        } catch {
            try? emit(["ok": false, "error": (error as? CaptureFailure)?.code ?? "protocol_failed"])
        }
    }
}
