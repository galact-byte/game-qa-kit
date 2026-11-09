/* 本地、显式启用的 MV/MZ 控制桥。未提供专用环境变量时不修改游戏行为。 */
(function () {
    'use strict';
    if (typeof require !== 'function' || typeof process === 'undefined' ||
        !process.env.GAMEQAKIT_BRIDGE_DIR || !process.env.GAMEQAKIT_SESSION_ID ||
        window.__gameQaBridgeInstalled) return;
    const fs = require('fs');
    const path = require('path');
    const root = path.resolve(process.env.GAMEQAKIT_BRIDGE_DIR);
    const read = name => {
        try { return JSON.parse(fs.readFileSync(path.join(root, name), 'utf8')); }
        catch (error) { if (error.code === 'ENOENT') return null; throw error; }
    };
    const meta = read('connection.json');
    const engine = Utils.RPGMAKER_NAME === 'MV' ? 'rpgmaker_mv' :
        Utils.RPGMAKER_NAME === 'MZ' ? 'rpgmaker_mz' : null;
    if (!meta || meta.protocol !== 1 || meta.session_id !== process.env.GAMEQAKIT_SESSION_ID ||
        meta.engine !== engine || !engine ||
        path.resolve(meta.exe_path).toLowerCase() !== path.resolve(process.execPath).toLowerCase()) return;
    window.__gameQaBridgeInstalled = true;
    fs.mkdirSync(path.join(root, 'results'), { recursive: true });
    fs.mkdirSync(path.join(root, 'screenshots'), { recursive: true });
    const buttons = ['up', 'down', 'left', 'right', 'ok', 'cancel', 'shift', 'pageup', 'pagedown'];
    let paused = true;
    let frame = 0;
    let current = null;
    let held = {};
    let clickHeld = false;
    let acceptedPause = '';
    let observedPause = '';
    let lastRequest = '';
    let rendererHooked = false;
    let lastStatus = 0;
    let lastError = null;

    function write(name, value, binary) {
        const target = path.join(root, name);
        const temporary = target + '.part';
        fs.writeFileSync(temporary, binary ? value : JSON.stringify(value), binary ? undefined : 'utf8');
        try { fs.renameSync(temporary, target); return true; }
        catch (error) {
            if (['EPERM', 'EACCES', 'EBUSY'].indexOf(error.code) >= 0) return false;
            throw error;
        }
    }
    function release(hard) {
        held = {};
        for (const key of Object.keys(Input._currentState || {})) Input._currentState[key] = false;
        if (clickHeld && typeof TouchInput._onRelease === 'function') TouchInput._onRelease(TouchInput.x, TouchInput.y);
        clickHeld = false;
        if (hard) { Input.clear(); TouchInput.clear(); }
    }
    function viewState() {
        const scene = SceneManager._scene;
        const windows = scene && scene._windowLayer ? scene._windowLayer.children.filter(w =>
            w.visible !== false && w.active && typeof w.index === 'function') : [];
        const started = scene && (engine === 'rpgmaker_mz' ? scene.isStarted() : SceneManager.isCurrentSceneStarted());
        const changing = typeof SceneManager.isSceneChanging === 'function' && SceneManager.isSceneChanging();
        const busy = !!started && !changing && typeof scene.isBusy === 'function' && scene.isBusy();
        const moving = scene && typeof scene.isMenuCalled === 'function' &&
            typeof $gamePlayer !== 'undefined' && $gamePlayer && $gamePlayer.isMoving();
        const ready = !!started && !changing && !busy && !moving && (windows.length === 0 || windows.some(w =>
            typeof w.isOpenAndActive === 'function' ? w.isOpenAndActive() : typeof w.isOpen === 'function' ? w.isOpen() : true));
        const player = typeof $gamePlayer !== 'undefined' && $gamePlayer ? {
            x: $gamePlayer.x, y: $gamePlayer.y, direction: $gamePlayer.direction(),
            map_id: typeof $gameMap !== 'undefined' && $gameMap ? $gameMap.mapId() : null
        } : null;
        return { scene: scene ? scene.constructor.name : null, input_ready: ready, player,
            selection: windows.map(w => ({ type: w.constructor.name,
                index: typeof w.index === 'function' ? w.index() : null,
                symbol: typeof w.currentSymbol === 'function' ? w.currentSymbol() : null })) };
    }
    function status(force) {
        if (!force && Date.now() - lastStatus < 200) return;
        if (write('status.json', {
            protocol: 1, session_id: meta.session_id, pid: meta.pid, exe_path: meta.exe_path,
            engine, plugin_pid: process.pid, updated_at: Date.now() / 1000, frame, paused,
            capabilities: rendererHooked ? ['move', 'confirm', 'cancel', 'wait', 'press', 'release', 'click'] : [],
            buttons: buttons.slice(),
            screen: [Graphics.width || 0, Graphics.height || 0],
            ...viewState(),
            held: Object.keys(held), command_id: current ? current.request.id : null, error: lastError
        })) lastStatus = Date.now();
    }
    function fail(error, request) {
        const active = current;
        request = request || (active && active.request);
        paused = true;
        release(true);
        current = null;
        lastError = String(error.message || error);
        if (request && /^[A-Za-z0-9_-]{1,64}$/.test(request.id)) {
            write('results/' + request.id + '.json', {
                protocol: 1, session_id: meta.session_id, id: request.id, ok: false,
                consumed: !!(active && active.consumed), frame, paused: true, error: lastError
            });
        }
        status(true);
    }
    function leaseValid() {
        const lease = read('lease.json');
        return lease && lease.session_id === meta.session_id && Number.isFinite(lease.expires_at) &&
            lease.expires_at >= Date.now() / 1000;
    }
    function pauseId() {
        const value = read('pause.json');
        return value && value.session_id === meta.session_id ? value.id : '';
    }
    function guard() {
        const stop = pauseId();
        if (stop !== observedPause) {
            observedPause = stop;
            if (!paused || current) fail('manual_pause');
            paused = true;
            release(true);
        }
        if (!paused && !leaseValid()) fail('lease_expired');
    }
    function validPayload(payload) {
        if (!payload || !Number.isInteger(payload.frames) || payload.frames < 1 || payload.frames > 120) return false;
        const fields = {
            resume: ['action', 'frames'], move: ['action', 'frames', 'direction'],
            confirm: ['action', 'frames'], cancel: ['action', 'frames'], wait: ['action', 'frames'],
            press: ['action', 'frames', 'button'], release: ['action', 'frames', 'button'],
            click: ['action', 'frames', 'x', 'y']
        }[payload.action];
        if (!fields || Object.keys(payload).some(key => fields.indexOf(key) < 0)) return false;
        if (payload.action === 'move' && ['up', 'down', 'left', 'right'].indexOf(payload.direction) < 0) return false;
        if (payload.action === 'press' && buttons.indexOf(payload.button) < 0) return false;
        if (payload.action === 'release' && payload.button !== undefined && buttons.indexOf(payload.button) < 0) return false;
        if (payload.action === 'click' && (!Number.isInteger(payload.x) || !Number.isInteger(payload.y) ||
            payload.x < 0 || payload.y < 0 || payload.x >= Graphics.width || payload.y >= Graphics.height)) return false;
        return true;
    }
    function poll() {
        if (current) return;
        const request = read('request.json');
        if (!request || request.id === lastRequest) return;
        if (request.protocol !== 1 || request.session_id !== meta.session_id ||
            typeof request.id !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(request.id)) return;
        if (fs.existsSync(path.join(root, 'results', request.id + '.json'))) { lastRequest = request.id; return; }
        if (!validPayload(request.payload) || !Number.isInteger(request.after_frame) || request.after_frame < 0 || request.after_frame > frame) {
            lastRequest = request.id; fail('invalid_command', request); return;
        }
        if (request.pause_id !== pauseId() || !leaseValid()) { lastRequest = request.id; fail('stale_command', request); return; }
        const payload = request.payload;
        if (['resume', 'wait', 'release'].indexOf(payload.action) < 0 && !viewState().input_ready) return;
        lastRequest = request.id;
        if (payload.action === 'resume') {
            acceptedPause = request.pause_id;
            paused = false;
            lastError = null;
            release(true);
        } else if (paused || request.pause_id !== acceptedPause) { fail('control_paused', request); return; }
        current = { request, remaining: payload.frames, settle: 2, phase: 'input', consumed: false };
        const button = payload.action === 'move' ? payload.direction : payload.action === 'confirm' ? 'ok' :
            payload.action === 'cancel' ? 'cancel' : payload.action === 'press' ? payload.button : null;
        const nativeButton = button === 'cancel' ? 'escape' : button;
        held = nativeButton ? { [nativeButton]: true } : {};
        if (payload.action === 'click') { TouchInput._onTrigger(payload.x, payload.y); clickHeld = true; }
    }
    function afterLogic() {
        frame++;
        if (!current) return;
        current.consumed = true;
        if (current.phase === 'input' && --current.remaining <= 0) {
            release(false);
            current.phase = 'settle';
        } else if (current.phase === 'settle' && --current.settle <= 0 && viewState().input_ready) {
            current.phase = 'capture';
        }
    }
    function afterRender() {
        if (!current || current.phase !== 'capture') return;
        const request = current.request;
        try {
            const data = Graphics._canvas.toDataURL('image/png');
            if (!data.startsWith('data:image/png;base64,')) throw new Error('invalid_canvas_capture');
            const relative = 'screenshots/' + request.id + '.png';
            if (!write(relative, Buffer.from(data.slice(22), 'base64'), true)) return;
            if (!write('results/' + request.id + '.json', {
                protocol: 1, session_id: meta.session_id, id: request.id, ok: true, consumed: true,
                frame, capture_frame: frame, screenshot: relative, paused,
                ...viewState()
            })) return;
            current = null;
            status(true);
        } catch (error) { fail(error); }
    }
    function hookRenderer() {
        if (rendererHooked) return;
        const renderer = Graphics._renderer || (Graphics._app && Graphics._app.renderer);
        if (!renderer || typeof renderer.render !== 'function') return;
        const original = renderer.render;
        renderer.render = function () {
            const result = original.apply(this, arguments);
            if (arguments[0] === SceneManager._scene) afterRender();
            return result;
        };
        rendererHooked = true;
    }
    const nativeUpdate = Input.update;
    Input.update = function () {
        if (!paused) {
            for (const key of Object.keys(this._previousState || {}).concat(Object.keys(this._currentState))) this._currentState[key] = false;
            Object.assign(this._currentState, held);
        }
        return nativeUpdate.apply(this, arguments);
    };
    const nativeGamepads = Input._pollGamepads;
    if (typeof nativeGamepads === 'function') Input._pollGamepads = function () {
        if (paused) return nativeGamepads.apply(this, arguments);
    };
    for (const input of [Input, TouchInput]) {
        const nativeBlur = input._onLostFocus;
        if (typeof nativeBlur === 'function') input._onLostFocus = function () {
            if (paused) return nativeBlur.apply(this, arguments);
        };
    }
    for (const name of ['keydown', 'keyup', 'keypress', 'mousedown', 'mouseup', 'mousemove', 'wheel',
                        'touchstart', 'touchmove', 'touchend', 'contextmenu']) {
        window.addEventListener(name, function (event) {
            if (!paused) { event.preventDefault(); event.stopImmediatePropagation(); }
        }, true);
    }
    const nativeActive = SceneManager.isGameActive;
    if (typeof nativeActive === 'function') SceneManager.isGameActive = function () {
        return !paused || nativeActive.apply(this, arguments);
    };
    const nativeInput = SceneManager.updateInputData;
    SceneManager.updateInputData = function () {
        try { hookRenderer(); guard(); poll(); }
        catch (error) { fail(error); }
        return nativeInput.apply(this, arguments);
    };
    const nativeScene = SceneManager.updateScene;
    SceneManager.updateScene = function () {
        const scene = this._scene;
        const started = scene && (engine === 'rpgmaker_mz' ? scene.isStarted() : this.isCurrentSceneStarted());
        const active = !this.isGameActive || this.isGameActive();
        const result = nativeScene.apply(this, arguments);
        if (started && active && this._scene === scene) afterLogic();
        return result;
    };
    hookRenderer();
    status(true);
    setInterval(function () {
        try { guard(); status(false); }
        catch (error) { try { fail(error); } catch (_) { paused = true; release(true); } }
    }, 100);
})();
