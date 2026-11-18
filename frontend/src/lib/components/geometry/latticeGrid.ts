// Regular-grid analysis of a lattice for the viewport's "lattice leaf" fast path.
//
// WHY THIS EXISTS
//   Drawing a lattice used to create one BVH leaf per pin. Per-ray cost then grew with the
//   number of pins (BVH walk + one exact intersection per pin box the ray touched), and the
//   instance texture capped the scene at MAX_INSTANCES pins. A rectangular lattice is a
//   regular grid, so the shader can instead walk the pitch cells a ray crosses (a 2D DDA)
//   and test only the pin in each: the cost depends on cells crossed, not on pin count.
//   The whole lattice is ONE BVH leaf.
//
// WHAT QUALIFIES
//   A lattice whose pins (and, for nested assemblies, whose assemblies) sit on a full,
//   evenly spaced xy grid at one common z, where each pin fits inside its own pitch cell
//   (so "the ray is in cell (i, j)" really means "only pin (i, j) can be hit"), and each
//   assembly's pin grid fits inside its own assembly cell. Anything else (hex lattices,
//   a missing pin, overlapping pins, an old payload without `origin`) returns null and the
//   caller keeps using per-pin instances, exactly as before.
//
// COORDINATE CONVENTION (matches the instance path)
//   The prototype is the FIRST pin, expressed at its own world position (`origin`). Pin
//   offsets are relative to it: pin world frame = prototype frame translated by `offset`.
//   offset(a, b, i, j) = asmOff0 + (a, b) * outerPitch + (i, j) * innerPitch   (xy)
//                        offZ                                                  (z)
//   where (a, b) indexes assemblies (always 0 for a flat lattice) and (i, j) pins.
//
// RULE FOR FUTURE EDITORS: the packed layout below is mirrored by latA()/latticeVisit() in
// CsgViewportPanel.svelte's fragment shader. Change both together.

export type Vec3 = [number, number, number];
export interface Box { xMin: number; xMax: number; yMin: number; yMax: number; zMin: number; zMax: number }

/** Texels per lattice in the lattice table (see packLatticeGrid). */
export const LAT_TEXELS = 7;

export interface AxisGrid { off: number; pitch: number; count: number }
export interface RegularGrid { x: AxisGrid; y: AxisGrid; z: number }

/** Evenly spaced distinct values -> {off (lowest), pitch, count}; null if not a clean grid. */
function analyzeAxis(values: number[], tol: number): AxisGrid | null {
  const sorted = [...values].sort((a, b) => a - b);
  const uniq: number[] = [];
  for (const v of sorted) if (uniq.length === 0 || v - uniq[uniq.length - 1] > tol) uniq.push(v);
  if (uniq.length === 1) return { off: uniq[0], pitch: 0, count: 1 };
  const pitch = (uniq[uniq.length - 1] - uniq[0]) / (uniq.length - 1);
  if (!(pitch > tol)) return null;
  for (let i = 0; i < uniq.length; i++) {
    if (Math.abs(uniq[i] - (uniq[0] + i * pitch)) > Math.max(tol, 1e-6 * pitch)) return null;
  }
  return { off: uniq[0], pitch, count: uniq.length };
}

/** A full nx*ny grid of points at one z, every grid slot occupied exactly once. */
export function detectRegularGrid(points: Vec3[], tol = 1e-7): RegularGrid | null {
  if (points.length === 0) return null;
  if (points.some((p) => !p.every(Number.isFinite))) return null;
  const x = analyzeAxis(points.map((p) => p[0]), tol);
  const y = analyzeAxis(points.map((p) => p[1]), tol);
  if (!x || !y || x.count * y.count !== points.length) return null;
  const z = points[0][2];
  if (points.some((p) => Math.abs(p[2] - z) > tol)) return null;
  const seen = new Set<number>();
  for (const p of points) {
    const i = x.count === 1 ? 0 : Math.round((p[0] - x.off) / x.pitch);
    const j = y.count === 1 ? 0 : Math.round((p[1] - y.off) / y.pitch);
    const key = i * y.count + j;
    if (seen.has(key)) return null;
    seen.add(key);
  }
  return { x, y, z };
}

// A region as the viewer receives it (only the parts this module reads).
export type RegionLike =
  | { op: 'inside' | 'outside'; surface: string }
  | { op: 'and' | 'or'; items: RegionLike[] }
  | { op: 'not'; item: RegionLike };
export interface SurfaceLike { type: string; params: Record<string, number> }

/**
 * Conservative xy circle around a pin: (cx, cy, r), or null when none can be proven.
 *
 * It lets the shader reject a ray that only clips the CORNER of a pin's box (the big miss cost
 * in a lattice: such a ray touches many boxes and used to run three exact tests in each) and
 * clip the rest to the circle's chord. Provable only when every layer is the AND of, among other
 * things, an Inside(cylinder_z) and all those cylinders share one axis; the layer is then inside
 * its tightest such cylinder, so the pin is inside the widest one. Anything else (a layer with
 * no cylinder, a cylinder off-axis, ...) returns null and the shader skips the test.
 */
export function pinEnvelope(layers: RegionLike[], surface: (id: string) => SurfaceLike | undefined): [number, number, number] | null {
  let cx = NaN, cy = NaN, rMax = 0;
  for (const region of layers) {
    const terms = region.op === 'and' ? region.items : [region];
    let tightest = Infinity;
    for (const t of terms) {
      if (t.op !== 'inside') continue;
      const s = surface(t.surface);
      if (!s || s.type !== 'cylinder_z') continue;
      const { x0, y0, r } = s.params;
      if (![x0, y0, r].every(Number.isFinite) || !(r > 0)) return null;
      if (Number.isNaN(cx)) { cx = x0; cy = y0; }
      else if (Math.abs(x0 - cx) > 1e-9 || Math.abs(y0 - cy) > 1e-9) return null;   // off-axis: no single circle
      tightest = Math.min(tightest, r);
    }
    if (!Number.isFinite(tightest)) return null;   // this layer has no cylinder bounding it
    rMax = Math.max(rMax, tightest);
  }
  return layers.length > 0 && rMax > 0 ? [cx, cy, rMax] : null;
}

export interface LatticeGridInput {
  /** World position of the prototype pin. null/undefined -> not usable. */
  origin: Vec3 | null | undefined;
  /** Outer offsets (pins for a flat lattice, assemblies for a nested one), relative to origin. */
  instances: Vec3[];
  /** Pin offsets within an assembly; non-empty = nested lattice. */
  innerOffsets?: Vec3[];
  /** Union box of the prototype's layers, in the prototype's own (world) frame, padded. Becomes the BVH leaf box. */
  proto: Box;
  /**
   * Exact (unpadded) union box of the same layers, used to decide whether a pin fits inside its
   * pitch cell. Defaults to `proto`. Pass it whenever you can: `proto` carries a scene-relative
   * safety margin that can exceed the gap between tightly pitched pins.
   */
  fit?: Box;
  /** From pinEnvelope(); null/undefined = no circle test in the shader. */
  envelope?: [number, number, number] | null;
}

export interface LatticeGridLimits { maxInnerSteps: number; maxOuterSteps: number }

export interface LatticeGrid {
  outerLo: [number, number];      // lower xy corner of assembly cell (0, 0)
  outerPitch: [number, number];
  outerCount: [number, number];
  innerPitch: [number, number];
  innerCount: [number, number];
  pinLo0: [number, number];       // lower xy corner of pin cell (0, 0) inside assembly (0, 0)
  asmOff0: [number, number];      // offset (vs the prototype) of pin (0, 0) in assembly (0, 0)
  offZ: number;
  pinCount: number;
  /** World AABB holding every pin of every layer: the BVH leaf box. */
  box: Box;
  /** One pin's EXACT box in the prototype's own frame (translate by the pin's offset). The shader
   *  clips each cell's depth window to it (plus a tiny margin). Deliberately not the padded BVH
   *  box: that pad is 2% of the pin's height (~7 cm on a 366 cm pin), and a shallow ray skimming
   *  the pin tops would otherwise pay for an exact test in every pin it crosses in that empty slab. */
  pinBox: Box;
  /** Pin envelope circle (cx, cy, r) in the prototype's frame; r = 0 disables the test. */
  env: [number, number, number];
}

const FIT_TOL = 1e-9;

/** null when the lattice does not qualify (the caller falls back to per-pin instances). */
export function buildLatticeGrid(inp: LatticeGridInput, limits: LatticeGridLimits): LatticeGrid | null {
  const o = inp.origin;
  if (!o || o.length !== 3 || !o.every(Number.isFinite)) return null;
  const nested = (inp.innerOffsets?.length ?? 0) > 0;
  const innerG = detectRegularGrid(nested ? inp.innerOffsets! : inp.instances);
  if (!innerG) return null;
  const outerG = nested ? detectRegularGrid(inp.instances) : null;
  if (nested && !outerG) return null;

  const pb = inp.proto;
  if (![pb.xMin, pb.xMax, pb.yMin, pb.yMax, pb.zMin, pb.zMax].every(Number.isFinite)) return null;
  const b = inp.fit ?? pb;   // extent used for the pin-fits-its-cell checks
  if (![b.xMin, b.xMax, b.yMin, b.yMax].every(Number.isFinite)) return null;

  // Inner pitch cell. On an axis with a single pin the cell is just wide enough for the pin.
  const ip: [number, number] = [innerG.x.pitch, innerG.y.pitch];
  const ext = [Math.max(o[0] - b.xMin, b.xMax - o[0]), Math.max(o[1] - b.yMin, b.yMax - o[1])];
  const inCount: [number, number] = [innerG.x.count, innerG.y.count];
  for (let k = 0; k < 2; k++) {
    if (inCount[k] === 1) ip[k] = Math.max(2 * ext[k], 1e-6);
    else if (2 * ext[k] > ip[k] + FIT_TOL) return null;   // pins overlap their neighbours' cells
  }
  // Each pin must sit inside ITS cell: check the (possibly asymmetric) box, not just the half-extent.
  if (b.xMin < o[0] - ip[0] / 2 - FIT_TOL || b.xMax > o[0] + ip[0] / 2 + FIT_TOL ||
      b.yMin < o[1] - ip[1] / 2 - FIT_TOL || b.yMax > o[1] + ip[1] / 2 + FIT_TOL) return null;

  const span: [number, number] = [inCount[0] * ip[0], inCount[1] * ip[1]];
  const outCount: [number, number] = nested ? [outerG!.x.count, outerG!.y.count] : [1, 1];
  const op: [number, number] = [span[0], span[1]];
  if (nested) {
    const og = [outerG!.x, outerG!.y];
    for (let k = 0; k < 2; k++) {
      if (og[k].count > 1) {
        if (span[k] > og[k].pitch + FIT_TOL) return null;  // assemblies would overlap
        op[k] = og[k].pitch;
      }
    }
  }

  if (inCount[0] + inCount[1] + 2 > limits.maxInnerSteps) return null;
  if (outCount[0] + outCount[1] + 2 > limits.maxOuterSteps) return null;

  const outerOff0: [number, number] = nested ? [outerG!.x.off, outerG!.y.off] : [0, 0];
  const asmOff0: [number, number] = [outerOff0[0] + innerG.x.off, outerOff0[1] + innerG.y.off];
  const pinLo0: [number, number] = [o[0] + asmOff0[0] - ip[0] / 2, o[1] + asmOff0[1] - ip[1] / 2];
  const outerLo: [number, number] = [pinLo0[0] + span[0] / 2 - op[0] / 2, pinLo0[1] + span[1] / 2 - op[1] / 2];
  const offZ = innerG.z + (nested ? outerG!.z : 0);

  const maxOff = [
    asmOff0[0] + (outCount[0] - 1) * op[0] + (inCount[0] - 1) * ip[0],
    asmOff0[1] + (outCount[1] - 1) * op[1] + (inCount[1] - 1) * ip[1],
  ];
  return {
    outerLo, outerPitch: op, outerCount: outCount,
    innerPitch: ip, innerCount: inCount,
    pinLo0, asmOff0, offZ,
    pinCount: outCount[0] * outCount[1] * inCount[0] * inCount[1],
    pinBox: { ...b },
    env: inp.envelope && inp.envelope[2] > 0 && inp.envelope.every(Number.isFinite) ? inp.envelope : [0, 0, 0],
    box: {
      xMin: pb.xMin + asmOff0[0], xMax: pb.xMax + maxOff[0],
      yMin: pb.yMin + asmOff0[1], yMax: pb.yMax + maxOff[1],
      zMin: pb.zMin + offZ, zMax: pb.zMax + offZ,
    },
  };
}

/**
 * Write one lattice into the lattice table at row `index` (LAT_TEXELS texels, RGBA each):
 *   0: outerLo.xy, outerPitch.xy      1: outerCount.xy, innerPitch.xy
 *   2: innerCount.xy, baseProto, nLayers   3: pinLo0.xy, offZ, 0     4: asmOff0.xy, 0, 0
 *   5: pinBox min xyz, 0              6: pinBox max xyz, 0
 *   (texel 3.w = envelope radius, texel 4.zw = envelope centre xy; radius 0 = no envelope)
 * Layers of the prototype occupy prototypes [baseProto, baseProto + nLayers).
 */
export function packLatticeGrid(g: LatticeGrid, baseProto: number, nLayers: number,
                                data: Float32Array, index: number): void {
  const t = index * LAT_TEXELS * 4;
  data.set([g.outerLo[0], g.outerLo[1], g.outerPitch[0], g.outerPitch[1]], t);
  data.set([g.outerCount[0], g.outerCount[1], g.innerPitch[0], g.innerPitch[1]], t + 4);
  data.set([g.innerCount[0], g.innerCount[1], baseProto, nLayers], t + 8);
  data.set([g.pinLo0[0], g.pinLo0[1], g.offZ, g.env[2]], t + 12);
  data.set([g.asmOff0[0], g.asmOff0[1], g.env[0], g.env[1]], t + 16);
  data.set([g.pinBox.xMin, g.pinBox.yMin, g.pinBox.zMin, 0], t + 20);
  data.set([g.pinBox.xMax, g.pinBox.yMax, g.pinBox.zMax, 0], t + 24);
}
