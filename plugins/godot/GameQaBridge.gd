extends SceneTree
## 显式启动的 Godot 4 帧协议桥；仅把输入交给引擎，再观察正常场景绘制。

const BUTTONS = {
    "up": KEY_UP, "down": KEY_DOWN, "left": KEY_LEFT, "right": KEY_RIGHT,
    "ok": KEY_ENTER, "cancel": KEY_ESCAPE, "shift": KEY_SHIFT,
    "pageup": KEY_PAGEUP, "pagedown": KEY_PAGEDOWN,
    "x": KEY_X, "c": KEY_C, "tab": KEY_TAB, "f1": KEY_F1, "f9": KEY_F9,
}
const FIELDS = {
    "resume": ["action", "frames"], "move": ["action", "frames", "direction"],
    "confirm": ["action", "frames"], "cancel": ["action", "frames"],
    "wait": ["action", "frames"], "press": ["action", "frames", "button"],
    "release": ["action", "frames", "button"], "click": ["action", "frames", "x", "y"],
}
var directory = OS.get_environment("GAMEQAKIT_BRIDGE_DIR")
var session = OS.get_environment("GAMEQAKIT_SESSION_ID")
var meta: Dictionary = {}
var frame: int = 0
var input_paused = true
var held: Array = []
var mouse_down = false
var mouse_position = Vector2.ZERO
var active: Dictionary = {}
var accepted_pause = ""
var observed_pause = ""
var last_request = ""
var error = ""
var observation_seq: int = 0
var last_observation: int = 0
var last_rendered_frame: int = -1
var last_status: int = 0
var capture_error = ""

func _initialize():
    if directory.is_empty() or session.is_empty() or not directory.is_absolute_path():
        quit(2)
        return
    meta = _read("connection.json")
    if (meta.get("protocol") != 1 or meta.get("session_id") != session
            or meta.get("engine") != "godot" or meta.get("pid") != OS.get_process_id()
            or str(meta.get("exe_path", "")).replace("\\", "/").to_lower() != OS.get_executable_path().to_lower()):
        quit(2)
        return
    var main_loop = str(ProjectSettings.get_setting("application/run/main_loop_type", "SceneTree"))
    if main_loop != "SceneTree":
        quit(2)
        return
    for name in ["results", "screenshots"]:
        if DirAccess.make_dir_recursive_absolute(directory.path_join(name)) != OK:
            quit(2)
            return
    process_frame.connect(_before_frame)
    RenderingServer.frame_post_draw.connect(_after_render)
    call_deferred("_start_game")

func _start_game():
    # 仍从项目声明的入口启动，保留引擎自身加载的 Autoload 与正常玩法逻辑。
    var scene = str(ProjectSettings.get_setting("application/run/main_scene", ""))
    if scene.is_empty() or change_scene_to_file(scene) != OK:
        _fail("main_scene_load_failed")

func _read(name: String, retry_open := false) -> Dictionary:
    var path = directory.path_join(name)
    if not retry_open and not FileAccess.file_exists(path):
        return {}
    var stream = FileAccess.open(path, FileAccess.READ)
    if retry_open:
        # Windows can briefly refuse an open during atomic renewal. Never reuse an old lease.
        for _attempt in range(2):
            if stream != null or FileAccess.get_open_error() != ERR_FILE_CANT_OPEN:
                break
            OS.delay_msec(1)
            stream = FileAccess.open(path, FileAccess.READ)
    if stream == null or stream.get_length() > 1048576:
        return {}
    var parsed = JSON.new()
    if parsed.parse(stream.get_as_text()) != OK or typeof(parsed.data) != TYPE_DICTIONARY:
        return {}
    return parsed.data

func _write(name: String, value: Dictionary) -> bool:
    var target = directory.path_join(name)
    var stream = FileAccess.open(target + ".part", FileAccess.WRITE)
    if stream == null:
        return false
    stream.store_string(JSON.stringify(value))
    var success = stream.get_error() == OK
    stream.close()
    return success and DirAccess.rename_absolute(target + ".part", target) == OK

func _pause_id() -> String:
    var value = _read("pause.json")
    return str(value.get("id", "")) if value.get("session_id") == session else ""

func _lease_valid() -> bool:
    var lease = _read("lease.json", true)
    var expires = lease.get("expires_at")
    return (lease.get("session_id") == session and _number(expires)
        and expires >= Time.get_unix_time_from_system())

func _number(value) -> bool:
    return typeof(value) in [TYPE_INT, TYPE_FLOAT] and is_finite(float(value))

func _integer(value) -> bool:
    return _number(value) and floor(float(value)) == float(value)

func _view() -> Dictionary:
    return {"screen": [int(root.size.x), int(root.size.y)],
        "scene": str(current_scene.scene_file_path) if current_scene != null else "",
        "input_ready": current_scene != null and observation_seq > 0 and capture_error.is_empty()}

func _status(force = false):
    if not force and Time.get_ticks_msec() - last_status < 200:
        return
    var value = {"protocol": 1, "session_id": session, "pid": int(meta.pid),
        "exe_path": meta.exe_path, "engine": "godot", "plugin_pid": OS.get_process_id(),
        "updated_at": Time.get_unix_time_from_system(), "frame": frame, "paused": input_paused,
        "held": held, "command_id": active.get("request", {}).get("id"), "error": error,
        "capabilities": [] if not capture_error.is_empty() or observation_seq == 0 else FIELDS.keys().filter(func(key): return key != "resume"),
        "buttons": BUTTONS.keys(), "max_input_frames": 120, "observation_seq": observation_seq,
        "observation_screenshot": "screenshots/observation.png" if observation_seq > 0 else null,
        "user_data_dir": OS.get_user_data_dir(), "engine_version": Engine.get_version_info().string}
    value.merge(_view())
    value["pause_id"] = observed_pause
    if not capture_error.is_empty():
        value["observation_error"] = capture_error
    if _write("status.json", value):
        last_status = Time.get_ticks_msec()

func _key(button: String, pressed: bool):
    var event = InputEventKey.new()
    event.keycode = BUTTONS[button]
    event.physical_keycode = BUTTONS[button]
    event.pressed = pressed
    Input.parse_input_event(event)

func _release():
    for button in held:
        _key(button, false)
    held = []
    if mouse_down:
        var event = InputEventMouseButton.new()
        event.button_index = MOUSE_BUTTON_LEFT
        event.pressed = false
        event.position = mouse_position
        event.global_position = mouse_position
        Input.parse_input_event(event)
    mouse_down = false
    Input.flush_buffered_events()

func _fail(message: String, request: Dictionary = {}):
    var consumed = active.get("consumed", false)
    if request.is_empty() and not active.is_empty():
        request = active.request
    input_paused = true
    _release()
    active = {}
    error = message
    if not request.is_empty() and _valid_id(request.get("id")):
        _write("results/" + request.id + ".json", {"protocol": 1, "session_id": session,
            "id": request.id, "ok": false, "consumed": consumed,
            "frame": frame, "paused": true, "error": message})
    _status(true)

func _valid_id(value) -> bool:
    if typeof(value) != TYPE_STRING or value.length() < 1 or value.length() > 64:
        return false
    for character in value:
        if not character in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-":
            return false
    return true

func _valid_payload(payload) -> bool:
    if typeof(payload) != TYPE_DICTIONARY or not FIELDS.has(payload.get("action")):
        return false
    if not _integer(payload.get("frames")) or payload.frames < 1 or payload.frames > 120:
        return false
    for key in payload:
        if not key in FIELDS[payload.action]:
            return false
    match payload.action:
        "move": return payload.get("direction") in ["up", "down", "left", "right"]
        "press": return BUTTONS.has(payload.get("button"))
        "release": return not payload.has("button") or BUTTONS.has(payload.button)
        "click":
            return (_integer(payload.get("x")) and _integer(payload.get("y"))
                and payload.x >= 0 and payload.x < root.size.x
                and payload.y >= 0 and payload.y < root.size.y)
    return true

func _poll(request: Dictionary):
    if not active.is_empty() or request.is_empty() or request.get("id") == last_request:
        return
    if (request.get("protocol") != 1 or request.get("session_id") != session
            or not _valid_id(request.get("id"))):
        return
    last_request = request.id
    if FileAccess.file_exists(directory.path_join("results/" + request.id + ".json")):
        return
    if (not _valid_payload(request.get("payload")) or not _integer(request.get("after_frame"))
            or request.after_frame < 0 or request.after_frame > frame):
        _fail("invalid_command", request)
        return
    if not _lease_valid() or request.get("pause_id") != _pause_id():
        _fail("stale_command", request)
        return
    var payload = request.payload
    if not capture_error.is_empty() or observation_seq == 0:
        _fail("capture_unavailable", request)
        return
    if payload.action == "resume":
        accepted_pause = request.pause_id
        input_paused = false
        error = ""
    elif input_paused or request.pause_id != accepted_pause:
        _fail("control_paused", request)
        return
    _release()
    active = {"request": request, "remaining": int(payload.frames), "settle": 2,
        "phase": "input", "consumed": false, "started": frame}
    var button = ""
    match payload.action:
        "move": button = payload.direction
        "confirm": button = "ok"
        "cancel": button = "cancel"
        "press": button = payload.button
        "click":
            mouse_position = Vector2(payload.x, payload.y)
            var motion = InputEventMouseMotion.new()
            motion.position = mouse_position
            motion.global_position = mouse_position
            Input.parse_input_event(motion)
            var event = InputEventMouseButton.new()
            event.button_index = MOUSE_BUTTON_LEFT
            event.pressed = true
            event.position = mouse_position
            event.global_position = mouse_position
            Input.parse_input_event(event)
            mouse_down = true
    if not button.is_empty():
        held = [button]
        _key(button, true)
    Input.flush_buffered_events()

func _before_frame():
    frame += 1
    # 先读请求，再读租约与暂停代次，避免把新恢复请求配到旧租约上。
    var request = _read("request.json")
    var stop = _pause_id()
    if stop != observed_pause:
        observed_pause = stop
        if not input_paused or not active.is_empty():
            _fail("manual_pause")
        input_paused = true
        _release()
    if not input_paused and not _lease_valid():
        _fail("lease_expired")
    if not active.is_empty() and frame > active.started:
        active.consumed = true
        if active.phase == "input":
            active.remaining -= 1
            if active.remaining <= 0:
                _release()
                active.phase = "settle"
        elif active.phase == "settle":
            active.settle -= 1
            if active.settle <= 0:
                active.phase = "capture"
    _poll(request)
    _status()
    call_deferred("_ensure_render")

func _ensure_render():
    if not capture_error.is_empty() or current_scene == null or frame < 3:
        return
    if last_rendered_frame >= frame - 1:
        return
    var observing = Time.get_ticks_msec() - last_observation >= 200
    var completing = not active.is_empty() and active.phase == "capture"
    if observing or completing:
        # 完全遮挡时引擎会跳过自动绘制；在场景处理后离屏绘制，仍由原回调取图与回执。
        RenderingServer.force_draw(false)

func _save_image(image: Image, relative: String) -> bool:
    var target = directory.path_join(relative)
    return image.save_png(target + ".part") == OK and DirAccess.rename_absolute(target + ".part", target) == OK

func _image_usable(image: Image) -> bool:
    var width = image.get_width()
    var height = image.get_height()
    if width <= 2 or height <= 2 or width * height > 16777216:
        return false
    # 与受管入口沿用同一判据；排除一像素外缘，最多约两万点采样。
    var total = (width - 2) * (height - 2)
    var stride = maxi(1, int(total / 20000))
    var count = 0
    var black = 0
    var white = 0
    var data = image.get_data()
    for index in range(0, total, stride):
        var pixel = ((int(index / (width - 2)) + 1) * width + index % (width - 2) + 1) * 3
        var red = data[pixel]
        var green = data[pixel + 1]
        var blue = data[pixel + 2]
        black += int(red == 0 and green == 0 and blue == 0)
        white += int(red == 255 and green == 255 and blue == 255)
        count += 1
    return float(maxi(black, white)) / count < 0.999

func _fail_capture(message: String):
    capture_error = message
    _fail(message)

func _after_render():
    last_rendered_frame = frame
    if not capture_error.is_empty() or current_scene == null or frame < 3:
        return
    var observing = Time.get_ticks_msec() - last_observation >= 200
    var completing = not active.is_empty() and active.phase == "capture"
    if not observing and not completing:
        return
    if not active.is_empty():
        # 本帧场景已处理完输入；即使随后取图失败，也不能声称动作未消费。
        active.consumed = true
    var image = root.get_texture().get_image()
    if image == null or image.is_empty():
        _fail_capture("capture_failed")
        return
    image.convert(Image.FORMAT_RGB8)
    if not _image_usable(image):
        _fail_capture("blank_capture")
        return
    if observing:
        if observation_seq == 0 and not _save_image(image, "screenshots/startup.png"):
            _fail_capture("capture_write_failed")
            return
        if not _save_image(image, "screenshots/observation.png"):
            _fail_capture("capture_write_failed")
            return
        observation_seq += 1
        last_observation = Time.get_ticks_msec()
    if completing:
        var request = active.request
        var relative = "screenshots/" + request.id + ".png"
        if not _save_image(image, relative):
            _fail_capture("capture_write_failed")
            return
        var result = {"protocol": 1, "session_id": session, "id": request.id,
            "ok": true, "consumed": true, "frame": frame, "capture_frame": frame,
            "screenshot": relative, "paused": input_paused}
        result.merge(_view())
        if not _write("results/" + request.id + ".json", result):
            _fail("result_write_failed")
            return
        active = {}
    _status(true)

func _finalize():
    _release()
