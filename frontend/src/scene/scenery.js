// Background scenery for the 3D view: trees, distant hills, a concrete test pad.
// Everything is generated in code (no downloads) and uses a fixed random seed,
// so the scene looks the same every time it loads.
import {
  BufferGeometry,
  CanvasTexture,
  Color,
  CylinderGeometry,
  Float32BufferAttribute,
  Group,
  IcosahedronGeometry,
  InstancedMesh,
  MeshStandardMaterial,
  Object3D,
  RepeatWrapping,
  SRGBColorSpace,
} from 'three';

/** Tiny seeded random number generator (same numbers every run). */
function seededRandom(seed) {
  let s = seed;
  return () => {
    s = (s * 16807) % 2147483647;
    return (s - 1) / 2147483646;
  };
}

/**
 * Scattered trees in a ring around the tower area.
 * keepClear: radius (m) around the center with no trees, so they never block the view.
 * Returns a three.js Group to drop into the scene.
 */
export function makeTrees(keepClear, count = 260) {
  const rand = seededRandom(7);
  const trunkGeo = new CylinderGeometry(0.12, 0.18, 1, 6);
  trunkGeo.translate(0, 0.5, 0); // base at y = 0
  const crownGeo = new IcosahedronGeometry(1, 1);
  const trunks = new InstancedMesh(trunkGeo, new MeshStandardMaterial({ color: '#5b4632', roughness: 1 }), count);
  const crowns = new InstancedMesh(crownGeo, new MeshStandardMaterial({ roughness: 0.9, flatShading: true }), count);

  const o = new Object3D();
  const c = new Color();
  for (let i = 0; i < count; i++) {
    const angle = rand() * Math.PI * 2;
    const dist = keepClear + 25 + rand() * 180; // start well back from the towers
    const x = Math.cos(angle) * dist;
    const z = Math.sin(angle) * dist;
    const h = 3 + rand() * 4; // tree height, m

    o.position.set(x, 0, z);
    o.rotation.set(0, rand() * Math.PI, 0);
    o.scale.set(1, h * 0.45, 1);
    o.updateMatrix();
    trunks.setMatrixAt(i, o.matrix);

    const r = h * (0.28 + rand() * 0.12); // crown radius
    o.position.set(x, h * 0.45 + r * 0.8, z);
    o.scale.set(r, r * (1.1 + rand() * 0.5), r);
    o.updateMatrix();
    crowns.setMatrixAt(i, o.matrix);
    crowns.setColorAt(i, c.setHSL(0.25 + rand() * 0.07, 0.35 + rand() * 0.2, 0.22 + rand() * 0.1));
  }
  for (const m of [trunks, crowns]) {
    m.castShadow = true;
    m.receiveShadow = true;
  }
  const group = new Group();
  group.add(trunks, crowns);
  return group;
}

/** A ring of low rolling hills on the horizon. */
export function makeHills(radius = 750) {
  const N = 180;
  const pos = [];
  const idx = [];
  for (let i = 0; i <= N; i++) {
    const a = (i / N) * Math.PI * 2;
    const h = 28 + 18 * Math.sin(3 * a) + 11 * Math.sin(7 * a + 1) + 6 * Math.sin(17 * a + 2); // bumpy skyline
    const x = Math.cos(a) * radius;
    const z = Math.sin(a) * radius;
    pos.push(x, -2, z, x * 1.03, h, z * 1.03); // bottom vertex, top vertex
    if (i < N) {
      const b = i * 2;
      idx.push(b, b + 2, b + 1, b + 1, b + 2, b + 3);
    }
  }
  const geo = new BufferGeometry();
  geo.setAttribute('position', new Float32BufferAttribute(pos, 3));
  geo.setIndex(idx);
  geo.computeVertexNormals();
  return geo;
}

/** Concrete texture for the test pad (mid grey with speckles and faint slab joints). */
export function concreteTexture(sizeMeters) {
  const size = 512;
  const cv = document.createElement('canvas');
  cv.width = cv.height = size;
  const g = cv.getContext('2d');
  g.fillStyle = '#6f706c';
  g.fillRect(0, 0, size, size);
  const rand = seededRandom(3);
  for (let i = 0; i < 30000; i++) {
    const v = 80 + rand() * 60;
    g.fillStyle = `rgba(${v},${v},${v - 4},0.3)`;
    g.fillRect(rand() * size, rand() * size, 1.5, 1.5);
  }
  g.strokeStyle = 'rgba(60,60,58,0.35)'; // slab joint on the tile edge
  g.lineWidth = 3;
  g.strokeRect(0, 0, size, size);
  const tex = new CanvasTexture(cv);
  tex.wrapS = tex.wrapT = RepeatWrapping;
  tex.repeat.set(sizeMeters / 3, sizeMeters / 3); // one slab per 3 m
  tex.colorSpace = SRGBColorSpace;
  tex.anisotropy = 8;
  return tex;
}
