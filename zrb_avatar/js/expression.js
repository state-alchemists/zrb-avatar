// Her face, frame by frame: lip sync, blinks (her own rhythm, and the ones she chose), eye contact,
// smiles, the emotions and blends she can wear, and the head tilting while thinking and nodding with
// the voice; on top of whatever the clip does.
import * as THREE from 'three';

const EMOTIONS = ['happy', 'sad', 'angry', 'surprised', 'relaxed']; // VRM preset expressions
// A face is not always one preset: `pout` is angry and sad at once, half and half, and the rest are
// the same idea — two presets at weights that happen to land. Named here rather than weighted at the
// call site, so the only thing a caller chooses is which face, never how much of it: a weight is
// where a dial starts. Auditioned on cream-dress at 560x640 before they were named; `delighted` is
// the smile that also opens the eyes, `sleepy` is the lids, `worried` the mouth corners.
const BLENDS = {
  pout: { angry: 0.5, sad: 0.5 },
  smirk: { happy: 0.4, angry: 0.3 },
  worried: { sad: 0.4, surprised: 0.4 },
  delighted: { happy: 0.7, surprised: 0.3 },
  sleepy: { relaxed: 0.7, sad: 0.3 },
};
const FACES = [...EMOTIONS, ...Object.keys(BLENDS)];
// How hard a face is worn: one word from the tool, a factor here. Soft is half, strong is one and a
// half; every shape is still clamped at 1, because a morph at 1.5 is a face turning inside out.
const STRENGTHS = { soft: 0.5, normal: 1, strong: 1.5 };

export function createExpression(vrm, camera, scene) {
  const em = vrm.expressionManager;
  // VRM 1.0 makes every preset optional, so a name a model does not carry is asked for and left
  // alone rather than set on nothing.
  const has = name => !!em?.getExpression(name);
  const neck = vrm.humanoid.getNormalizedBoneNode('neck') ?? vrm.humanoid.getNormalizedBoneNode('head'); // neck is optional in VRM
  const neckPose = new THREE.Quaternion(), side = new THREE.Vector3(), lift = new THREE.Vector3();
  let t = 0;
  let mouthQueue = [], mouthStep = 0.04, mouthClock = 0, mouth = 0;
  let nextBlink = 2, blinkUntil = 0, tilt = 0;
  // A blink she chose (AvatarBlink) runs on its own clock: the shape it names, how long, and the
  // eyes held still while they are shut.
  let blinkLeftUntil = 0, blinkRightUntil = 0, gazeHold = 0;
  let blinkStart = 0, blinkLeftStart = 0, blinkRightStart = 0;
  let nextSmile = 12, smileUntil = 0, smile = 0;
  const feel = Object.fromEntries(EMOTIONS.map(e => [e, 0]));
  let emotion = null, emotionUntil = 0, emotionScale = 1;
  // Her eyes: on you (the camera), but alive. Eyes jump rather than glide: small jumps around your
  // face every second or few, and now and then a glance away, which a blink often goes with.
  // Thinking looks away, mostly up and to the side; speaking holds your eyes more.
  const gaze = new THREE.Object3D(), gazeAt = new THREE.Vector2();
  let nextGaze = 1;
  // How shut a lid is at this instant: 1 while it is held, with a short ramp at each end so a
  // deliberate half-second blink does not arrive like a switch. The ramp scales with the length,
  // which keeps the 0.12 s automatic one as snappy as it was.
  const lid = (start, until) => {
    const ramp = Math.min(0.06, Math.max(0.02, (until - start) / 4));
    return Math.max(0, Math.min((t - start) / ramp, (until - t) / ramp, 1));
  };
  scene.add(gaze);
  if (vrm.lookAt) vrm.lookAt.target = gaze;

  return {
    get speaking() { return mouthQueue.length > 0; },
    get emotion() { return emotion && t < emotionUntil ? emotion : null; },
    get faces() { return FACES; }, // what show() takes: the presets, and the blends above
    get strengths() { return Object.keys(STRENGTHS); },
    speak(values, step) { mouthQueue.push(...values); mouthStep = step; },
    hush() { mouthQueue = []; },
    smileSoon() { smileUntil = t + 2; }, // done with a task
    show(value, scale = 1) {
      emotion = value;
      emotionUntil = t + 6;
      // A page may be told a strength it does not know (an older tool, a typo in a curl); one is
      // never wrong, and a NaN would make the whole face a NaN.
      emotionScale = Number.isFinite(scale) && scale > 0 ? Math.min(2, scale) : 1;
    },

    // A blink she chose, as against the rhythm below: the shape it names is held for its length,
    // her eyes stop wandering while they are shut, and the automatic blink is pushed back so it
    // cannot land on top of this one half a second later. blinkLeft and blinkRight are optional in
    // VRM 1.0, so a model without them winks with both eyes -- a half-closed eye that never moved
    // is not a wink -- and says so in its own file rather than here.
    blinkNow(eyes, seconds) {
      const until = t + Math.min(2, Math.max(0.08, Number(seconds) || 0.2));
      const side = eyes === 'left' ? 'Left' : eyes === 'right' ? 'Right' : '';
      const shape = side && has(`blink${side}`) ? `blink${side}` : 'blink';
      if (shape === 'blinkLeft') { blinkLeftStart = t; blinkLeftUntil = until; }
      else if (shape === 'blinkRight') { blinkRightStart = t; blinkRightUntil = until; }
      else { blinkStart = t; blinkUntil = until; }
      gazeHold = until;
      nextBlink = until + 1.5 + Math.random() * 3; // the rhythm resumes after, not during
    },

    // This frame's face and head, with animate() moving the body; undo() after rendering.
    pose(dt, state, animate) {
      t += dt;
      // Mouth: play queued loudness at real speed, smoothed.
      mouthClock += dt;
      while (mouthClock >= mouthStep && mouthQueue.length) { mouthClock -= mouthStep; mouthQueue.shift(); }
      if (!mouthQueue.length) mouthClock = 0;
      mouth += ((mouthQueue[0] ?? 0) - mouth) * Math.min(1, dt * 25);

      if (t > nextBlink) { blinkStart = t; blinkUntil = t + 0.12; nextBlink = t + 2 + Math.random() * 4; }

      // Gaze: a point beside the camera, in the camera's own right/up directions (metres). Held
      // still while a blink she chose is closing her eyes: eyes that wander behind the lids read
      // as a twitch rather than as a look.
      if (t > nextGaze && t >= gazeHold) {
        const away = Math.random() < ({ thinking: 0.55, speaking: 0.08 }[state] ?? 0.18);
        if (away) {
          gazeAt.set((Math.random() < 0.5 ? -1 : 1) * (0.4 + Math.random() * 0.6),
            state === 'thinking' ? 0.3 + Math.random() * 0.4 : (Math.random() - 0.6) * 0.6);
          if (Math.random() < 0.4 && t > blinkUntil + 0.5) { blinkStart = t; blinkUntil = t + 0.12; }
        } else {
          gazeAt.set((Math.random() - 0.5) * 0.1, (Math.random() - 0.5) * 0.06);
        }
        nextGaze = t + (away ? 0.7 + Math.random() * 1.5 : 0.6 + Math.random() * 2.4);
      }
      gaze.position.copy(camera.position)
        .addScaledVector(side.setFromMatrixColumn(camera.matrixWorld, 0), gazeAt.x)
        .addScaledVector(lift.setFromMatrixColumn(camera.matrixWorld, 1), gazeAt.y);

      // A soft smile now and then (every 25-50 s), and after finishing a task. 'relaxed' is the
      // gentle closed-mouth smile; 'happy' is usually a wide grin, which looks forced when idle.
      if (t > nextSmile) { smileUntil = t + 3; nextSmile = t + 25 + Math.random() * 25; }
      smile += ((t < smileUntil ? 0.5 : 0) - smile) * Math.min(1, dt * 2);
      // The feeling, as a share of each preset it is made of: a preset is itself, a blend is its
      // parts at the weights above, and how hard it is worn comes from the one word the tool sent.
      // Neither is ever a number a caller chose.
      const want = emotion && t < emotionUntil ? (BLENDS[emotion] ?? { [emotion]: 0.6 }) : {};
      for (const e of EMOTIONS) {
        const share = Math.min(1, (want[e] ?? 0) * emotionScale);
        feel[e] += (share - feel[e]) * Math.min(1, dt * 3);
      }
      // The face is the larger of ours (blinking, lip sync, the smile, zrb's emotion) and the clip's
      // own (a laugh, a frown): zero what a clip may animate, let it, then take the larger of the two.
      const own = { blink: lid(blinkStart, blinkUntil), aa: mouth };
      // A wink gets the same treatment as a blink: eased, zeroed and taken back, so a clip that
      // moves the same lid keeps the larger of the two. A model without the shape is left out of
      // `own` entirely, which leaves whatever the clip does with it alone.
      for (const [name, start, until] of [
        ['blinkLeft', blinkLeftStart, blinkLeftUntil],
        ['blinkRight', blinkRightStart, blinkRightUntil],
      ]) {
        if (has(name)) own[name] = lid(start, until);
      }
      for (const e of EMOTIONS) own[e] = e === 'relaxed' ? Math.max(smile, feel.relaxed) : feel[e];
      for (const e in own) em?.setValue(e, 0);
      animate();
      for (const e in own) em?.setValue(e, Math.max(em?.getValue(e) ?? 0, own[e]));

      // On top of the animation, for this frame only: tilt while thinking, and small nods
      // with the voice for models whose mouth can't move.
      tilt += ((state === 'thinking' ? 0.12 : 0) - tilt) * Math.min(1, dt * 3);
      neckPose.copy(neck.quaternion);
      neck.rotation.z += tilt;
      neck.rotation.x += mouth * 0.04;
    },
    undo() { neck.quaternion.copy(neckPose); }, // so it never accumulates, even if no clip moves the neck
    dispose() { scene.remove(gaze); }, // another model took her place
  };
}
