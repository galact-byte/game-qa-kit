[iscript]
f.phase = f.phase || 0;
if (!window.fixtureInstalled) {
    window.fixtureInstalled = true;
    const fs = require('fs'), path = require('path');
    const root = process.env.GAMEQA_FIXTURE_ROOT;
    const marker = name => fs.existsSync(path.join(root, name));
    window.fixtureEvents = [];
    const panel = document.createElement('div');
    panel.id = 'fixture-panel';
    panel.style.cssText = 'position:absolute;left:0;top:0;width:480px;height:320px;background:#203040;color:white;font:22px Arial;z-index:100;pointer-events:none';
    panel.innerHTML = '<div style="padding:20px">GameQA TyranoScript Ver5</div><div id="fixture-stage" style="padding:0 20px"></div><div id="fixture-square" style="position:absolute;top:130px;width:40px;height:40px;background:#ff8020"></div>';
    document.getElementById('tyrano_base').appendChild(panel);
    function record(e) {
        window.fixtureEvents.push({type: e.type, code: e.keyCode, trusted: e.isTrusted});
        if (e.type === 'keyup' && marker('drop-release')) e.stopImmediatePropagation();
        if (e.type === 'keydown' && marker('renderer-stall')) {
            const end = Date.now() + 4000;
            while (Date.now() < end) {}
        }
        if (e.type === 'keydown' && marker('main-stall')) require('electron').ipcRenderer.send('fixture-main-stall');
        if (e.type === 'keydown' && marker('blank-after-confirm')) fs.writeFileSync(path.join(root, 'blank'), '');
    }
    document.addEventListener('keydown', record);
    document.addEventListener('keyup', record);
    function updateFixture() {
        const phase = TYRANO.kag.stat.f.phase;
        const blank = marker('blank'), white = marker('white');
        panel.style.background = white ? '#ffffff' : blank ? '#000000' : marker('background') ? '#405060' : '#203040';
        panel.children[0].style.visibility = blank || white ? 'hidden' : 'visible';
        document.getElementById('fixture-stage').textContent = blank || white ? '' : 'PHASE ' + phase;
        const square = document.getElementById('fixture-square');
        square.style.left = (60 + 100 * (phase % 4)) + 'px';
        square.style.background = white ? '#ffffff' : blank ? '#000000' : phase % 2 ? '#40c080' : '#ff8020';
        if (marker('speck')) square.style.cssText = 'position:absolute;left:2px;top:2px;width:1px;height:1px;background:white';
        const output = {phase, events: window.fixtureEvents, keyHeld: TYRANO.kag.key_mouse.is_keydown,
            mode: TYRANO.kag.config.configSave, project: TYRANO.kag.config.projectID};
        const dest = path.join(root, 'fixture.json');
        fs.writeFileSync(dest + '.part', JSON.stringify(output));
        try { fs.renameSync(dest + '.part', dest); }
        catch (error) { if (!['EACCES', 'EPERM', 'EBUSY'].includes(error.code)) throw error; }
    }
    updateFixture();
    setInterval(updateFixture, 30);
}
[endscript]
*loop
[l]
[eval exp="f.phase++"]
[jump target="loop"]
