// Procedural geometry and textures for the realistic 3D view (no asset downloads,
// so the demo works offline).
import {
  CanvasTexture,
  CylinderGeometry,
  ExtrudeGeometry,
  LatheGeometry,
  Quaternion,
  RepeatWrapping,
  Shape,
  ShapeGeometry,
  SRGBColorSpace,
  Vector2,
  Vector3,
} from 'three';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';

const UP = new Vector3(0, 1, 0);

/** A cylinder between two points. */
function strut(a, b, r, sides = 6) {
  const dir = new Vector3().subVectors(b, a);
  const g = new CylinderGeometry(r, r, dir.length(), sides, 1, true);
  g.applyQuaternion(new Quaternion().setFromUnitVectors(UP, dir.normalize()));
  const mid = a.clone().add(b).multiplyScalar(0.5);
  g.translate(mid.x, mid.y, mid.z);
  return g;
}

const latticeCache = new Map();

/**
 * Three-legged tapered lattice mast of height h (meters), like a self-supporting
 * cell tower: legs, horizontal rings and zig-zag diagonal bracing on each face.
 * Returns { geometry, topRadius }.
 */
export function latticeTower(h) {
  const key = h.toFixed(2);
  if (latticeCache.has(key)) return latticeCache.get(key);

  const rb = Math.max(0.1, h * 0.11); // base radius
  const rt = rb * 0.32; // top radius
  const legR = Math.max(0.01, h * 0.009);
  const braceR = legR * 0.5;
  const levels = Math.min(14, Math.max(4, Math.round(h / 0.22)));
  const leg = (i, t) => {
    const r = rb + (rt - rb) * t;
    const a = (i * 2 * Math.PI) / 3 + Math.PI / 2;
    return new Vector3(r * Math.cos(a), t * h, r * Math.sin(a));
  };

  const parts = [];
  for (let i = 0; i < 3; i++) parts.push(strut(leg(i, 0), leg(i, 1), legR, 8));
  for (let l = 0; l < levels; l++) {
    const t0 = l / levels;
    const t1 = (l + 1) / levels;
    for (let i = 0; i < 3; i++) {
      const j = (i + 1) % 3;
      parts.push(strut(leg(i, t1), leg(j, t1), braceR));
      parts.push(l % 2 ? strut(leg(i, t0), leg(j, t1), braceR) : strut(leg(j, t0), leg(i, t1), braceR));
    }
  }
  const result = { geometry: mergeGeometries(parts), topRadius: rt };
  latticeCache.set(key, result);
  return result;
}

let planeParts;

/**
 * A generic light low-wing aircraft, nose along +X, about 1.2 m wingspan.
 * Returns geometries: fuselage, wing, hstab, fin, canopy, spinner, blade.
 */
export function aircraftParts() {
  if (planeParts) return planeParts;

  // Fuselage: lathe around Y then lay it along X. Profile = (radius, length) from tail to nose.
  const prof = [
    [0.0, -0.62],
    [0.025, -0.6],
    [0.045, -0.45],
    [0.07, -0.2],
    [0.085, 0.05],
    [0.088, 0.2],
    [0.08, 0.32],
    [0.06, 0.4],
    [0.0, 0.42],
  ].map(([r, y]) => new Vector2(r, y));
  const fuselage = new LatheGeometry(prof, 24);
  fuselage.rotateZ(-Math.PI / 2);

  // Wing planform in the XZ plane: tapered with slight sweep, then extruded thin.
  const wingShape = new Shape();
  const span = 0.62; // half span
  wingShape.moveTo(0.12, 0); // leading edge at root
  wingShape.lineTo(0.06, span); // leading edge at tip
  wingShape.lineTo(-0.04, span); // trailing edge at tip
  wingShape.lineTo(-0.1, 0); // trailing edge at root
  wingShape.lineTo(-0.04, -span);
  wingShape.lineTo(0.06, -span);
  wingShape.closePath();
  const wing = new ExtrudeGeometry(wingShape, {
    depth: 0.014,
    bevelEnabled: true,
    bevelThickness: 0.006,
    bevelSize: 0.006,
    bevelSegments: 2,
  });
  wing.rotateX(Math.PI / 2); // shape XY -> XZ
  wing.translate(0, 0.007, 0);

  const hShape = new Shape();
  hShape.moveTo(0.05, 0);
  hShape.lineTo(0.0, 0.2);
  hShape.lineTo(-0.06, 0.2);
  hShape.lineTo(-0.08, 0);
  hShape.lineTo(-0.06, -0.2);
  hShape.lineTo(0.0, -0.2);
  hShape.closePath();
  const hstab = new ExtrudeGeometry(hShape, { depth: 0.008, bevelEnabled: true, bevelThickness: 0.003, bevelSize: 0.003, bevelSegments: 1 });
  hstab.rotateX(Math.PI / 2);

  const finShape = new Shape();
  finShape.moveTo(0.06, 0);
  finShape.lineTo(-0.04, 0.2);
  finShape.lineTo(-0.1, 0.2);
  finShape.lineTo(-0.1, 0);
  finShape.closePath();
  const fin = new ExtrudeGeometry(finShape, { depth: 0.008, bevelEnabled: true, bevelThickness: 0.003, bevelSize: 0.003, bevelSegments: 1 });
  fin.translate(0, 0, -0.004);

  const canopy = new LatheGeometry(
    [
      [0.0, -0.12],
      [0.05, -0.08],
      [0.065, 0.0],
      [0.05, 0.08],
      [0.0, 0.12],
    ].map(([r, y]) => new Vector2(r, y)),
    20,
  );
  canopy.rotateZ(-Math.PI / 2);
  canopy.scale(1, 0.8, 0.85);

  const spinner = new LatheGeometry(
    [
      [0.0, 0.0],
      [0.035, 0.0],
      [0.03, 0.03],
      [0.0, 0.07],
    ].map(([r, y]) => new Vector2(r, y)),
    16,
  );
  spinner.rotateZ(-Math.PI / 2);

  const blade = new CylinderGeometry(0.012, 0.006, 0.34, 6);
  blade.scale(1, 1, 0.35);

  planeParts = { fuselage, wing, hstab, fin, canopy, spinner, blade };
  return planeParts;
}

/** Flat arrow (nose along +X) for the plane on the 2D map, lying on the ground plane. */
export function chevronGeometry(scale = 1) {
  const s = new Shape();
  s.moveTo(0.5 * scale, 0);
  s.lineTo(-0.32 * scale, 0.32 * scale);
  s.lineTo(-0.14 * scale, 0);
  s.lineTo(-0.32 * scale, -0.32 * scale);
  s.closePath();
  const g = new ShapeGeometry(s);
  g.rotateX(-Math.PI / 2); // XY shape -> lies flat in XZ
  return g;
}

/** Tiling grass texture drawn on a canvas. */
export function grassTexture(tileMeters = 4, areaMeters = 400) {
  const size = 512;
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d');
  g.fillStyle = '#4a6a2a';
  g.fillRect(0, 0, size, size);
  // Soft patches of light/dark.
  for (let i = 0; i < 70; i++) {
    const x = Math.random() * size;
    const y = Math.random() * size;
    const r = 30 + Math.random() * 90;
    const grad = g.createRadialGradient(x, y, 0, x, y, r);
    const light = Math.random() > 0.5;
    grad.addColorStop(0, light ? 'rgba(140,155,95,0.18)' : 'rgba(40,50,25,0.18)');
    grad.addColorStop(1, 'rgba(0,0,0,0)');
    g.fillStyle = grad;
    // wrap around the edges so the tile is seamless
    for (const dx of [-size, 0, size]) for (const dy of [-size, 0, size]) {
      g.save();
      g.translate(dx, dy);
      g.fillRect(x - r, y - r, r * 2, r * 2);
      g.restore();
    }
  }
  // Blade speckle.
  for (let i = 0; i < 26000; i++) {
    const v = Math.random();
    g.fillStyle = v > 0.5 ? `rgba(160,175,110,${0.15 + Math.random() * 0.2})` : `rgba(30,40,20,${0.15 + Math.random() * 0.2})`;
    g.fillRect(Math.random() * size, Math.random() * size, 1, 1 + Math.random() * 2);
  }
  const tex = new CanvasTexture(c);
  tex.wrapS = tex.wrapT = RepeatWrapping;
  tex.repeat.set(areaMeters / tileMeters, areaMeters / tileMeters);
  tex.colorSpace = SRGBColorSpace;
  tex.anisotropy = 8;
  return tex;
}
