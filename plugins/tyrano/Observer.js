// 仅观察引擎就绪、真实键盘事件与绘制；不改写游戏状态或调用场景接口。
(() => {
    const {ipcRenderer} = require('electron');
    const binding = JSON.parse(process.env.GAMEQAKIT_OBSERVER_BINDING);
    let armed = null;
    let seq = 0;
    let ready = false;
    function send(kind, extra) {
        ipcRenderer.send('gameqa-observation', Object.assign({}, binding, {kind}, extra));
    }
    ipcRenderer.on('gameqa-arm', (_event, value) => {
        if (value.session_id !== binding.session_id || typeof value.id !== 'string') return;
        armed = value;
        send('armed', {id: value.id, phase: value.phase});
    });
    function keyboard(event) {
        if (!armed || !event.isTrusted || event.keyCode !== 13 || event.type !== armed.phase) return;
        const current = armed;
        armed = null;
        // 在 DOM 分发完成后读取引擎的键保持状态，不能在捕获阶段提前宣布松键。
        setTimeout(() => {
            const held = TYRANO.kag.key_mouse.is_keydown === true;
            if (held !== (event.type === 'keydown')) return;
            send('input', {id: current.id, phase: current.phase, trusted: true, held});
        }, 0);
    }
    function frame() {
        if (!ready) {
            const kag = window.TYRANO && TYRANO.kag;
            if (kag && kag.key_mouse && kag.key_mouse.keyconfig.key
                    && kag.stat.current_scenario && kag.ftag.array_tag.length > 0
                    && kag.ftag.current_order_index >= 0
                    && document.querySelector('.layer_event_click')) {
                if (kag.version !== 500) throw new Error('Unvalidated TyranoScript version');
                ready = true;
                document.addEventListener('keydown', keyboard);
                document.addEventListener('keyup', keyboard);
            }
        }
        send('draw', {seq: ++seq, ready});
        requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
})();
