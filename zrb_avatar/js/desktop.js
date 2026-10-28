// In the desktop app: her window lets clicks through to whatever is behind it, except on her, and
// she is dragged by her body. Whether the cursor is on her is the alpha of the pixel under it,
// read right after a frame is drawn; the app (app/main.js) ignores the mouse while it is not.
// A drag only says when it starts and ends: the app moves the window with the cursor itself, as
// the page's own screen coordinates lag behind a window that is moving.
export function createDesktop(renderer, app) {
  if (!app?.hover) return { drawn() {} };
  const canvas = renderer.domElement, gl = renderer.getContext(), pixel = new Uint8Array(4);
  let probe = null, over = false, drag = false;
  const hover = now => { if (now !== over) app.hover(over = now); };

  addEventListener('pointermove', e => { if (!drag) probe = [e.clientX, e.clientY]; });
  document.addEventListener('pointerleave', () => { if (!drag) hover(false); });
  canvas.addEventListener('pointerdown', e => {
    if (!over || e.button !== 0) return;
    drag = true;
    app.drag(true);
    canvas.setPointerCapture(e.pointerId); // hears the release even off the window
  });
  const drop = () => { if (drag) app.drag(false); drag = false; };
  canvas.addEventListener('pointerup', drop);
  canvas.addEventListener('lostpointercapture', drop);

  return {
    // After each render: is she under the cursor?
    drawn() {
      if (!probe) return;
      const ratio = renderer.getPixelRatio(), [x, y] = probe;
      probe = null;
      gl.readPixels(Math.floor(x * ratio), Math.floor(canvas.height - (y + 1) * ratio), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
      hover(pixel[3] > 16);
    },
  };
}
