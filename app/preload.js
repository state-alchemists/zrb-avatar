// Lets the page size its window to her reach (see zrb_avatar/js/framing.js), and say whether the
// cursor is on her and when she is dragged (zrb_avatar/js/desktop.js). Only the page we
// loaded gets the bridge: the server can be any URL named on the command line, and a page from
// elsewhere has no business sizing an always-on-top window.
const { contextBridge, ipcRenderer } = require('electron');

// The last one: the main process appends it to the renderer's argv, after any argument the app
// was started with.
const origin = process.argv.findLast(a => a.startsWith('--face-origin='))?.slice('--face-origin='.length);
if (origin && window.location.origin === origin) {
  contextBridge.exposeInMainWorld('faceApp', {
    resize: (width, height) => ipcRenderer.send('resize', width, height),
    hover: over => ipcRenderer.send('hover', over),
    drag: on => ipcRenderer.send('drag', on),
  });
}
