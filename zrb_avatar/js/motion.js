// Which clip plays: the wanted pool (idle, thinking, tool, attention, speaking, dance) loops in
// random order, falling back to idle; an action plays once over it, then the loop goes on.
import * as THREE from 'three';

// Clips come in groups by the first part of their name (stretch-side -> stretch), or by their
// subfolder (calm/sway -> calm): a random group, then a random clip in it, so four stretches
// don't crowd out the rest.
const group = name => name.includes('/') ? name.split('/')[0] : name.split('-')[0];
const random = list => list[Math.floor(Math.random() * list.length)];

export function createMotion(mixer, clips) {
  let current = null, playing = null; // the mixer action, and the pool it came from
  let wanted = 'idle';
  const loopPool = () => clips[wanted] ? wanted : 'idle';

  function play(clip, fade = 0.6, speed = 1) {
    const next = mixer.clipAction(clip);
    next.reset().setLoop(THREE.LoopOnce, 1).play();
    next.timeScale = speed;
    next.clampWhenFinished = true;
    if (current && current !== next) next.crossFadeFrom(current, fade, true);
    current = next;
  }
  function playLoop() {
    playing = loopPool();
    const pool = clips[playing] ?? {};
    // A pool's own clip (idle/Idle, and its mirror) is where she rests: she comes back to it
    // after any other, and leaves it now and then (30%), so a stretch or a yawn stays a moment.
    const rest = Object.keys(pool).filter(n => group(n).toLowerCase() === playing);
    const resting = rest.some(n => pool[n] === current?.getClip());
    const choices = rest.length && (!resting || Math.random() < 0.7) ? rest : Object.keys(pool);
    const others = choices.filter(n => pool[n] !== current?.getClip());
    const pick = others.length ? others : choices;
    if (!pick.length) return;
    const g = random([...new Set(pick.map(group))]);
    // A little faster or slower each time, so a loop never repeats exactly; dances keep the beat.
    play(pool[random(pick.filter(n => group(n) === g))], 0.6, playing === 'dance' ? 1 : 0.9 + Math.random() * 0.2);
  }

  // After a loop or an action, the next loop — but only when the clip that finished is the one on
  // screen. A loop that an action just crossfaded over keeps running at fading weight and finishes
  // on its own schedule, and starting the next loop from that event cut the action short: the pose
  // snapped back to idle (or thinking) a second into the action.
  mixer.addEventListener('finished', ({ action }) => { if (action === current) playLoop(); });

  return {
    get clip() { return current?.getClip() ?? null; },
    get acting() { return playing === 'actions'; },
    // Switch to another loop pool, but let an action finish first.
    want(pool) {
      wanted = pool;
      if (playing !== 'actions' && playing !== loopPool()) playLoop();
    },
    act(clip) {
      play(clip, 0.3);
      playing = 'actions';
    },
  };
}
