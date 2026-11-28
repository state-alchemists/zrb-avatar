// The camera in this browser, on its way to zrb's /photo: the phone in your hand is a camera zrb
// can ask for by name, and the names come from here, because only the page can see them.
//
// Nothing opens a camera on load. A browser asks before it hands one over, and asking on load is a
// prompt with nothing behind it, which reads as a broken page -- so the first grant is a key (`c`,
// see main.js), and the names arrive with it: a browser hides a camera's label until the page has
// been allowed a camera at all, so before that a page knows how many it has and not what they are.
// zrb asks one photo at a time, and each ask opens a camera, takes one picture and closes it again,
// so a phone in a pocket is not filming.
//
// The picture is a JPEG at most FRAME_MAX_PX on its longest side. A phone's sensor is several
// thousand pixels wide and its data URL several megabytes; zrb looks at the photo rather than
// prints it, and a smaller one is quicker to take, send and read.

const JPEG_QUALITY = 0.85;
const FRAME_MAX_PX = 1280; // longest side of the photo handed to zrb
// How long to wait for a first picture from a camera that has just opened, when the browser cannot
// say that one arrived.
const FIRST_FRAME_MS = 250;

export function createCamera({ onState, page = '' } = {}) {
  let names = [], error = '';

  const changed = () => onState?.({ names, error });

  function videoInputs() {
    if (!navigator.mediaDevices?.enumerateDevices) return Promise.resolve([]);
    return navigator.mediaDevices
      .enumerateDevices()
      .then(devices => devices.filter(device => device.kind === 'videoinput'))
      .catch(() => []);
  }

  // Which camera zrb means by the name it was given. The names this page published are the labels,
  // so the match is a label; a device id is accepted too, for a page that had ids and no labels.
  function deviceIdFor(devices, wanted) {
    if (!wanted) return '';
    const want = wanted.trim().toLowerCase();
    const labelled = devices.filter(device => device.label);
    const exact = labelled.find(device => device.label.trim().toLowerCase() === want);
    if (exact) return exact.deviceId;
    const partial = labelled.find(device => device.label.trim().toLowerCase().includes(want));
    if (partial) return partial.deviceId;
    const asId = devices.find(device => device.deviceId === wanted);
    return asId ? asId.deviceId : '';
  }

  // Why a camera did not open, in words the user can act on: this sentence is what zrb repeats
  // when /photo finds no photo, so it says what happened rather than that something did.
  function whyNot(e, wanted) {
    const name = e?.name || '';
    if (name === 'NotAllowedError' || name === 'SecurityError') {
      return 'the page was not allowed to use its camera';
    }
    if (name === 'NotFoundError' || name === 'OverconstrainedError') {
      return wanted ? `this page has no camera called "${wanted}"` : 'this page has no camera';
    }
    if (name === 'NotReadableError' || name === 'AbortError') {
      return 'the camera is already in use by another app';
    }
    return `the camera could not be opened: ${e?.message || name || e}`;
  }

  // The names this page can offer zrb. Posted rather than kept here: the server is the one zrb
  // asks, and a page that reloaded says its piece again on the way up. The page goes with them,
  // because a name is only one page's: two pages are normal now -- a laptop and a phone -- and zrb
  // asks a photo of the page that named the camera rather than of every page in the room.
  //
  // One at a time: the server keeps whichever report arrives last, so two on their way together can land
  // in the wrong order. Pressing the button off and on again before the first one finished let the empty
  // report arrive after the new names and take the camera away from zrb while the button read "camera on".
  // Queued here instead, so the order they were sent in is the order they arrive in.
  let reporting = Promise.resolve();
  function report(list) {
    const body = JSON.stringify({ devices: list });
    reporting = reporting
      .then(() => fetch(`frame/devices?page=${encodeURIComponent(page)}`, { method: 'POST', body }))
      .catch(() => {
        // The server is going away; the names are published again on the next grant or photo.
      });
    return reporting;
  }

  async function publish() {
    const found = await videoInputs();
    // A page not allowed a camera knows only how many it has: "camera 2" is a position rather than a name,
    // so it is neither offered to zrb nor worn by the camera button. A button reading "camera on" over a
    // camera nobody granted spends its first press taking back a name that was never given, and the press
    // that should have asked for the camera is gone. An empty report is still a report -- the server drops
    // a page's names when one is handed to it -- which is how a camera whose permission was taken away
    // stops being offered.
    names = found.filter(device => device.label).map(device => device.label);
    changed();
    await report(names);
    return names;
  }

  // One picture from *device* -- a name this page published, or empty for whichever camera the
  // browser picks. Either an image or a refusal, never both: zrb shows one or says the other.
  async function shoot(device) {
    if (!navigator.mediaDevices?.getUserMedia) return { refusal: 'this page has no camera in it' };
    const wanted = (device || '').trim();
    const devices = await videoInputs();
    const deviceId = deviceIdFor(devices, wanted);
    if (wanted && !deviceId) return { refusal: `this page has no camera called "${wanted}"` };
    const constraints = {
      video: deviceId ? { deviceId: { exact: deviceId } } : true,
      audio: false,
    };
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia(constraints);
    } catch (e) {
      return { refusal: whyNot(e, wanted) };
    }
    try {
      const video = Object.assign(document.createElement('video'), {
        srcObject: stream,
        muted: true,
        playsInline: true,
      });
      await video.play();
      await firstFrame(video);
      const width = video.videoWidth, height = video.videoHeight;
      if (!width || !height) return { refusal: 'the camera opened but sent no picture' };
      const fit = Math.min(1, FRAME_MAX_PX / Math.max(width, height));
      const canvas = Object.assign(document.createElement('canvas'), {
        width: Math.round(width * fit),
        height: Math.round(height * fit),
      });
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      return { image: canvas.toDataURL('image/jpeg', JPEG_QUALITY) };
    } catch (e) {
      return { refusal: whyNot(e, wanted) };
    } finally {
      // The camera is closed again whatever happened: an open one is a phone still filming.
      stream.getTracks().forEach(track => track.stop());
    }
  }

  // A camera that has just opened sends its first picture a moment later, and drawing before it
  // arrives is a black photo. A browser that can say a frame is ready does; the rest get a wait.
  function firstFrame(video) {
    return new Promise(resolve => {
      if (typeof video.requestVideoFrameCallback === 'function') {
        video.requestVideoFrameCallback(() => resolve());
      } else {
        setTimeout(resolve, FIRST_FRAME_MS);
      }
    });
  }

  // The answer to one ask from zrb, by the id it asked with. A photo also means the browser has now
  // labelled this page's cameras, so they are published: the next ask can name the one just used.
  async function answer(id, device) {
    // A press taken back between the ask and the photo is a photo not taken: the camera is off by the time
    // this runs, and opening one for a request that arrived while it was on is the one way "off" would not
    // mean off. The refusal is answered rather than dropped, so the ask ends with words instead of nothing.
    const result = names.length ? await shoot(device) : { refusal: 'this page has its camera off' };
    try {
      await fetch(`frame?id=${encodeURIComponent(id)}`, {
        method: 'POST',
        body: JSON.stringify(result),
      });
    } catch {
      return; // the ask is over either way; a page that cannot answer is a missing photo
    }
    if (result.image) publish();
  }

  // The key: ask for a camera and give it straight back, so the browser's question is asked once,
  // on purpose, and the names it was keeping become known.
  async function ask() {
    const started = navigator.mediaDevices?.getUserMedia
      ? navigator.mediaDevices.getUserMedia({ video: true, audio: false })
      : Promise.reject({ name: 'NotFoundError' });
    try {
      const stream = await started;
      stream.getTracks().forEach(track => track.stop());
      error = '';
    } catch (e) {
      error = whyNot(e, '');
    }
    changed();
    await publish();
  }

  // Off: the names go back, so zrb stops asking this page for photos. Nothing is left running -- a
  // photo opens a camera for its one picture and closes it again -- so what is switched off is the
  // permission to be asked, which is all this page holds between photos.
  async function release() {
    names = [];
    error = '';
    changed();
    // Queued behind any report still on its way, so the last press is the one the server keeps: an empty
    // report that overtook a newer one took a camera away while the button read "camera on".
    await report([]);
  }

  return {
    get names() { return [...names]; }, // what this page told zrb it has
    get error() { return error; }, // the last refusal, in words, until the next attempt clears it
    ask,
    release,
    publish,
    answer,
  };
}
