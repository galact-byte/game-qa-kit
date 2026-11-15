'use strict';
const {app, ipcMain} = require('electron');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const now = () => Number(process.hrtime.bigint() / BigInt(1000000));
const session = process.env.GAMEQAKIT_SESSION_ID;
const directory = process.env.GAMEQAKIT_BRIDGE_DIR;
const identity = {protocol: 2, session_id: session, pid: process.pid, exe_path: process.execPath};

function configure(app) {
    if (process.versions.electron !== '7.1.2' || !session || !path.isAbsolute(directory || '')) {
        throw new Error('Unvalidated runtime or missing GameQA session');
    }
    const root = process.env.GAMEQAKIT_RUNTIME_ROOT;
    if (!path.isAbsolute(root || '')) throw new Error('Explicit isolated data root required');
    app.setName('GameQA TyranoScript Fixture');
    for (const pair of [['appData', 'appdata'], ['userData', 'userdata'], ['cache', 'cache'], ['temp', 'tmp']]) {
        app.setPath(pair[0], path.join(root, pair[1]));
    }
    app.commandLine.appendSwitch('disk-cache-dir', path.join(root, 'cache'));
    app.commandLine.appendSwitch('disable-background-timer-throttling');
    process.env.GAMEQAKIT_OBSERVER_BINDING = JSON.stringify(identity);
}

// 控制文件与证据均使用有界原子发布；仅 Windows 共享冲突可短暂重试。
function retry(operation) {
    const deadline = now() + 100;
    for (;;) {
        try { return operation(); }
        catch (error) {
            if (!['EACCES', 'EPERM', 'EBUSY'].includes(error.code) || now() >= deadline) throw error;
            Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 5);
        }
    }
}
function read(name) {
    let data;
    try { data = retry(() => {
        const target = path.join(directory, name + '.json');
        if (fs.statSync(target).size > 65536) throw new Error('Control file is too large');
        return fs.readFileSync(target, 'utf8');
    }); } catch (error) {
        if (error.code === 'ENOENT') return {};
        throw error;
    }
    const value = JSON.parse(data);
    if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('Control file must be an object');
    return value;
}
function write(name, value) {
    const target = path.join(directory, name);
    fs.mkdirSync(path.dirname(target), {recursive: true});
    fs.writeFileSync(target + '.part', value);
    retry(() => fs.renameSync(target + '.part', target));
}
function json(name, value) { write(name + '.json', JSON.stringify(value)); }
function samePath(a, b) { return typeof a === 'string' && path.resolve(a).toLowerCase() === path.resolve(b).toLowerCase(); }

class MainBridge {
    constructor(win) {
        this.win = win;
        const meta = read('connection');
        if (meta.protocol !== 2 || meta.session_id !== session || meta.pid !== process.pid
                || meta.engine !== 'tyranoscript' || !samePath(meta.exe_path, process.execPath)) {
            throw new Error('Connection does not match this process and session');
        }
        this.paused = true;
        this.pauseId = '';
        this.fault = '';
        this.held = false;
        this.active = null;
        this.seq = 0;
        this.rendererSeq = 0;
        this.ready = false;
        this.readyDraws = 0;
        this.stopping = false;
        this.observation = '';
        this.lastCapture = 0;
        this.capturePending = '';
        this.beat = 0;
        this.captureCount = 0;
        ipcMain.on('gameqa-observation', (event, value) => {
            if (event.sender !== win.webContents || !value || value.session_id !== session
                    || value.pid !== process.pid || value.protocol !== 2 || !samePath(value.exe_path, process.execPath)) return;
            try { this.observed(value); } catch (error) { this.fail(error); }
        });
        win.webContents.once('did-finish-load', () => {
            const source = fs.readFileSync(path.join(__dirname, 'Observer.js'), 'utf8');
            win.webContents.executeJavaScript(source).catch(error => this.fail(error));
        });
        setInterval(() => {
            try { this.pump(); } catch (error) { this.fail(error); }
        }, 20);
        setInterval(() => {
            try {
                json('watchdog', Object.assign({}, identity, {seq: ++this.beat,
                    renderer_seq: this.rendererSeq, ready: this.ready,
                    release_pending: this.active && this.active.pending
                        ? this.active.request.id + ':' + this.active.pending : '',
                    capture_pending: this.capturePending}));
            } catch (_error) {
                // 发布失败由独立宿主的单调时钟收口，不伪造继续活跃。
            }
        }, 30);
    }

    state() {
        return Object.assign({}, identity, {updated_at: Date.now() / 1000,
            paused: this.paused, pause_id: this.pauseId, error: this.fault,
            capabilities: this.seq && !this.fault ? ['confirm', 'wait'] : [], buttons: ['ok'],
            max_duration_ms: 1000, timing_mode: 'duration_ms', pause_scope: 'input',
            simulation_pause: false, observation_seq: this.seq,
            observation_screenshot: this.observation, held: this.held ? ['ok'] : []});
    }
    status() { json('status', this.state()); }
    leaseValid() {
        const lease = read('lease');
        const remaining = lease.expires_at - Date.now() / 1000;
        return lease.session_id === session && typeof lease.expires_at === 'number'
            && Number.isFinite(lease.expires_at) && remaining > 0 && remaining <= 2;
    }
    arm(phase) {
        const active = this.active;
        if (active.pending === phase) return;
        active.pending = phase;
        this.win.webContents.send('gameqa-arm', {session_id: session, id: active.request.id, phase});
    }
    release() {
        if (!this.active || this.active.releaseStarted !== null) return;
        this.active.releaseStarted = now();
        this.arm('keyup');
    }
    interrupt(message) {
        if (this.active) {
            if (!this.active.error) this.active.error = message;
            if (this.held || this.active.pending === 'keydown') this.release();
            else this.paused = true;
        } else this.paused = true;
    }
    fail(error) {
        if (!this.fault) this.fault = String(error.message || error);
        try {
            this.interrupt(this.fault);
            this.status();
        } catch (_error) {
            // 不延后释放期限；独立宿主会处理无法继续发布状态的情况。
        }
    }
    observed(value) {
        if (value.kind === 'draw') {
            if (Number.isSafeInteger(value.seq) && value.seq > this.rendererSeq) this.rendererSeq = value.seq;
            if (value.ready === true && this.win.isVisible()) this.readyDraws++;
            this.ready = this.ready || this.readyDraws >= 2;
            return;
        }
        const active = this.active;
        if (!active || value.id !== active.request.id || value.phase !== active.pending) return;
        if (value.kind === 'armed') {
            if (active.sent === value.phase) return;
            active.sent = value.phase;
            if (value.phase === 'keydown') this.held = true;
            this.win.webContents.sendInputEvent({type: value.phase === 'keydown' ? 'keyDown' : 'keyUp', keyCode: 'Return'});
        } else if (value.kind === 'input' && value.trusted === true) {
            if (value.phase === 'keydown' && value.held === true) {
                active.press = true;
                active.until = now() + active.request.payload.duration_ms;
                active.pending = '';
                if (active.error) this.release();
            } else if (value.phase === 'keyup' && value.held === false) {
                active.release = true;
                active.pending = '';
                active.afterDraw = this.rendererSeq;
                this.held = false;
                if (active.error) this.paused = true;
            }
        }
    }
    accept(request) {
        const id = request.id;
        if (typeof id !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(id)) throw new Error('Invalid command id');
        if (fs.existsSync(path.join(directory, 'results', id + '.json'))) return;
        const payload = request.payload;
        const fields = {resume: ['action'], wait: ['action', 'duration_ms'], confirm: ['action', 'duration_ms']};
        const allowed = payload && fields[payload.action];
        if (request.protocol !== 2 || request.session_id !== session || request.pause_id !== this.pauseId
                || !Number.isSafeInteger(request.after_observation_seq) || request.after_observation_seq < 0
                || request.after_observation_seq > this.seq || !allowed
                || Object.keys(payload).sort().join() !== allowed.slice().sort().join()) {
            throw new Error('Invalid request identity, pause generation or action fields');
        }
        if (payload.action !== 'resume' && (this.paused || !Number.isInteger(payload.duration_ms)
                || payload.duration_ms < 1 || payload.duration_ms > 1000)) throw new Error('Invalid duration or paused control');
        if (!this.leaseValid()) throw new Error('Lease missing or expired');
        const current = now();
        this.active = {request, started: current, until: current + (payload.duration_ms || 0),
            afterDraw: this.rendererSeq, press: false, release: false, releaseStarted: null,
            pending: '', sent: '', error: ''};
        if (payload.action === 'resume') this.paused = false;
        if (payload.action === 'confirm') this.arm('keydown');
    }
    pump() {
        // 释放及失败回执处理必须先于可能损坏的控制文件读取。
        const active = this.active;
        if (active && active.press && !active.release && active.releaseStarted === null && now() >= active.until) this.release();
        if (active && !this.capturePending && this.rendererSeq > active.afterDraw
                && (active.release || (!active.pending && !this.held && now() >= active.until))) {
            this.complete(active).catch(error => this.fail(error));
        }
        const stop = read('stop');
        if (stop.session_id === session) {
            this.stopping = true;
            this.interrupt('Host shutdown requested');
        }
        if (this.stopping && !this.active && !this.held && !this.capturePending) {
            app.quit();
            return;
        }
        const pause = read('pause');
        if (pause.session_id === session && pause.id !== this.pauseId) {
            if (typeof pause.id !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(pause.id)) throw new Error('Invalid pause id');
            this.pauseId = pause.id;
            this.interrupt('Action interrupted by pause');
        }
        if (!this.paused && !this.leaseValid()) this.interrupt('Lease expired');
        if (!this.stopping && !this.active && !this.fault && this.seq) {
            const request = read('request');
            if (Object.keys(request).length) this.accept(request);
        }
        if (!this.stopping && this.ready && !this.fault && !this.capturePending && !this.active && now() - this.lastCapture >= 200) {
            this.capture('observation').catch(error => this.fail(error));
        }
        this.status();
    }
    async capture(id) {
        this.capturePending = String(++this.captureCount);
        try {
            const image = await this.win.webContents.capturePage();
            const size = image.getSize();
            if (image.isEmpty() || size.width * size.height > 16777216) throw new Error('Empty or oversized observation');
            const pixels = image.toBitmap();
            let dark = 0, light = 0, count = 0;
            for (let y = 1; y < size.height - 1; y++) {
                for (let x = 1; x < size.width - 1; x++) {
                    const offset = (y * size.width + x) * 4;
                    const a = pixels[offset], b = pixels[offset + 1], c = pixels[offset + 2];
                    if (Math.max(a, b, c) <= 8) dark++;
                    if (Math.min(a, b, c) >= 247) light++;
                    count++;
                }
            }
            if (!count || Math.max(dark, light) / count >= .999) throw new Error('Rejected blank observation');
            const png = image.toPNG();
            if (png.length > 16 * 1024 * 1024) throw new Error('Oversized screenshot');
            const relative = 'screenshots/' + id + '.png';
            write(relative, png);
            this.observation = relative;
            this.seq++;
            this.lastCapture = now();
            return {screenshot: relative, screenshot_sha256: crypto.createHash('sha256').update(png).digest('hex')};
        } finally { this.capturePending = ''; }
    }
    async complete(active) {
        // 同一请求只能有一个完成任务；处理期间暂停／租约仍由 pump 检查。
        active.afterDraw = Number.MAX_SAFE_INTEGER;
        let shot = {};
        if (!active.error) {
            try { shot = await this.capture(active.request.id); }
            catch (error) { this.fail(error); }
        }
        if (!active.error) {
            try { if (!this.leaseValid()) this.interrupt('Lease expired during capture'); }
            catch (error) { this.fail(error); }
        }
        const result = Object.assign({}, identity, shot, {id: active.request.id,
            ok: !active.error, error: active.error, timing_mode: 'duration_ms', pause_scope: 'input',
            simulation_pause: false, pause_id: active.request.pause_id,
            consumed: active.press, press_consumed: active.press, release_consumed: active.release,
            held: this.held ? ['ok'] : [], observation_seq: this.seq,
            started_monotonic_ms: active.started, completed_monotonic_ms: now()});
        if (active.error) this.paused = true;
        this.status();
        json('results/' + active.request.id, result);
        this.active = null;
    }
}

module.exports = {configure, start: win => new MainBridge(win)};
