// In the desktop app: her window lets clicks through to whatever is behind it, except on her and on the
// controls at its foot, and she is dragged by her body. Whether the cursor is on her is the alpha of the
// pixel under it, read right after a frame is drawn; the app (app/main.js) ignores the mouse while it is
// neither. A drag only says when it starts and ends: the app moves the window with the cursor itself, as
// the page's own screen coordinates lag behind a window that is moving.
export function createDesktop(renderer, app) {
  if (!app?.hover) return { drawn() {}, takes: () => false, watch() {} };
  const canvas = renderer.domElement, gl = renderer.getContext(), pixel = new Uint8Array(4);
  let probe = null, over = false, drag = false;
  // The window takes the mouse where she is, and a control is the second place it has to take it: a
  // press on the talk button would otherwise be passed through to whatever is behind the window, and a
  // button nobody can press is not a control. Both places are one answer, because the app asks one
  // question -- and `over` stays hers alone, so a press on a control never becomes a drag of the window.
  let overControl = false, told = false;
  const tell = () => {
    const wants = over || overControl;
    if (wants === told) return;
    app.hover(told = wants);
  };
  const hover = now => { if (now !== over) { over = now; tell(); } };
  const watch = elements => {
    for (const element of elements) {
      element.addEventListener('pointerenter', () => { overControl = true; tell(); });
      element.addEventListener('pointerleave', () => { overControl = false; tell(); });
    }
  };
  // A press on her is the window's in the app: it moves her. js/main.js asks this before it hands the
  // same press to the camera, which took it as a finger and orbited her while the window slid — she
  // drifted as the window caught up a frame late, and at the screen's edge she turned with the cursor.
  // It answers for the whole press, not only its first event, so the answer does not depend on which
  // listener happens to run first.
  const takes = e => over && e.button === 0;

  addEventListener('pointermove', e => { if (!drag) probe = [e.clientX, e.clientY]; });
  document.addEventListener('pointerleave', () => { if (!drag) hover(false); });
  canvas.addEventListener('pointerdown', e => {
    if (!takes(e) || drag) return;
    drag = true;
    app.drag(true);
    canvas.setPointerCapture(e.pointerId); // hears the release even off the window
  });
  const drop = () => { if (drag) app.drag(false); drag = false; };
  canvas.addEventListener('pointerup', drop);
  canvas.addEventListener('lostpointercapture', drop);

  return {
    takes,
    watch,
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
