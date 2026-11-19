// Tests for the lattice leaf's concentric-cylinder pin stack (analytic layer entry).
//   npm run test:unit
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  pinStack, pinStackEntry, packLatticeGrid, buildLatticeGrid, LAT_TEXELS,
  type RegionLike, type SurfaceLike, type PinStack, type Vec3,
} from '../src/lib/components/geometry/latticeGrid.ts';

// A PWR-like FuelPin as the backend expands it (templates.py _build_fuel_pin_cells):
//   layer 0 = Inside(c0) ; layer i = Outside(c[i-1]) Inside(c[i]) ; all + Outside(bot) Inside(top)
const RADII = [0.4096, 0.4178, 0.4750];
function makePin(cx = 1.5, cy = -2, zb = 10, zt = 375.76, radii = RADII) {
  const surfaces = new Map<string, SurfaceLike>();
  radii.forEach((r, i) => surfaces.set(`c${i}`, { type: 'cylinder_z', params: { x0: cx, y0: cy, r } }));
  surfaces.set('bot', { type: 'plane_z', params: { z: zb } });
  surfaces.set('top', { type: 'plane_z', params: { z: zt } });
  const axial: RegionLike[] = [{ op: 'outside', surface: 'bot' }, { op: 'inside', surface: 'top' }];
  const layers: RegionLike[] = radii.map((_, i) => ({
    op: 'and',
    items: [
      ...(i === 0 ? [] : [{ op: 'outside', surface: `c${i - 1}` } as RegionLike]),
      { op: 'inside', surface: `c${i}` } as RegionLike,
      ...axial,
    ],
  }));
  return { surfaces, layers, get: (id: string) => surfaces.get(id) };
}

// Reference: dense march of the layer's region predicate. Returns the first t in [wLo, wHi) where
// the ray is inside the layer, refined by bisection. Independent of the analytic code.
function insideLayer(st: PinStack, m: number, p: Vec3): boolean {
  const rho = Math.hypot(p[0] - st.cx, p[1] - st.cy);
  const rin = m === 0 ? -1 : st.radii[m - 1];
  return rho <= st.radii[m] && rho > rin && p[2] > st.zLo && p[2] <= st.zHi;
}
function marchEntry(st: PinStack, m: number, ro: Vec3, rd: Vec3, wLo: number, wHi: number, step: number): number {
  const at = (t: number): Vec3 => [ro[0] + rd[0] * t, ro[1] + rd[1] * t, ro[2] + rd[2] * t];
  if (insideLayer(st, m, at(wLo))) return wLo;
  let prev = wLo;
  for (let t = wLo + step; t < wHi; t += step) {
    if (insideLayer(st, m, at(t))) {
      let lo = prev, hi = t;
      for (let i = 0; i < 60; i++) { const mid = (lo + hi) / 2; if (insideLayer(st, m, at(mid))) hi = mid; else lo = mid; }
      return hi;
    }
    prev = t;
  }
  return Infinity;
}

test('pinStack reads a FuelPin as concentric layers', () => {
  const { layers, get } = makePin();
  const st = pinStack(layers, get);
  assert.ok(st);
  assert.deepEqual(st.radii, RADII);
  assert.equal(st.cx, 1.5); assert.equal(st.cy, -2);
  assert.equal(st.zLo, 10); assert.equal(st.zHi, 375.76);
});

test('pinStack rejects anything it cannot reproduce exactly', () => {
  const base = makePin();
  const clone = () => base.layers.map((l) => structuredClone(l)) as RegionLike[];
  // extra term
  let ls = clone(); (ls[1] as any).items.push({ op: 'inside', surface: 'c2' });
  assert.equal(pinStack(ls, base.get), null);
  // layers that do not chain (layer 2 cuts out c0 instead of c1)
  ls = clone(); (ls[2] as any).items[0] = { op: 'outside', surface: 'c0' };
  assert.equal(pinStack(ls, base.get), null);
  // an OR / NOT anywhere
  ls = clone(); (ls[0] as any).items[0] = { op: 'not', item: { op: 'inside', surface: 'c0' } };
  assert.equal(pinStack(ls, base.get), null);
  // off-axis cylinder
  const off = makePin(); off.surfaces.set('c1', { type: 'cylinder_z', params: { x0: 1.6, y0: -2, r: RADII[1] } });
  assert.equal(pinStack(off.layers, off.get), null);
  // axial planes that differ between layers
  const ax = makePin(); const l2 = ax.layers[2] as any;
  ax.surfaces.set('top2', { type: 'plane_z', params: { z: 300 } });
  l2.items[l2.items.length - 1] = { op: 'inside', surface: 'top2' };
  assert.equal(pinStack(ax.layers, ax.get), null);
  // non-increasing radii
  const rr = makePin(1.5, -2, 10, 375.76, [0.4, 0.4, 0.5]);
  assert.equal(pinStack(rr.layers, rr.get), null);
  // too many layers for the two radius texels
  const many = makePin(0, 0, 0, 1, [1, 2, 3, 4, 5, 6, 7, 8, 9]);
  assert.equal(pinStack(many.layers, many.get), null);
  assert.equal(pinStack([], base.get), null);
});

test('pinStackEntry matches a brute-force march (random rays, incl. camera inside)', () => {
  const { layers, get } = makePin();
  const st = pinStack(layers, get)!;
  // deterministic PRNG
  let seed = 12345;
  const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);
  const sliver = () => 1e-6;
  let hits = 0, checked = 0;
  for (let n = 0; n < 4000; n++) {
    // rays aimed at the pin from a box around it, any direction toward its volume
    const tgt: Vec3 = [st.cx + (rnd() - 0.5) * 1.2, st.cy + (rnd() - 0.5) * 1.2, st.zLo + rnd() * (st.zHi - st.zLo)];
    let d: Vec3 = [rnd() - 0.5, rnd() - 0.5, (rnd() - 0.5) * (rnd() < 0.5 ? 0.05 : 1)];
    const len = Math.hypot(...d); d = [d[0] / len, d[1] / len, d[2] / len];
    const back = 1 + rnd() * 5;
    const ro: Vec3 = [tgt[0] - d[0] * back, tgt[1] - d[1] * back, tgt[2] - d[2] * back];
    const wLo = rnd() < 0.2 ? back : 0, wHi = back * 2 + 2;   // sometimes start the window inside the pin
    for (let m = 0; m < st.radii.length; m++) {
      const a = pinStackEntry(st, m, ro, d, wLo, wHi, sliver);
      const b = marchEntry(st, m, ro, d, wLo, wHi, 2e-4);
      checked++;
      if (b === Infinity) {
        // the march can miss a layer thinner than its step (the 0.0082 gap at grazing angles);
        // the analytic answer may then be finite but must be a real entry.
        if (a !== Infinity) {
          const p: Vec3 = [ro[0] + d[0] * (a + 1e-7), ro[1] + d[1] * (a + 1e-7), ro[2] + d[2] * (a + 1e-7)];
          assert.ok(insideLayer(st, m, p) || a === wLo, `analytic hit at t=${a} is not inside layer ${m}`);
        }
      } else {
        hits++;
        assert.ok(Math.abs(a - b) < 5e-4, `layer ${m}: analytic ${a} vs march ${b}`);
      }
    }
  }
  assert.ok(hits > 500, `too few rays hit anything to mean much (${hits}/${checked})`);
});

test('pinStackEntry: straight-down ray through the axis enters pellet at the top, gap/clad as thin shells', () => {
  const { layers, get } = makePin(0, 0, 0, 100);
  const st = pinStack(layers, get)!;
  const ro: Vec3 = [0.01, 0, 150], rd: Vec3 = [0, 0, -1];
  // Entry into the pellet is the top cap; the gap and clad are annuli and not hit by an axial ray at rho=0.01.
  assert.ok(Math.abs(pinStackEntry(st, 0, ro, rd, 0, 1000) - 50) < 1e-9);
  assert.equal(pinStackEntry(st, 1, ro, rd, 0, 1000), Infinity);
  assert.equal(pinStackEntry(st, 2, ro, rd, 0, 1000), Infinity);
  // The same ray at rho = 0.45 lands in the clad annulus (0.4178 < rho <= 0.475).
  const ro2: Vec3 = [0.45, 0, 150];
  assert.ok(Math.abs(pinStackEntry(st, 2, ro2, rd, 0, 1000) - 50) < 1e-9);
});

test('pinStackEntry: horizontal ray crosses clad, gap, pellet in order', () => {
  const { layers, get } = makePin(0, 0, 0, 100);
  const st = pinStack(layers, get)!;
  const ro: Vec3 = [-2, 0, 50], rd: Vec3 = [1, 0, 0];
  const t2 = pinStackEntry(st, 2, ro, rd, 0, 10);
  const t1 = pinStackEntry(st, 1, ro, rd, 0, 10);
  const t0 = pinStackEntry(st, 0, ro, rd, 0, 10);
  assert.ok(Math.abs(t2 - (2 - 0.4750)) < 1e-9);
  assert.ok(Math.abs(t1 - (2 - 0.4178)) < 1e-9);
  assert.ok(Math.abs(t0 - (2 - 0.4096)) < 1e-9);
  assert.ok(t2 < t1 && t1 < t0);
  // from the other side the clad's far wall is entered after passing back through the pellet
  const back = pinStackEntry(st, 2, [2, 0, 50], [-1, 0, 0], 0, 10);
  assert.ok(Math.abs(back - (2 - 0.4750)) < 1e-9);
});

test('pinStackEntry: slivers thinner than the pixel are skipped, but a ray starting inside still hits', () => {
  const { layers, get } = makePin(0, 0, 0, 100);
  const st = pinStack(layers, get)!;
  const ro: Vec3 = [-2, 0, 50], rd: Vec3 = [1, 0, 0];
  const gapWidth = 0.4178 - 0.4096;
  assert.ok(pinStackEntry(st, 1, ro, rd, 0, 10, () => 0.5 * gapWidth) < Infinity);
  assert.equal(pinStackEntry(st, 1, ro, rd, 0, 10, () => 2 * gapWidth), Infinity);
  // window starts inside the gap: hit at the window start regardless of the sliver size
  assert.equal(pinStackEntry(st, 1, ro, rd, 2 - 0.413, 10, () => 1), 2 - 0.413);
});

test('packLatticeGrid writes the stack into texels 7-9 and nothing when there is none', () => {
  const { layers, get } = makePin(1, 2, 3, 40);
  const stack = pinStack(layers, get)!;
  const mk = (st: PinStack | null) => buildLatticeGrid({
    origin: [1, 2, 3], instances: [[0, 0, 0], [1.26, 0, 0], [0, 1.26, 0], [1.26, 1.26, 0]],
    proto: { xMin: 0.5, xMax: 1.5, yMin: 1.5, yMax: 2.5, zMin: 3, zMax: 40 }, stack: st,
  }, { maxInnerSteps: 384, maxOuterSteps: 96 })!;
  const buf = new Float32Array(LAT_TEXELS * 4);
  packLatticeGrid(mk(stack), 5, 3, buf, 0);
  assert.deepEqual([...buf.slice(28, 32)], [1, 2, 3, 40]);
  assert.deepEqual([...buf.slice(32, 36)].map((v) => +v.toFixed(4)), [0.4096, 0.4178, 0.475, 0]);
  assert.deepEqual([...buf.slice(36, 40)], [0, 0, 0, 0]);
  // radius count must agree with nLayers, else the shader would read garbage radii
  const bad = new Float32Array(LAT_TEXELS * 4);
  packLatticeGrid(mk(stack), 5, 2, bad, 0);
  assert.deepEqual([...bad.slice(28, 40)], new Array(12).fill(0));
  const none = new Float32Array(LAT_TEXELS * 4);
  packLatticeGrid(mk(null), 5, 3, none, 0);
  assert.deepEqual([...none.slice(28, 40)], new Array(12).fill(0));
});
