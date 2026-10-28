// The face as a small always-on-top, see-through desktop window.
// It only shows the page served by face.py. Arguments: a model name (rem -> rem.vrm) and/or a server URL.
const { app, BrowserWindow, ipcMain, screen } = require('electron');
const path = require('path');

const base = process.argv.find(a => a.startsWith('http')) || 'http://localhost:7777';
const model = process.argv.slice(1).find(a => /^[a-z0-9][\w-]*(\.vrm)?$/i.test(a))?.replace(/\.vrm$/i, ''); // rem or rem.vrm
const url = `${base}/?app${model ? `&model=${model}` : ''}`;
// The server may be any URL named on the command line, so its page is untrusted: it may size the
// window and nothing else. This is the one origin allowed to do that; the preload hands the bridge
// to that origin alone, and the resize handler checks the asker's own origin as well.
const origin = (() => { try { return new URL(base).origin; } catch { return null; } })();
const MIN_SIZE = 160; // the page's own smallest window
// Clicks pass through the window except on her. Linux can't forward the mouse to a window that
// ignores it, so there the page would never hear the cursor come back: the window takes clicks.
const PASS_THROUGH = process.platform === 'win32' || process.platform === 'darwin';

app.whenReady().then(() => {
  const { width, height } = screen.getPrimaryDisplay().workAreaSize;
  const win = new BrowserWindow({
    width: 440, height: 640, x: width - 460, y: height - 660, // until the page sizes it to her reach
    transparent: true, frame: false, hasShadow: false, backgroundColor: '#00000000',
    alwaysOnTop: true, skipTaskbar: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      additionalArguments: [`--face-origin=${origin ?? ''}`], // the origin the preload gives the bridge to
    },
  });
  // The page asks for the size her reach needs from the current camera; the bottom-right corner
  // stays put, so she grows from where you left her. The ask is clamped to the work area of the
  // display she is on, and the window is kept on it: no page can make it enormous or move it
  // off the screen, and only our own page may ask at all.
  const fromPage = event => event.sender === win.webContents && ours(event.senderFrame?.url);
  ipcMain.on('resize', (event, w, h) => {
    if (!fromPage(event) || !Number.isInteger(w) || !Number.isInteger(h)) return;
    const area = screen.getDisplayMatching(win.getBounds()).workArea;
    const size = {
      width: Math.min(Math.max(w, MIN_SIZE), area.width),
      height: Math.min(Math.max(h, MIN_SIZE), area.height),
    };
    const b = win.getBounds();
    win.setBounds({
      x: Math.max(area.x, Math.min(area.x + area.width - size.width, b.x + b.width - size.width)),
      y: Math.max(area.y, Math.min(area.y + area.height - size.height, b.y + b.height - size.height)),
      ...size,
    });
  });
  // The page says when the cursor is on her: then the window takes the mouse, else clicks go
  // through to what is behind it (still forwarding moves, so the page hears it come back).
  if (PASS_THROUGH) win.setIgnoreMouseEvents(true, { forward: true });
  ipcMain.on('hover', (event, over) => {
    if (PASS_THROUGH && fromPage(event) && typeof over === 'boolean') win.setIgnoreMouseEvents(!over, { forward: true });
  });
  // Dragged by her body: between the page's start and end, the window follows the cursor from
  // where the drag began (the page's own screen coordinates lag behind a moving window), kept on
  // the display it lands on. A drag the page never ends stops when the window loses focus.
  let dragging = null;
  const stopDrag = () => { clearInterval(dragging?.timer); dragging = null; };
  ipcMain.on('drag', (event, on) => {
    if (!fromPage(event) || typeof on !== 'boolean') return;
    stopDrag();
    if (!on) return;
    const from = screen.getCursorScreenPoint(), start = win.getBounds();
    dragging = { timer: setInterval(() => {
      if (win.isDestroyed()) return stopDrag();
      const at = screen.getCursorScreenPoint(), b = win.getBounds();
      const to = { ...b, x: start.x + at.x - from.x, y: start.y + at.y - from.y };
      // a smaller display shrinks the window to fit it first, then it is placed on it
      const area = screen.getDisplayMatching(to).workArea;
      const width = Math.min(b.width, area.width), height = Math.min(b.height, area.height);
      win.setBounds({
        x: Math.max(area.x, Math.min(area.x + area.width - width, to.x)),
        y: Math.max(area.y, Math.min(area.y + area.height - height, to.y)),
        width, height,
      });
    }, 16) };
  });
  win.on('blur', stopDrag);
  // The window stays on the server we were pointed at: a page that navigates away (a link, a
  // redirect elsewhere) does not take the always-on-top window with it.
  win.webContents.on('will-navigate', (event, to) => { if (!ours(to)) event.preventDefault(); });
  // The page's size asks are in its own pixels; at any page zoom but 100% (Ctrl+scroll, Ctrl+-,
  // remembered per site) they come out the wrong size and she is drawn blurry. The wheel and keys
  // zoom her camera instead (js/framing.js).
  win.webContents.on('did-finish-load', () => { win.webContents.setZoomFactor(1); win.webContents.setVisualZoomLevelLimits(1, 1); });
  win.webContents.on('zoom-changed', () => win.webContents.setZoomFactor(1));
  win.setAlwaysOnTop(true, 'screen-saver'); // above full-screen windows too
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true }); // macOS: also over full-screen apps' Spaces
  // face.py may start after the app: keep retrying.
  win.webContents.on('did-fail-load', () => setTimeout(() => win.loadURL(url), 2000));
  win.loadURL(url);
});

// Whether a URL is on the server we were pointed at: the page stays there, and only a page there
// may size the window. The frame's own origin is what is checked, so an iframe of another site
// cannot ask in the page's place.
function ours(url) {
  try { return origin !== null && new URL(url).origin === origin; } catch { return false; }
}

app.on('window-all-closed', () => app.quit());
