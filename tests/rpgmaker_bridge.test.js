const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');

const pluginPath = path.resolve(__dirname, '../plugins/rpgmaker_js/GameQaBridge.js');

function fixture(t, version = 'MZ', enabled = true) {
    assert.ok(fs.existsSync(pluginPath), '缺少 RPG Maker 家族控制插件');
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'gameqa-js-'));
    t.after(() => fs.rmSync(root, { recursive: true, force: true }));
    let now = Date.now();
    let timer;
    let sceneBusy = false;
    let moving = false;
    const stats = { right: 0, left: 0, clicks: 0, nativePolls: 0, escape: 0, okEdges: 0 };
    const input = {
        _currentState: {}, _previousState: {}, _latestButton: null, _pressedTime: 0,
        clear() { this._currentState = {}; this._previousState = {}; this._latestButton = null; },
        _pollGamepads() { stats.nativePolls++; },
        update() {
            this._pollGamepads();
            if (!this._currentState[this._latestButton]) this._latestButton = null;
            else this._pressedTime++;
            for (const [button, value] of Object.entries(this._currentState)) {
                if (value && !this._previousState[button]) { this._latestButton = button; this._pressedTime = 0; }
                this._previousState[button] = value;
            }
        },
        _onLostFocus() { this.clear(); },
        isPressed(key) { return !!this._currentState[key]; }
    };
    const touch = {
        _events: {}, clear() { this._events = {}; }, update() {},
        _onTrigger(x, y) { this._events.triggered = true; this.x = x; this.y = y; },
        _onRelease() { this._events.released = true; }
    };
    class Scene_Map {
        isStarted() { return true; }
        isBusy() { return sceneBusy; }
        isMenuCalled() { return false; }
        update() {
            if (input.isPressed('right')) stats.right++;
            if (input.isPressed('left')) stats.left++;
            if (input.isPressed('escape')) stats.escape++;
            if (input._latestButton === 'ok' && input._pressedTime === 0) stats.okEdges++;
            if (touch._events.triggered) { stats.clicks++; touch._events.triggered = false; }
        }
    }
    const scene = new Scene_Map();
    const renderer = { render() {} };
    const graphics = { width: 320, height: 240,
        _canvas: { toDataURL() { return 'data:image/png;base64,' + Buffer.from('unit-render').toString('base64'); } } };
    if (version === 'MV') graphics._renderer = renderer;
    else graphics._app = { renderer, stage: scene };
    const manager = {
        _scene: scene, isCurrentSceneStarted() { return true; },
        updateInputData() { input.update(); touch.update(); },
        updateScene() { if (!this.isGameActive || this.isGameActive()) this._scene.update(); }
    };
    if (version === 'MZ') manager.isGameActive = () => false;
    const meta = { protocol: 1, session_id: 'unit-session', pid: process.pid,
        exe_path: process.execPath, engine: version === 'MV' ? 'rpgmaker_mv' : 'rpgmaker_mz' };
    fs.writeFileSync(path.join(root, 'connection.json'), JSON.stringify(meta));
    const originalUpdate = input.update;
    const context = {
        require, Buffer, console, Input: input, TouchInput: touch, Graphics: graphics, SceneManager: manager,
        $gamePlayer: { x: 0, y: 0, direction() { return 6; }, isMoving() { return moving; } },
        $gameMap: { mapId() { return 1; } },
        Utils: { RPGMAKER_NAME: version },
        process: { env: enabled ? { GAMEQAKIT_BRIDGE_DIR: root, GAMEQAKIT_SESSION_ID: 'unit-session' } : {}, execPath: process.execPath, pid: process.pid },
        Date: class extends Date { static now() { return now; } },
        setInterval(callback) { timer = callback; return 1; }, clearInterval() {},
        window: { addEventListener() {}, top: { document: { hasFocus() { return false; } } } }
    };
    vm.runInNewContext(fs.readFileSync(pluginPath, 'utf8'), context, { filename: pluginPath });
    function step(count = 1) {
        for (let i = 0; i < count; i++) {
            manager.updateInputData(); manager.updateScene(); renderer.render(scene);
            now += 16;
            if (timer) timer();
        }
    }
    function send(id, action, options = {}) {
        const state = JSON.parse(fs.readFileSync(path.join(root, 'status.json'), 'utf8'));
        const pausePath = path.join(root, 'pause.json');
        const pauseValue = fs.existsSync(pausePath) ? JSON.parse(fs.readFileSync(pausePath, 'utf8')) : {};
        const pauseId = pauseValue.session_id === 'unit-session' ? pauseValue.id : '';
        fs.writeFileSync(path.join(root, 'lease.json'), JSON.stringify({ session_id: 'unit-session', expires_at: now / 1000 + 1.5 }));
        fs.writeFileSync(path.join(root, 'request.json'), JSON.stringify({ protocol: 1, session_id: 'unit-session', id,
            after_frame: state.frame, pause_id: pauseId, payload: { action, frames: 1, ...options } }));
    }
    return { root, context, input, touch, stats, step, send, originalUpdate,
        status() { return JSON.parse(fs.readFileSync(path.join(root, 'status.json'), 'utf8')); },
        result(id) { return JSON.parse(fs.readFileSync(path.join(root, 'results', id + '.json'), 'utf8')); },
        setBusy(value) { sceneBusy = value; },
        setMoving(value) { moving = value; },
        advance(ms) { now += ms; if (timer) timer(); } };
}

for (const version of ['MV', 'MZ']) {
    test(version + ': bounded input, real update and render receipt use one protocol', t => {
        const f = fixture(t, version);
        assert.equal(f.status().paused, true);
        f.send('resume', 'resume'); f.step(5);
        assert.equal(f.result('resume').ok, true);
        const before = f.status().frame;
        f.send('right', 'move', { direction: 'right', frames: 3 });
        f.input._currentState.left = true;
        f.step(8);
        assert.equal(f.stats.right, 3);
        assert.equal(f.stats.left, 0);
        const result = f.result('right');
        assert.equal(result.consumed, true);
        assert.ok(result.frame > before);
        assert.ok(result.capture_frame >= result.frame);
        assert.ok(fs.existsSync(path.join(f.root, result.screenshot)));
        const count = f.stats.right;
        f.step(5);
        assert.equal(f.stats.right, count, '旧命令不得重复执行');
        assert.deepEqual(f.status().held, []);
    });
}

test('engine button declarations do not allow PS1 square on MV or MZ', t => {
    for (const version of ['MV', 'MZ']) {
        const f = fixture(t, version);
        assert.deepEqual(f.status().buttons, ['up', 'down', 'left', 'right', 'ok', 'cancel', 'shift', 'pageup', 'pagedown']);
        f.send('resume', 'resume'); f.step(5);
        f.send('square', 'press', { button: 'square' }); f.step(5);
        assert.equal(f.result('square').ok, false);
        assert.equal(f.status().paused, true);
        assert.deepEqual(f.status().held, []);
    }
});

test('plugin absent environment does not hook manual input', t => {
    const f = fixture(t, 'MZ', false);
    assert.equal(f.input.update, f.originalUpdate);
    assert.equal(fs.existsSync(path.join(f.root, 'status.json')), false);
});

test('an unstarted scene is not queried for windows before initialization', t => {
    const f = fixture(t);
    f.send('resume', 'resume'); f.step(5);
    let started = false;
    f.context.SceneManager._scene = {
        isStarted() { return started; },
        isBusy() {
            assert.ok(started, 'engine window fields do not exist before scene startup');
            return false;
        },
        update() {}
    };
    f.advance(300);
    assert.equal(f.status().paused, false);
    assert.equal(f.status().input_ready, false);
    started = true;
    f.advance(300);
    assert.equal(f.status().input_ready, true);
});

test('late pause from an old session does not stop a new session', t => {
    const f = fixture(t);
    f.send('resume', 'resume'); f.step(5);
    fs.writeFileSync(path.join(f.root, 'pause.json'), JSON.stringify({ id: 'late-stop', session_id: 'old-session' }));
    f.step(2);
    assert.equal(f.status().paused, false);
});

test('manual pause releases input and stale queued actions cannot revive on resume', t => {
    const f = fixture(t);
    f.send('resume', 'resume'); f.step(5);
    f.send('long', 'move', { direction: 'right', frames: 100 }); f.step(2);
    fs.writeFileSync(path.join(f.root, 'pause.json'), JSON.stringify({ id: 'manual-stop', session_id: 'unit-session' }));
    f.step(2);
    assert.equal(f.status().paused, true);
    assert.deepEqual(f.status().held, []);
    assert.equal(f.result('long').ok, false);
    const count = f.stats.right;
    f.send('stale', 'move', { direction: 'left', frames: 2 }); f.step(3);
    assert.equal(f.stats.left, 0);
    f.send('new-resume', 'resume'); f.step(5);
    assert.equal(f.result('new-resume').ok, true);
    assert.equal(f.stats.right, count);
});

test('closed nonselectable message and gold windows do not block ordinary map input', t => {
    const f = fixture(t, 'MV');
    f.context.SceneManager._scene._windowLayer = { children: [
        { active: true, visible: true, isOpen() { return false; }, constructor: { name: 'Window_Message' } },
        { active: true, visible: true, isOpen() { return false; }, constructor: { name: 'Window_Gold' } }
    ] };
    f.send('resume', 'resume'); f.step(5);
    assert.equal(fs.existsSync(path.join(f.root, 'results/resume.json')), true);
    assert.equal(f.result('resume').input_ready, true);
});

test('two separate confirms produce two native trigger edges instead of leaving previous state pressed', t => {
    const f = fixture(t);
    f.send('resume', 'resume'); f.step(5);
    f.send('first', 'confirm'); f.step(5);
    f.send('second', 'confirm'); f.step(5);
    assert.equal(f.stats.okEdges, 2);
});

test('canonical cancel uses native escape so both map menu and submenu cancel work', t => {
    const f = fixture(t);
    f.send('resume', 'resume'); f.step(5);
    f.send('menu', 'cancel'); f.step(5);
    assert.equal(f.stats.escape, 1);
    f.send('button-cancel', 'press', { button: 'cancel' }); f.step(5);
    assert.equal(f.stats.escape, 2);
});

test('movement settles before delivering the next decision frame', t => {
    const f = fixture(t);
    f.send('resume', 'resume'); f.step(5);
    f.send('move', 'move', { direction: 'right', frames: 1 }); f.step(1);
    f.setMoving(true); f.step(5);
    assert.equal(fs.existsSync(path.join(f.root, 'results/move.json')), false);
    f.setMoving(false); f.step(3);
    assert.equal(f.result('move').input_ready, true);
});

test('transitioning scene must settle before reporting a usable action screenshot', t => {
    const f = fixture(t);
    f.setBusy(true);
    f.send('resume', 'resume'); f.step(6);
    assert.equal(fs.existsSync(path.join(f.root, 'results/resume.json')), false, '转场未结束不应提前交付旧界面');
    f.setBusy(false); f.step(3);
    assert.equal(f.result('resume').ok, true);
    assert.equal(f.status().input_ready, true);
});

test('manual pause leaves ordinary human input working on later frames', t => {
    const f = fixture(t);
    f.send('resume', 'resume'); f.step(5);
    fs.writeFileSync(path.join(f.root, 'pause.json'), JSON.stringify({ id: 'takeover', session_id: 'unit-session' }));
    f.step(1);
    const polls = f.stats.nativePolls;
    f.input._currentState.right = true;
    f.step(1);
    assert.equal(f.input._currentState.right, true, '人工接管后不能每帧清空真人输入');
    assert.ok(f.stats.nativePolls > polls);
});

test('lease expiry and capture failure release keys and stop automatic control', t => {
    const f = fixture(t);
    f.send('resume', 'resume'); f.step(5);
    f.send('held', 'press', { button: 'right', frames: 100 }); f.step(2);
    f.advance(2000);
    assert.equal(f.status().paused, true);
    assert.deepEqual(f.status().held, []);
    f.send('resume-again', 'resume'); f.step(5);
    f.context.Graphics._canvas.toDataURL = () => { throw new Error('capture unavailable'); };
    f.send('no-image', 'confirm'); f.step(5);
    assert.equal(f.result('no-image').ok, false);
    assert.equal(f.status().paused, true);
});

test('lost focus does not clear synthetic hold and native gamepad poll is suppressed in AI mode', t => {
    const f = fixture(t);
    f.send('resume', 'resume'); f.step(5);
    const polls = f.stats.nativePolls;
    f.send('hold', 'move', { direction: 'right', frames: 3 });
    f.step(1); f.input._onLostFocus(); f.step(5);
    assert.equal(f.stats.right, 3);
    assert.equal(f.stats.nativePolls, polls);
});

test('invalid plugin commands are rejected independently of host validation', t => {
    const f = fixture(t);
    f.send('resume', 'resume'); f.step(5);
    f.send('invalid', 'move', { direction: 'right', frames: -1 }); f.step(3);
    assert.equal(f.result('invalid').ok, false);
    assert.equal(f.stats.right, 0);
});

test('v1 MV/MZ reject millisecond fields without producing input', t => {
    for (const version of ['MV', 'MZ']) {
        const f = fixture(t, version);
        f.send('resume', 'resume'); f.step(5);
        f.send('time', 'move', { direction: 'right', duration_ms: 100 }); f.step(5);
        assert.equal(f.result('time').ok, false);
        assert.equal(f.stats.right, 0);
        assert.deepEqual(f.status().held, []);
        f.send('mixed', 'move', { direction: 'right', frames: 1, duration_ms: 100 }); f.step(5);
        assert.equal(f.result('mixed').ok, false);
        assert.equal(f.stats.right, 0);
        f.send('v2', 'move', { direction: 'right', frames: 1 });
        const requestPath = path.join(f.root, 'request.json');
        const request = JSON.parse(fs.readFileSync(requestPath, 'utf8'));
        request.protocol = 2;
        fs.writeFileSync(requestPath, JSON.stringify(request));
        f.step(5);
        assert.equal(f.stats.right, 0);
    }
});
