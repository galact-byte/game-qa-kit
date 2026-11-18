const {app, BrowserWindow, ipcMain} = require('electron');
const fs = require('fs');
const path = require('path');
const bridge = require("./MainBridge.js");
bridge.configure(app);
process.on('uncaughtException', error => {
    fs.writeFileSync(path.join(process.env.GAMEQA_FIXTURE_ROOT, 'main-error.txt'), error.stack);
    app.exit(2);
});
ipcMain.on('fixture-main-stall', () => {
    const end = Date.now() + 4000;
    while (Date.now() < end) {}
});
app.on('ready', () => {
    const win = new BrowserWindow({width: 480, height: 320, useContentSize: true, show: false,
        webPreferences: {nodeIntegration: true, contextIsolation: false,
            enableRemoteModule: true, backgroundThrottling: false}});
    win.on('close', event => {
        if (fs.existsSync(path.join(process.env.GAMEQA_FIXTURE_ROOT, 'prevent-quit'))) event.preventDefault();
    });
    const capturePage = win.webContents.capturePage.bind(win.webContents);
    win.webContents.capturePage = (...args) =>
        fs.existsSync(path.join(process.env.GAMEQA_FIXTURE_ROOT, 'capture-stall'))
            ? new Promise(() => {}) : capturePage(...args);
    bridge.start(win);
    win.webContents.on('console-message', (_event, level, message) => {
        fs.appendFileSync(path.join(process.env.GAMEQA_FIXTURE_ROOT, 'console.log'),
            JSON.stringify({level, message}) + '\n');
    });
    win.webContents.setUserAgent(win.webContents.getUserAgent() + ' TyranoErectron GameQA');
    win.loadFile(path.join(__dirname, 'index.html'));
    win.once('ready-to-show', () => { win.removeMenu(); win.show(); win.focus(); });
});
