// The page's ears and mouth: the microphone in this browser on its way to zrb, and her voice out of
// the speaker beside it. A page that does this is the only microphone and the only speaker zrb has
// (`audio.py` says so), which is why there is one format up — 16 kHz mono 16-bit, refused at any
// other — and one stream down, at whatever rate she happened to be made at.
//
// Nothing here starts on load. A microphone, an AudioContext and a sound are all held back until the
// page has been touched, and a refusal reads as a broken page rather than as a browser doing its
// job: startMic() and startEars() belong to a gesture — a key on the existing page (`main.js`), or a
// button on one that has a button. A page that calls neither makes no sound and hears none, and
// nothing about it breaks.
//
// Both worklets are in this file as text. `addModule` takes a URL, and a Blob is a URL a page can
// make out of what it already has: the processor then sits beside the code that feeds it, rather
// than in a file of its own for the server to serve.
//
// The microphone, decimated to the one format the server takes. A browser runs its audio graph at
// 48 kHz whatever the device does, so one output sample is the mean of the input samples inside it —
// a box filter, the cheapest low-pass that is not simply picking every third sample. Good enough for
// speech at 16 kHz, and it is what keeps this a page rather than a resampler.
const CAPTURE = `
class Capture extends AudioWorkletProcessor {
  constructor({ processorOptions }) {
    super();
    this.step = processorOptions.step; // input samples per output sample: 3 for a 48 kHz graph
    this.size = processorOptions.block; // output samples held before one message
    this.pcm = new Int16Array(this.size);
    this.have = 0;
    this.want = 0; // input samples counted into the next output sample
    this.sum = 0;
    this.count = 0;
  }
  process(inputs) {
    const input = inputs[0][0];
    if (!input) return true; // no microphone this block: silence, not a crash
    for (let i = 0; i < input.length; i++) {
      this.sum += input[i];
      this.count++;
      this.want++;
      if (this.want < this.step) continue;
      this.want -= this.step;
      this.push();
    }
    return true;
  }
  push() {
    const mean = Math.max(-1, Math.min(1, this.sum / this.count));
    this.pcm[this.have++] = Math.round(mean * 32767);
    this.sum = 0;
    this.count = 0;
    if (this.have < this.size) return;
    this.port.postMessage(this.pcm.slice());
    this.have = 0;
  }
}
registerProcessor('zrb-capture', Capture);
`;

// Her voice on its way out: a ring these messages fill and process() reads, read at the stream's own
// rate — `step` is the stream's rate against this graph's, so a browser that will not run its graph
// at 22 kHz still plays 22 kHz audio at the right pitch. It starts once it holds the target, and
// stops again when it runs dry, so an uneven connection is a pause rather than a rattle.
const PLAY = `
class Play extends AudioWorkletProcessor {
  constructor({ processorOptions }) {
    super();
    this.step = processorOptions.step;
    this.target = processorOptions.target; // samples held before the first sound
    this.most = processorOptions.most; // samples allowed to wait, past which the oldest goes
    this.ring = new Float32Array(processorOptions.ring);
    this.write = 0; // samples ever written
    this.read = 0; // fractional, so step is not rounded to a whole sample
    this.on = false;
    this.port.onmessage = ({ data }) => {
      if (data.clear) return this.clear();
      if (data.pcm) return this.fill(data.pcm);
      if (data.step) this.step = data.step;
    };
  }
  fill(pcm) {
    const size = this.ring.length;
    // Wait already more than the cap and the oldest of it goes: that much is audio from before now,
    // and hearing it late is worse than losing it -- the rule the server's queues follow. The block
    // arriving is never cut for being long, because its words are hers; only one longer than the
    // whole ring, which no sentence's block is, keeps just its end.
    if (this.write - this.read > this.most) this.read = this.write - this.most;
    for (let i = Math.max(0, pcm.length - size); i < pcm.length; i++) this.ring[this.write++ % size] = pcm[i] / 32768;
  }
  clear() {
    this.read = this.write;
    this.on = false;
  }
  process(_, outputs) {
    const out = outputs[0][0];
    if (!out) return true;
    const size = this.ring.length;
    if (!this.on && this.write - this.read >= this.target) this.on = true;
    for (let i = 0; i < out.length; i++) {
      if (!this.on) { out[i] = 0; continue; }
      const at = Math.floor(this.read), f = this.read - at;
      const a = this.ring[at % size], next = at + 1;
      out[i] = a + ((next < this.write ? this.ring[next % size] : a) - a) * f;
      this.read += this.step;
      if (this.read < this.write) continue;
      // Dry: the rest of this block is silence, and the target has to be reached again before
      // another sample plays.
      this.read = this.write;
      this.on = false;
    }
    return true;
  }
}
registerProcessor('zrb-play', Play);
`;

// What POST /audio takes (audio.py's INBOUND_RATE), and refuses with a 415 otherwise: audio at
// another rate is noise to every speech-to-text backend, so there is nothing here to negotiate.
const RATE = 16000;
// Seconds of the microphone per message. The server's queue holds two blocks and drops the oldest,
// so long blocks buy a reader slack and short ones buy dictation latency; a tenth of a second is
// what the fallback posts, and a fair size to stream in too.
const BLOCK = 0.1;
// Seconds her voice is held before the speaker starts, and the most it may wait behind her once it
// has started. A tenth of a second absorbs a jittery connection without being heard as a delay; past
// half a second what is waiting is late enough to give up.
const TARGET = 0.1, LATE = 0.5;
// Seconds the ring holds. Well past LATE, because a block zrb's player hands over whole is never cut
// for being long: a second of it is a second of her, and dropping its start loses words.
const RING = 4;
const RETRY = 1000; // ms before the out stream is asked for again after it failed
const AHEAD = 2; // fallback posts allowed on their way; past this the past is dropped, not queued

const HEADERS = { 'Content-Type': 'application/octet-stream', 'X-Sample-Rate': String(RATE), 'X-Channels': '1' };
const wait = ms => new Promise(done => setTimeout(done, ms));
const workletUrl = source => URL.createObjectURL(new Blob([source], { type: 'text/javascript' }));
let captureUrl, playUrl; // made the first time a worklet is needed: a Blob at load is a Blob nobody uses

// Whether this browser will send a body it has no length for. `duplex: "half"` is what says yes, and
// one that does not know the word throws here rather than at the POST — which is worth asking before
// the microphone opens, since the answer decides how it is sent.
function streamsBody() {
  try {
    return new Request('/audio', { method: 'POST', body: new ReadableStream(), duplex: 'half' }).duplex === 'half';
  } catch {
    return false;
  }
}

// A block of the stream as samples, and the byte a read cut in half kept back for the next one: the
// out stream is 16-bit little-endian, and a network chunk knows nothing about frames.
function samples(bytes, rest) {
  const all = new Uint8Array(rest.length + bytes.length);
  all.set(rest);
  all.set(bytes, rest.length);
  const even = all.length & ~1, view = new DataView(all.buffer, 0, even), pcm = new Int16Array(even >> 1);
  for (let i = 0; i < pcm.length; i++) pcm[i] = view.getInt16(i * 2, true);
  return [pcm, all.subarray(even)];
}

// A graph at `rate` where the browser has one, and at its own where it does not — the worklets carry
// the difference as a step, so both play and record correctly. Resumed because a context made
// without a gesture waits for ever; every caller here has one.
async function contextAt(rate) {
  let context;
  try {
    context = new AudioContext(rate ? { sampleRate: rate } : undefined);
  } catch {
    context = new AudioContext();
  }
  await context.resume().catch(() => {});
  return context;
}

// `onState({mic, ears, error})` on every change of either switch and on every refusal, so a page can
// show what happened without asking; it may be left out. Callers hold what they return: startMic and
// stopMic, startEars and stopEars, or both at once with stop().
export function createAudio({ onState } = {}) {
  let listening = false, hearing = false, error = '';
  // Where the microphone's blocks go and what ends them, set with the request and cleared with it,
  // so a block can never be captured into a request that has stopped.
  let postPcm = null, endPost = null;
  let micStream = null, micContext = null, micNode = null;
  let earsContext = null, earsNode = null, earsAbort = null;

  const changed = () => onState?.({ mic: listening, ears: hearing, error });
  // Said out loud rather than swallowed: a microphone the browser refused has to look like a
  // refusal, not like a switch that does nothing. Only when it is something new, since a reader
  // retrying once a second would otherwise say it once a second.
  function failed(reason) {
    if (reason === error) return;
    error = reason;
    changed();
  }

  // The request that carries the microphone. Ending it is stopping: the server answers 204 and keeps
  // what arrived, so 'off' is a closed request rather than a stream of silence for somebody
  // downstream to notice.
  function openPost() {
    let open = true;
    if (streamsBody()) {
      const body = new ReadableStream({
        start(controller) {
          // A request that went away under the microphone — a server that answered early, a
          // connection that dropped — cannot be written to any more, and a block every tenth of a
          // second at a closed door is a page full of errors. Off is the truth by then, so it is the
          // microphone that stops.
          postPcm = pcm => {
            if (!open) return;
            try {
              controller.enqueue(pcm);
            } catch {
              open = false;
              stopMic();
              failed('microphone: the request closed');
            }
          };
          endPost = () => {
            if (!open) return;
            open = false;
            controller.close();
          };
        },
      });
      // Not awaited: it answers as the body ends, which is after the microphone is already off. A
      // failure here is a server that went away mid-request, and stopping is all there is to do.
      fetch('/audio', { method: 'POST', body, duplex: 'half', headers: HEADERS })
        .then(res => { if (!res.ok) failed(`microphone refused: ${res.status}`); })
        .catch(() => {});
      return;
    }
    // No duplex: a body with no length cannot be streamed, so the same audio goes a block at a time,
    // one after another because the order is the audio. A block that cannot leave while two others
    // are on their way is audio from before now and is dropped.
    let sent = 0, chain = Promise.resolve();
    postPcm = pcm => {
      if (!open || sent >= AHEAD) return;
      sent++;
      chain = chain
        .then(() => fetch('/audio', { method: 'POST', body: pcm, headers: HEADERS }))
        .then(res => { if (!res.ok) failed(`microphone refused: ${res.status}`); })
        .catch(() => {})
        .finally(() => { sent--; });
    };
    endPost = () => { open = false; };
  }

  // Whatever happened, in this order: the microphone really off, then the request ended. Reversed, a
  // block could still arrive at a controller that had closed, and 'off' has to mean nothing is
  // captured.
  function releaseMic() {
    micNode?.port.close();
    micStream?.getTracks().forEach(track => track.stop());
    micContext?.close().catch(() => {});
    micNode = micContext = micStream = null;
    endPost?.();
    postPcm = endPost = null;
  }

  async function startMic() {
    if (listening) return;
    // Not a refusal by the user but by the page's origin, and it says so in its own words: a phone on
    // plain http is the usual case, and "cannot read properties of undefined" is not an answer.
    if (!navigator.mediaDevices?.getUserMedia) return failed('microphone: needs https on anything but this machine');
    error = '';
    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, channelCount: 1, sampleRate: RATE } });
      micContext = await contextAt(RATE);
      await micContext.audioWorklet.addModule((captureUrl ??= workletUrl(CAPTURE)));
      // Before the graph runs: the blocks it starts sending need somewhere to go.
      openPost();
      const node = new AudioWorkletNode(micContext, 'zrb-capture', {
        processorOptions: { step: micContext.sampleRate / RATE, block: Math.round(RATE * BLOCK) },
      });
      const mute = micContext.createGain();
      mute.gain.value = 0; // the microphone is not played out loud here; the node needs a sink to be pulled
      node.port.onmessage = ({ data }) => postPcm?.(data);
      micContext.createMediaStreamSource(micStream).connect(node);
      node.connect(mute).connect(micContext.destination);
      micNode = node;
      listening = true;
    } catch (e) {
      failed(`microphone: ${e.message || 'did not start'}`);
      releaseMic(); // part of it may have opened before the refusal
    }
    changed();
  }

  // Ending the request, not muting it: nothing is captured after this returns.
  function stopMic() {
    if (!listening) return;
    listening = false;
    releaseMic();
    changed();
  }

  function toggleMic() {
    return listening ? stopMic() : startMic();
  }

  // The speaker's graph, made once: its rate is whatever the first sentence was made at, and a later
  // sentence at another rate only moves the step it is read at, so a rate change is not a click. The
  // stream itself ends when that happens (see hear) and is asked for again.
  async function ears(rate) {
    if (earsNode) {
      earsNode.port.postMessage({ step: rate / earsContext.sampleRate });
      return earsNode;
    }
    earsContext = await contextAt(rate);
    await earsContext.audioWorklet.addModule((playUrl ??= workletUrl(PLAY)));
    const node = new AudioWorkletNode(earsContext, 'zrb-play', {
      processorOptions: {
        step: rate / earsContext.sampleRate,
        target: Math.round(earsContext.sampleRate * TARGET),
        most: Math.round(earsContext.sampleRate * LATE),
        ring: Math.round(earsContext.sampleRate * RING),
      },
    });
    node.connect(earsContext.destination);
    earsNode = node;
    return node;
  }

  // One response, read to its end: a sentence made at a new rate ends it, and the next pass reads the
  // new rate out of the headers rather than playing that one at the old pitch.
  async function hear(res) {
    const rate = Number(res.headers.get('X-Sample-Rate'));
    if (!Number.isFinite(rate) || rate <= 0) throw new Error('no sample rate on the out stream');
    const node = await ears(rate);
    const reader = res.body.getReader();
    let rest = new Uint8Array(0);
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return;
      if (!value?.length) continue;
      const [pcm, kept] = samples(value, rest);
      rest = kept;
      if (pcm.length) node.port.postMessage({ pcm }, [pcm.buffer]);
    }
  }

  // The out stream, asked for again for as long as the ears are on. A failure is a second's pause and
  // another ask: the server may be restarting, and only stopEars ends this.
  async function pump(signal) {
    while (!signal.aborted) {
      try {
        const res = await fetch('/audio/out', { signal, cache: 'no-store' });
        if (!res.ok || !res.body) {
          failed(`audio out: ${res.status}`);
          await wait(RETRY);
          continue;
        }
        await hear(res);
      } catch (e) {
        if (signal.aborted) return;
        failed(`audio out: ${e.message || 'stopped'}`);
        await wait(RETRY);
      }
    }
  }

  function startEars() {
    if (hearing) return;
    hearing = true;
    error = '';
    earsAbort = new AbortController();
    changed();
    // Not awaited: it reads until stopEars aborts it. Caught all the same, so a page whose own
    // onState throws does not take the stream down with it.
    pump(earsAbort.signal).catch(() => {});
  }

  // Off: the response is one long read that only the page can end, so it is aborted, and the graph is
  // closed a moment after it, since an idle one still holds this device's audio session.
  function stopEars() {
    if (!hearing) return;
    hearing = false;
    earsAbort?.abort(); // one long response, and only the page can end it
    earsAbort = null;
    earsNode?.disconnect();
    earsContext?.close().catch(() => {}); // an idle graph still holds this device's audio session
    earsNode = earsContext = null;
    changed();
  }

  return {
    get listening() { return listening; }, // the microphone is being sent, not merely open
    get hearing() { return hearing; }, // the out stream is being asked for and played
    get error() { return error; }, // the last refusal, in words, until the next attempt clears it
    startMic,
    stopMic,
    toggleMic,
    startEars,
    stopEars,
    // Both at once, for a page going away: off then means off in both directions.
    stop() {
      stopMic();
      stopEars();
    },
  };
}
