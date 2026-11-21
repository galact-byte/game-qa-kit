// 被动单窗口采集组件；标准输出仅传输 IPC，不激活或控制目标应用。
import AppKit
import ScreenCaptureKit
import CoreVideo

struct CaptureFailure: Error {
    let code: String
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

@available(macOS 14.0, *)
func windowIdentity(_ window: SCWindow) throws -> [String: Any] {
    guard let app = window.owningApplication else { throw CaptureFailure(code: "missing_owner") }
    var seconds: UInt64 = 0
    var microseconds: UInt64 = 0
    let pathCapacity = UInt32(PROC_PIDPATHINFO_MAXSIZE)
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
    guard CGPreflightScreenCaptureAccess() else {
        if request["op"] as? String == "list" { _ = CGRequestScreenCaptureAccess() }
        throw CaptureFailure(code: "permission_denied")
    }
    let content: SCShareableContent
    do { content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: false) }
    catch {
        if !CGPreflightScreenCaptureAccess() { throw CaptureFailure(code: "permission_denied") }
        throw CaptureFailure(code: "window_query_failed")
    }
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
        guard identity["started_at"] as? String == stamp else { throw CaptureFailure(code: "source_changed") }
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
        let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
        guard CGPreflightScreenCaptureAccess() else { throw CaptureFailure(code: "permission_denied") }
        let refreshed = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: false)
        guard let same = refreshed.windows.first(where: { $0.windowID == window.windowID && $0.owningApplication?.processID == pid_t(pid) }),
              try windowIdentity(same)["started_at"] as? String == stamp else { throw CaptureFailure(code: "source_changed") }
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

@main
struct CaptureHelper {
    static func main() async {
        let application = NSApplication.shared
        application.setActivationPolicy(.accessory)
        _ = NSScreen.screens
        guard #available(macOS 14.0, *) else {
            try? emit(["ok": false, "error": "unsupported_os"])
            return
        }
        var sequence = 0
        do {
            while let request = try readRequest() {
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
