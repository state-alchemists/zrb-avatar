// Her face, frame by frame: lip sync, blinking, eye contact, smiles, zrb's emotions, and the head
// tilting while thinking and nodding with the voice; on top of whatever the clip does.
import * as THREE from 'three';

const EMOTIONS = ['happy', 'sad', 'angry', 'surprised', 'relaxed']; // VRM preset expressions

export function createExpression(vrm, camera, scene) {
  const em = vrm.expressionManager;
  const neck = vrm.humanoid.getNormalizedBoneNode('neck') ?? vrm.humanoid.getNormalizedBoneNode('head'); // neck is optional in VRM
  const neckPose = new THREE.Quaternion(), side = new THREE.Vector3(), lift = new THREE.Vector3();
  let t = 0;
  let mouthQueue = [], mouthStep = 0.04, mouthClock = 0, mouth = 0;
  let nextBlink = 2, blinkUntil = 0, tilt = 0;
  let nextSmile = 12, smileUntil = 0, smile = 0;
  const feel = Object.fromEntries(EMOTIONS.map(e => [e, 0]));
  let emotion = null, emotionUntil = 0;
  // Her eyes: on you (the camera), but alive. Eyes jump rather than glide: small jumps around your
  // face every second or few, and now and then a glance away, which a blink often goes with.
  // Thinking looks away, mostly up and to the side; speaking holds your eyes more.
  const gaze = new THREE.Object3D(), gazeAt = new THREE.Vector2();
  let nextGaze = 1;
  scene.add(gaze);
  if (vrm.lookAt) vrm.lookAt.target = gaze;

  return {
    get speaking() { return mouthQueue.length > 0; },
    get emotion() { return emotion && t < emotionUntil ? emotion : null; },
    speak(values, step) { mouthQueue.push(...values); mouthStep = step; },
    hush() { mouthQueue = []; },
    smileSoon() { smileUntil = t + 2; }, // done with a task
    show(value) { emotion = value; emotionUntil = t + 6; },

    // This frame's face and head, with animate() moving the body; undo() after rendering.
    pose(dt, state, animate) {
      t += dt;
      // Mouth: play queued loudness at real speed, smoothed.
      mouthClock += dt;
      while (mouthClock >= mouthStep && mouthQueue.length) { mouthClock -= mouthStep; mouthQueue.shift(); }
      if (!mouthQueue.length) mouthClock = 0;
      mouth += ((mouthQueue[0] ?? 0) - mouth) * Math.min(1, dt * 25);

      if (t > nextBlink) { blinkUntil = t + 0.12; nextBlink = t + 2 + Math.random() * 4; }

      // Gaze: a point beside the camera, in the camera's own right/up directions (metres).
      if (t > nextGaze) {
        const away = Math.random() < ({ thinking: 0.55, speaking: 0.08 }[state] ?? 0.18);
        if (away) {
          gazeAt.set((Math.random() < 0.5 ? -1 : 1) * (0.4 + Math.random() * 0.6),
            state === 'thinking' ? 0.3 + Math.random() * 0.4 : (Math.random() - 0.6) * 0.6);
          if (Math.random() < 0.4 && t > blinkUntil + 0.5) blinkUntil = t + 0.12;
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
      for (const e of EMOTIONS) feel[e] += ((e === emotion && t < emotionUntil ? 0.6 : 0) - feel[e]) * Math.min(1, dt * 3);
      // The face is the larger of ours (blinking, lip sync, the smile, zrb's emotion) and the clip's
      // own (a laugh, a frown): zero what a clip may animate, let it, then take the larger of the two.
      const own = { blink: t < blinkUntil ? 1 : 0, aa: mouth };
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
