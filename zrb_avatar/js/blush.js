// Pink cheeks: two soft patches on her face, fading in and out. Placed from her own face (where
// her blink and her "aa" move it: her eyelids and her mouth), so any model's proportions fit, and
// riding her head bone, so they follow every motion.
import * as THREE from 'three';

// Calibration knobs, for a model whose blush sits off. In eye widths (outer corner to outer corner):
// each cheek's distance from the middle, its size, and how far it floats off the skin; and how far
// down from her eyes to her mouth it sits.
const OUT = 0.3, SIZE = 0.32, LIFT = 0.01, DOWN = 0.45;

function softDisc() {
  const c = Object.assign(document.createElement('canvas'), { width: 64, height: 64 }), g = c.getContext('2d');
  const fill = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  fill.addColorStop(0, '#ff5a7a');
  fill.addColorStop(1, '#ff5a7a00');
  g.fillStyle = fill;
  g.fillRect(0, 0, 64, 64);
  return new THREE.CanvasTexture(c);
}

// Where an expression moves her mesh, as she stands now: a box around what it moves most (a
// blink barely nudges much of the face), empty if it moves nothing.
function moved(vrm, expression) {
  const box = new THREE.Box3(), v = new THREE.Vector3(), d = new THREE.Vector3(), em = vrm.expressionManager;
  if (!em?.getExpression(expression)) return box;
  const before = new Map(), moves = [];
  vrm.scene.traverse(o => { if (o.morphTargetInfluences) before.set(o, [...o.morphTargetInfluences]); });
  em.setValue(expression, 1);
  em.update();
  for (const [mesh, was] of before) {
    mesh.morphTargetInfluences.forEach((w, t) => {
      const delta = w !== was[t] && mesh.geometry.morphAttributes.position?.[t];
      for (let i = 0; delta && i < delta.count; i++) {
        const far = d.fromBufferAttribute(delta, i).length();
        if (far) moves.push([mesh, i, far]);
      }
    });
  }
  const most = Math.max(0, ...moves.map(m => m[2]));
  for (const [mesh, i, far] of moves) {
    if (far > 0.3 * most) box.expandByPoint(mesh.getVertexPosition(i, v).applyMatrix4(mesh.matrixWorld));
  }
  em.setValue(expression, 0);
  em.update();
  return box;
}

// Call it at load, while she stands in her rest pose. A model without a blink, or whose cheeks a
// ray from the front doesn't find, gets no blush rather than one in the wrong place.
export function createBlush(vrm) {
  let level = 0, want = 0;
  const off = { set() {}, update() {} };
  vrm.scene.updateMatrixWorld(true);
  const eyes = moved(vrm, 'blink'), mouth = moved(vrm, 'aa');
  if (eyes.isEmpty()) return off;
  const width = eyes.max.x - eyes.min.x, middle = eyes.getCenter(new THREE.Vector3());
  const y = mouth.isEmpty() ? eyes.min.y - DOWN * width / 2 : THREE.MathUtils.lerp(eyes.min.y, mouth.max.y, DOWN);

  const forward = new THREE.Vector3(0, 0, 1); // she faces +z (VRMUtils.rotateVRM0 sees to it)
  const meshes = [];
  vrm.scene.traverse(o => { if (o.isMesh) meshes.push(o); });
  const ray = new THREE.Raycaster(), material = new THREE.MeshBasicMaterial({
    map: softDisc(), transparent: true, opacity: 0, depthWrite: false,
    polygonOffset: true, polygonOffsetFactor: -4,
  });
  const patches = [];
  for (const side of [-1, 1]) {
    const cheek = new THREE.Vector3(middle.x + side * OUT * width, y, eyes.max.z + width);
    ray.set(cheek, forward.clone().negate());
    // The skin: the first surface about as deep as her eyelids, not a strand of hair in front.
    const hit = ray.intersectObjects(meshes, false).find(h => h.face && Math.abs(h.point.z - eyes.max.z) < width / 2);
    if (!hit) return off;
    const normal = hit.face.normal.clone().transformDirection(hit.object.matrixWorld);
    const patch = new THREE.Mesh(new THREE.PlaneGeometry(SIZE * width, SIZE * width), material);
    patch.position.copy(hit.point).addScaledVector(normal, LIFT * width);
    patch.lookAt(patch.position.clone().add(normal));
    patch.renderOrder = 1;
    patch.visible = false;
    patches.push(patch);
  }
  const head = vrm.humanoid.getRawBoneNode('head');
  for (const patch of patches) head.attach(patch); // keeps where it is, then follows the head

  return {
    set(on) { want = on ? 1 : 0; },
    update(dt) {
      level += (want - level) * Math.min(1, dt * 2);
      material.opacity = 0.55 * level;
      for (const patch of patches) patch.visible = level > 0.01;
    },
  };
}
