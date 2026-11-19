<script lang="ts">
  // CsgViewportPanel — CSG ray viewer with Phase A/B/C/D scaling work.
  // Phase D: nested lattices (inner_offsets) + lattice-aware fill (plane-only).
  // Phase C: lattice instancing. Phase B: analytic ray intervals for primary hit.
  // Phase A: BVH, root AABB, tetrahedron normals, adaptive DPR, perf logs.
  //
  // SURFACE TYPES the shader understands (keep packSurface(), accumulateSurfaceBounds(),
  // and the GLSL surfaceSDF/surfaceInterval/surfaceCrossings in sync when adding one):
  //   0-2 plane_x/y/z   3-5 cylinder_x/y/z   6 sphere
  //   7 cone_z   (double-napped, Inside = both nappes; params x0,y0,z0,r2 = slope^2)
  //   8 torus    (z-axis; params x0,y0,z0,a,b,c; Inside = ((rho-a)/b)^2 + ((z-z0)/c)^2 < 1)
  // ROTATED / INSTANCED PLACEMENTS: the backend exports universe_id/fill/translation/rotation
  // per cell; csgUniverses.resolveWorldCells() turns them into (cell, offset, quaternion)
  // and every such cell becomes an instance whose transform is stored in the instance
  // texture (row 0 = offset + proto, row 1 = quaternion). The shader inverse-rotates the
  // ray into the cell's local frame and rotates the normal back.
  //
  // Everything the shader looks up by index lives in float data textures, NOT uniform arrays
  // (WebGL only guarantees MAX_FRAGMENT_UNIFORM_VECTORS >= 221, desktop GPUs expose ~1024, and
  // the old uniform tables used ~700 of those, which capped surfaces at 256 and prototypes at 64):
  //   surface table   2 texels each: params (vec4 centre/radius) | meta (x = type id, y/z = torus b, c)
  //   prototype table 2 texels each: colour rgb + isFill | token count, first grid, grid count
  //   grid table      2 texels each: origin.xy + pitch.xy | nx, ny, envelope prototype
  //   instances       2 texels each: offset + first prototype/layer count | rotation quaternion
  //   BVH nodes       3 texels each; tokens: one row per prototype
  // Tables are laid out as a linear array wrapped at a fixed texture width, so the only limit is
  // memory. Only a few scalars (counts, texture heights, camera) remain uniforms.

  import { onMount, onDestroy, untrack } from 'svelte';
  import * as THREE from 'three';
  import { buildLatticeGrid, packLatticeGrid, pinEnvelope, pinStack, LAT_TEXELS, type LatticeGrid } from '../latticeGrid';
  import { activeProject } from '../stores/projects.svelte.js';
  import { csgState, requestCsgRefresh } from '../stores/csg.svelte.js';
  // Same store ObjectPanel writes — keyns are `${projectId}:${placementName}`
  // inside isVisible/toggleVisibility. Must match ObjectPanel's import path.
  import { isVisible, visibility } from '../stores/visibility.svelte.js';
  import { baseGroupName } from '../csgCellGrouping';
  import { resolveWorldCells, transformAABB, QUAT_IDENTITY, type Quat, type WorldCell } from '../csgUniverses';
  import type { CsgCell, CsgGeometry, CsgSurface, RegionNode, LatticeInstance } from '$lib/types';

  /** Placement key for visibility — same grouping as ObjectPanel rows. */
  function visibilityKey(name: string | null | undefined): string | null {
    if (!name) return null;
    return baseGroupName(name);
  }

  const MAX_SURFACES = 4096;
  const MAX_PROTOTYPES = 1024;
  const MAX_INSTANCES = 16384;
  // Fill cell = 6 box planes + Outside(each pin outer). A 10×10 lattice needs
  // ~200 RPN pairs; 17×17 needs ~600. Cap of 96 was truncating the AND chain
  // so the water solid lost constraints and pins appeared to punch through sides.
  const MAX_TOKENS_PER_PROTO = 256;
  const MAX_BVH_NODES = 2 * MAX_INSTANCES;   // a binary tree over N leaves has 2N - 1 nodes
  // Pin grids a cell is cut by (see GridBlocker).
  // Longest token stream the exact leaf test handles (longer ones fall back to marching).
  const MAX_EXACT_TOKENS = 48;
  // A pin's layers (pellet, gap, clad ...) share ONE instance and BVH leaf: an instance names
  // its first prototype and how many consecutive ones it covers. Pins with more layers than
  // this are split into several instances. The count rides in the instance's w, above the id.
  const MAX_GROUP = 8;
  const GROUP_STRIDE = 4096;
  // Lattice leaf (see latticeGrid.ts): a rectangular pin grid is ONE BVH leaf and the shader walks
  // the pitch cells a ray crosses. Steps bound the cells one ray may cross inside one assembly /
  // across the assembly grid; buildLatticeGrid() refuses lattices that could exceed them.
  const MAX_LATTICES = 64;
  const MAX_LAT_LAYERS = MAX_GROUP;
  const MAX_LAT_STEPS = 384;
  const MAX_LAT_OUTER = 96;
  const MAX_GRIDS = 256;
  const MAX_GRIDS_PER_PROTO = 4;
  // Wrap widths (texels) of the linear tables above. BVH_TEX_W is a multiple of 3 so a node's
  // three texels never straddle a row.
  const SURF_TEX_W = 1024, PROTO_TEX_W = 1024, GRID_TEX_W = 1024, INST_TEX_W = 2048, BVH_TEX_W = 1536;
  const INTERACT_DPR = 0.65;
  // Tint pixels where two solid (non-fill) cells genuinely overlap. Overlaps are
  // invalid in OpenMC, so this doubles as a cheap geometry sanity check.
  const MARK_OVERLAPS = true;
  // Depth-tolerance floor as a fraction of scene span. ~50x float32 ulp at the
  // scene scale: small enough never to blur real features, big enough that
  // float noise cannot flip a tie. The per-pixel tolerance is screen-relative.
  const EPS_MIN_FRACTION = 1e-5;
  // Minimum per-axis padding of a cell's AABB, as a fraction of scene span: 10x the
  // depth-tolerance floor, so a box always clears float noise but stays far tighter
  // than the cell on tall thin objects (see cellAABB).
  const AABB_PAD_FLOOR_FRACTION = 10 * EPS_MIN_FRACTION;
  const IDLE_DPR = Math.min(typeof window !== 'undefined' ? window.devicePixelRatio : 1.5, 1.5);
  // ---- render-perf pass 1 switches ---------------------------------------------------------
  // Table lookups in the shader use texelFetch (integer addressing; no float divides per fetch).
  // Output is bit-identical to the old texture2D path. Flip to false to A/B it with the GPU-ms badge.
  const SHADER_TEXELFETCH = true;
  // While the camera moves, choose the render scale from the MEASURED GPU cost per pixel so a frame
  // lands near TARGET_FRAME_MS (needs EXT_disjoint_timer_query_webgl2: Chromium browsers). Without
  // the extension the fixed INTERACT_DPR above is used, exactly as before.
  const ADAPTIVE_INTERACT_DPR = true;
  const TARGET_FRAME_MS = 14;
  const MIN_INTERACT_DPR = 0.3;
  const MAX_INTERACT_DPR = Math.min(1.0, IDLE_DPR);

  const OP_INSIDE = 0, OP_OUTSIDE = 1, OP_AND = 2, OP_OR = 3, OP_NOT = 4;

  function flattenRegion(node: RegionNode, surfIndex: Map<string, number>, out: number[]) {
    switch (node.op) {
      case 'inside': out.push(OP_INSIDE, surfIndex.get(node.surface) ?? -1); return;
      case 'outside': out.push(OP_OUTSIDE, surfIndex.get(node.surface) ?? -1); return;
      case 'and': {
        if (node.items.length === 0) { out.push(OP_INSIDE, -1); return; }
        flattenRegion(node.items[0], surfIndex, out);
        for (let i = 1; i < node.items.length; i++) { flattenRegion(node.items[i], surfIndex, out); out.push(OP_AND, 0); }
        return;
      }
      case 'or': {
        if (node.items.length === 0) { out.push(OP_INSIDE, -1); return; }
        flattenRegion(node.items[0], surfIndex, out);
        for (let i = 1; i < node.items.length; i++) { flattenRegion(node.items[i], surfIndex, out); out.push(OP_OR, 0); }
        return;
      }
      case 'not': flattenRegion(node.item, surfIndex, out); out.push(OP_NOT, 0); return;
    }
  }

  function surfaceIdsInRegion(node: RegionNode, out: string[]) {
    switch (node.op) {
      case 'inside': case 'outside': out.push(node.surface); return;
      case 'and': case 'or': for (const item of node.items) surfaceIdsInRegion(item, out); return;
      case 'not': surfaceIdsInRegion(node.item, out); return;
    }
  }


  /** Phase D fill scaling: keep only plane half-spaces from a region tree.
   *  Used for the moderator fill cell under lattice instancing — pin holes are
   *  already owned by pin protos (nearest-hit), so Outside(every pin) is redundant
   *  for opaque primary rays and blows the token budget at core scale.
   */
  function planeOnlyRegion(node: RegionNode, surfaceById: Map<string, CsgSurface>): RegionNode | null {
    switch (node.op) {
      case 'inside':
      case 'outside': {
        const s = surfaceById.get(node.surface);
        if (!s) return null;
        if (s.type === 'plane_x' || s.type === 'plane_y' || s.type === 'plane_z') return node;
        return null;
      }
      case 'and': {
        const items: RegionNode[] = [];
        for (const it of node.items) {
          const k = planeOnlyRegion(it, surfaceById);
          if (k) items.push(k);
        }
        if (items.length === 0) return null;
        if (items.length === 1) return items[0];
        return { op: 'and', items };
      }
      case 'or': {
        // Bugfix: unlike 'and' (where dropping a non-plane item only
        // *widens* the region — safe, since pin-vs-fill priority in the
        // shader still resolves visibility), dropping a disjunct from an
        // 'or' *narrows* it. The per-pin fill exclusion is now
        // Outside(cyl) OR Inside(bot) OR Outside(top) (De Morgan complement
        // of the pin's finite solid volume). Stripping the non-plane
        // Outside(cyl) term here used to leave `Inside(bot) OR
        // Outside(top)` behind — "outside the pin's axial band" with no
        // radial scoping at all — which, ANDed into the fill cell, wiped
        // out the moderator across the *entire* box footprint at that
        // height (not just at each pin), for every pin in the lattice.
        // An 'or' is only safe to keep if *every* branch survives; if any
        // branch is non-plane, drop the whole thing (falls back to the
        // pre-instancing behavior of just not scoping this exclusion at
        // all under lattices, same as before De Morgan).
        const items: RegionNode[] = [];
        for (const it of node.items) {
          const k = planeOnlyRegion(it, surfaceById);
          if (!k) return null;
          items.push(k);
        }
        if (items.length === 0) return null;
        if (items.length === 1) return items[0];
        return { op: 'or', items };
      }
      case 'not': {
        const inner = planeOnlyRegion(node.item, surfaceById);
        return inner ? { op: 'not', item: inner } : null;
      }
    }
  }

  /** True when a region describes a real SOLID, not a moderator fill.
   *
   *  planeOnlyRegion() is a stand-in for the moderator fill only (box planes minus
   *  Outside(every pin)). Stripping is safe there because it only widens a cell that
   *  loses every pin-vs-fill tie. For a real solid it is destructive: dropping
   *  Inside(cylinder/sphere/cone/torus) turns a CylinderZ into a slab spanning the
   *  whole scene footprint (the "brick"), and dropping a NOT(...) operand re-fills a
   *  boolean subtraction. A fill never carries a positive curved-surface constraint
   *  or a NOT, so either one means "keep the exact region".
   */
  function isSolidRegion(node: RegionNode, surfaceById: Map<string, CsgSurface>): boolean {
    switch (node.op) {
      case 'inside': {
        const s = surfaceById.get(node.surface);
        return !!s && s.type !== 'plane_x' && s.type !== 'plane_y' && s.type !== 'plane_z';
      }
      case 'outside': return false;
      case 'not': return true;
      case 'and': case 'or': return node.items.some((it) => isSolidRegion(it, surfaceById));
    }
  }

  function countSurfaceRefs(node: RegionNode): number {
    const ids: string[] = [];
    surfaceIdsInRegion(node, ids);
    return ids.length;
  }

  function num(params: Record<string, number>, canon: string, alias: string, fallback = 0): number {
    if (params[canon] != null) return params[canon];
    if (params[alias] != null) return params[alias];
    return fallback;
  }

  /** Packed surface: `type` id + `v` (surface table params texel) + `b`/`c` extras (meta texel .yz). */
  interface PackedSurface { type: number; v: THREE.Vector4; b: number; c: number; }

  function packSurface(s: CsgSurface): PackedSurface {
    const p = s.params;
    const mk = (type: number, v: THREE.Vector4, b = 0, c = 0): PackedSurface => ({ type, v, b, c });
    // cone_z (type 7): v = (x0, y0, z0, r2). r2 is already the SQUARED slope (the backend
    // squares radius_slope exactly once in primitives.py), so it is passed through as-is;
    // the shader takes sqrt only where it needs the un-squared slope (surfaceSDF).
    if (s.type === 'cone_z') {
      return mk(7, new THREE.Vector4(num(p, 'x0', 'x'), num(p, 'y0', 'y'), num(p, 'z0', 'z'), Math.max(num(p, 'r2', 'r2', 1), 0)));
    }
    // torus (type 8): v = (x0, y0, z0, a), extras b (radial tube semi-axis) and c (axial
    // tube semi-axis). The backend always emits c (defaults it to b for a circular tube).
    // b/c are clamped away from 0 because the shader divides by them.
    if (s.type === 'torus') {
      const b = Math.max(Math.abs(num(p, 'b', 'b', 0.5)), 1e-6);
      const c = Math.max(Math.abs(num(p, 'c', 'c', b)), 1e-6);
      return mk(8, new THREE.Vector4(num(p, 'x0', 'x'), num(p, 'y0', 'y'), num(p, 'z0', 'z'), Math.abs(num(p, 'a', 'a', 1))), b, c);
    }
    switch (s.type) {
      case 'plane_x': return mk(0, new THREE.Vector4(num(p, 'x0', 'x'), 0, 0, 0));
      case 'plane_y': return mk(1, new THREE.Vector4(0, num(p, 'y0', 'y'), 0, 0));
      case 'plane_z': return mk(2, new THREE.Vector4(0, 0, num(p, 'z0', 'z'), 0));
      case 'cylinder_x': return mk(3, new THREE.Vector4(0, num(p, 'y0', 'y'), num(p, 'z0', 'z'), num(p, 'r', 'r', 1)));
      case 'cylinder_y': return mk(4, new THREE.Vector4(num(p, 'x0', 'x'), 0, num(p, 'z0', 'z'), num(p, 'r', 'r', 1)));
      case 'cylinder_z': return mk(5, new THREE.Vector4(num(p, 'x0', 'x'), num(p, 'y0', 'y'), 0, num(p, 'r', 'r', 1)));
      case 'sphere': return mk(6, new THREE.Vector4(num(p, 'x0', 'x'), num(p, 'y0', 'y'), num(p, 'z0', 'z'), num(p, 'r', 'r', 1)));
      default: return mk(-1, new THREE.Vector4(0, 0, 0, 0));
    }
  }

  // Curated palette — keep in sync with scene_builder_service._MATERIAL_COLORS
  // so CSG viewport matches ResultsViewport3D / GeometryPlotPanel.
  const MATERIAL_COLORS: Record<string, string> = {
    'UO2': '#E8703A', 'UO2_3.5pct': '#E8703A', 'UO2_4pct': '#D4612E',
    'ThO2': '#C8A882', 'UC': '#B06030',
    'He': '#D0EEF8', 'He4': '#D0EEF8', 'Air': '#E8F4F8',
    'Zr4': '#8BAFC0', 'Zircaloy': '#8BAFC0', 'Zircaloy-4': '#8BAFC0',
    'SS304': '#A0A8B0', 'SS316': '#9098A8', 'Steel': '#909090',
    'Al': '#C8D0D8',
    'H2O': '#4A90D9', 'Water': '#4A90D9', 'D2O': '#3A7AC0',
    'Na': '#F0C040', 'LiFBeF2': '#90D0A0', 'FLiBe': '#90D0A0',
    'B4C': '#303030', 'Hafnium': '#606878', 'Hf': '#606878',
    'AgInCd': '#788090', 'Gd2O3': '#A08060',
    'Graphite': '#505050', 'Be': '#B0C8B0', 'Pb': '#787870',
    'void': '#101020', 'Void': '#101020',
  };

  function hexToRgb01(hex: string): [number, number, number] {
    const h = hex.replace('#', '');
    const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  }

  function hashColor(id: string): [number, number, number] {
    let h = 2166136261;
    for (let i = 0; i < id.length; i++) { h ^= id.charCodeAt(i); h = Math.imul(h, 16777619); }
    return hslToRgb(((h >>> 0) % 360) / 360, 0.55, 0.55);
  }

  function resolveMaterialColor(id: string): [number, number, number] {
    const curated = MATERIAL_COLORS[id];
    if (curated) return hexToRgb01(curated);
    const lower = id.toLowerCase();
    for (const [k, v] of Object.entries(MATERIAL_COLORS)) {
      if (k.toLowerCase() === lower) return hexToRgb01(v);
    }
    return hashColor(id);
  }
  function hslToRgb(h: number, s: number, l: number): [number, number, number] {
    const c = (1 - Math.abs(2 * l - 1)) * s;
    const hp = h * 6;
    const x = c * (1 - Math.abs((hp % 2) - 1));
    let [r1, g1, b1] = [0, 0, 0];
    if (hp < 1) [r1, g1, b1] = [c, x, 0];
    else if (hp < 2) [r1, g1, b1] = [x, c, 0];
    else if (hp < 3) [r1, g1, b1] = [0, c, x];
    else if (hp < 4) [r1, g1, b1] = [0, x, c];
    else if (hp < 5) [r1, g1, b1] = [x, 0, c];
    else [r1, g1, b1] = [c, 0, x];
    const m = l - c / 2;
    return [r1 + m, g1 + m, b1 + m];
  }

  interface AxisBounds { xMin: number; xMax: number; yMin: number; yMax: number; zMin: number; zMax: number; }

  function accumulateSurfaceBounds(b: AxisBounds, s: CsgSurface): AxisBounds {
    const p = s.params;
    if (s.type === 'plane_x') { const v = num(p, 'x0', 'x'); return { ...b, xMin: Math.min(b.xMin, v), xMax: Math.max(b.xMax, v) }; }
    if (s.type === 'plane_y') { const v = num(p, 'y0', 'y'); return { ...b, yMin: Math.min(b.yMin, v), yMax: Math.max(b.yMax, v) }; }
    if (s.type === 'plane_z') { const v = num(p, 'z0', 'z'); return { ...b, zMin: Math.min(b.zMin, v), zMax: Math.max(b.zMax, v) }; }
    if (s.type === 'cylinder_z') {
      const x0 = num(p, 'x0', 'x'), y0 = num(p, 'y0', 'y'), r = num(p, 'r', 'r', 1);
      return { ...b, xMin: Math.min(b.xMin, x0 - r), xMax: Math.max(b.xMax, x0 + r), yMin: Math.min(b.yMin, y0 - r), yMax: Math.max(b.yMax, y0 + r) };
    }
    if (s.type === 'cylinder_x') {
      const y0 = num(p, 'y0', 'y'), z0 = num(p, 'z0', 'z'), r = num(p, 'r', 'r', 1);
      return { ...b, yMin: Math.min(b.yMin, y0 - r), yMax: Math.max(b.yMax, y0 + r), zMin: Math.min(b.zMin, z0 - r), zMax: Math.max(b.zMax, z0 + r) };
    }
    if (s.type === 'cylinder_y') {
      const x0 = num(p, 'x0', 'x'), z0 = num(p, 'z0', 'z'), r = num(p, 'r', 'r', 1);
      return { ...b, xMin: Math.min(b.xMin, x0 - r), xMax: Math.max(b.xMax, x0 + r), zMin: Math.min(b.zMin, z0 - r), zMax: Math.max(b.zMax, z0 + r) };
    }
    if (s.type === 'sphere') {
      const x0 = num(p, 'x0', 'x'), y0 = num(p, 'y0', 'y'), z0 = num(p, 'z0', 'z'), r = num(p, 'r', 'r', 1);
      return { ...b, xMin: Math.min(b.xMin, x0 - r), xMax: Math.max(b.xMax, x0 + r), yMin: Math.min(b.yMin, y0 - r), yMax: Math.max(b.yMax, y0 + r), zMin: Math.min(b.zMin, z0 - r), zMax: Math.max(b.zMax, z0 + r) };
    }
    // z-torus: finite in all three axes. Radial reach is ring radius + radial tube
    // semi-axis (a + b); axial reach is the axial semi-axis c. (Mirrors
    // backend/cascade/domain/bounds.py so viewport AABBs agree with the backend.)
    if (s.type === 'torus') {
      const x0 = num(p, 'x0', 'x'), y0 = num(p, 'y0', 'y'), z0 = num(p, 'z0', 'z');
      const a = Math.abs(num(p, 'a', 'a', 1)), tb = Math.abs(num(p, 'b', 'b', 0.5));
      const tc = Math.abs(num(p, 'c', 'c', tb));
      const reach = a + tb;
      return { xMin: Math.min(b.xMin, x0 - reach), xMax: Math.max(b.xMax, x0 + reach), yMin: Math.min(b.yMin, y0 - reach), yMax: Math.max(b.yMax, y0 + reach), zMin: Math.min(b.zMin, z0 - tc), zMax: Math.max(b.zMax, z0 + tc) };
    }
    // cone_z is unbounded on its own (both nappes run to +/-infinity along z), so it adds
    // nothing here; applyConeBounds() clips its xy extent to whatever z range the
    // OTHER surfaces in the same set establish.
    return b;
  }

  /**
   * Give cone_z surfaces a finite xy extent once the z range is known.
   *
   * A cone cell is only finite when something else (usually two plane_z) caps it.
   * Without this its AABB would fall back to the whole-scene xy bounds, which is
   * wasteful at best and, for a scene made of just a capped cone, falls to the +/-5 default
   * and clips the cone. The cone radius grows linearly from the apex, so the widest
   * point inside [zMin, zMax] is at whichever z end is farther from the apex.
   * No-op while z is still unbounded (nothing to clip against).
   */
  function applyConeBounds(b: AxisBounds, cones: CsgSurface[]): AxisBounds {
    if (!isFinite(b.zMin) || !isFinite(b.zMax)) return b;
    let out = b;
    for (const s of cones) {
      const p = s.params;
      const x0 = num(p, 'x0', 'x'), y0 = num(p, 'y0', 'y'), z0 = num(p, 'z0', 'z');
      const slope = Math.sqrt(Math.max(num(p, 'r2', 'r2', 1), 0));
      const R = slope * Math.max(Math.abs(out.zMin - z0), Math.abs(out.zMax - z0));
      out = { ...out, xMin: Math.min(out.xMin, x0 - R), xMax: Math.max(out.xMax, x0 + R), yMin: Math.min(out.yMin, y0 - R), yMax: Math.max(out.yMax, y0 + R) };
    }
    return out;
  }

  function computeBounds(csg: CsgGeometry): AxisBounds {
    let b: AxisBounds = { xMin: Infinity, xMax: -Infinity, yMin: Infinity, yMax: -Infinity, zMin: Infinity, zMax: -Infinity };
    for (const s of csg.surfaces) b = accumulateSurfaceBounds(b, s);
    b = applyConeBounds(b, csg.surfaces.filter((s) => s.type === 'cone_z'));
    // Per-axis fallback — a missing plane_y must not leave yMin/yMax at ±Infinity
    // (that breaks cameraDistance and the shader root AABB).
    if (!isFinite(b.xMin) || !isFinite(b.xMax)) { b.xMin = -5; b.xMax = 5; }
    if (!isFinite(b.yMin) || !isFinite(b.yMax)) { b.yMin = -5; b.yMax = 5; }
    if (!isFinite(b.zMin) || !isFinite(b.zMax)) { b.zMin = -5; b.zMax = 5; }
    return b;
  }

  /** Bounds of a cell. `padded = false` returns the exact surface extents (no safety margin); the
   *  lattice fast path uses that to decide whether a pin really fits inside its pitch cell. */
  function cellAABB(region: RegionNode, surfaceById: Map<string, CsgSurface>, fallback: AxisBounds, padded = true): AxisBounds {
    // Always accumulate bounds from surfaces referenced by the region (cylinders
    // give tight xy even when the cell also has Outside(...)). Only fall back
    // per-axis when that axis is missing — do NOT replace the whole box with
    // sceneBounds solely because the region contains an Outside node.
    const names: string[] = [];
    surfaceIdsInRegion(region, names);
    let b: AxisBounds = { xMin: Infinity, xMax: -Infinity, yMin: Infinity, yMax: -Infinity, zMin: Infinity, zMax: -Infinity };
    const cones: CsgSurface[] = [];
    for (const n of names) {
      const s = surfaceById.get(n);
      if (!s) continue;
      b = accumulateSurfaceBounds(b, s);
      if (s.type === 'cone_z') cones.push(s);
    }
    b = applyConeBounds(b, cones);
    if (!isFinite(b.xMin) || !isFinite(b.xMax)) { b.xMin = fallback.xMin; b.xMax = fallback.xMax; }
    if (!isFinite(b.yMin) || !isFinite(b.yMax)) { b.yMin = fallback.yMin; b.yMax = fallback.yMax; }
    if (!isFinite(b.zMin) || !isFinite(b.zMax)) { b.zMin = fallback.zMin; b.zMax = fallback.zMax; }
    // Pad PER AXIS, not by 2% of the largest axis. A reactor pin is ~0.95 cm wide and
    // ~365 cm tall: padding every axis by 2% of the height gave each pin an XY box
    // ~7.3 cm larger on every side (16x the pin), so neighbouring boxes overlapped
    // heavily, the BVH could not prune, and every ray ran leafExact on 100+ pins.
    // Hit depths are exact (leafExact), so the box only has to contain the cell; the
    // floor keeps flat/degenerate axes and float noise from producing a zero-width slab.
    const floor = AABB_PAD_FLOOR_FRACTION * Math.max(
      fallback.xMax - fallback.xMin, fallback.yMax - fallback.yMin, fallback.zMax - fallback.zMin, 1);
    const padX = padded ? Math.max(0.02 * (b.xMax - b.xMin), floor) : 0;
    const padY = padded ? Math.max(0.02 * (b.yMax - b.yMin), floor) : 0;
    const padZ = padded ? Math.max(0.02 * (b.zMax - b.zMin), floor) : 0;
    return { xMin: b.xMin - padX, xMax: b.xMax + padX, yMin: b.yMin - padY, yMax: b.yMax + padY, zMin: b.zMin - padZ, zMax: b.zMax + padZ };
  }

  function translateAABB(b: AxisBounds, dx: number, dy: number, dz: number): AxisBounds {
    return { xMin: b.xMin + dx, xMax: b.xMax + dx, yMin: b.yMin + dy, yMax: b.yMax + dy, zMin: b.zMin + dz, zMax: b.zMax + dz };
  }

  function unionAABB(a: AxisBounds, b: AxisBounds): AxisBounds {
    return { xMin: Math.min(a.xMin, b.xMin), xMax: Math.max(a.xMax, b.xMax), yMin: Math.min(a.yMin, b.yMin), yMax: Math.max(a.yMax, b.yMax), zMin: Math.min(a.zMin, b.zMin), zMax: Math.max(a.zMax, b.zMax) };
  }

  // ---- BVH build: binned surface-area heuristic (render-perf pass 2) ---------------------------
  // WHY: the old build split at the median centroid of the longest axis. That cannot separate a
  // big container box from the small things inside it (a shell's centroid sits in the middle of
  // everything), so a scene of nested boxes + pins had every ray visit ~15 nodes of a 77-node tree
  // (see the "Cost: BVH nodes" heat-map). SAH picks the split minimising
  // nLeft * area(left) + nRight * area(right), which isolates large overlapping boxes near the root.
  // Measured on a CPU model of the shader walk for 3 nested boxes + 36 pins: mean visits 15.0 -> 7.4.
  // Output format is unchanged (flattenBvh / the shader do not care how the tree was chosen).
  const BVH_SAH_BINS = 16;
  // The shader's traversal stack holds MAX_BVH_STACK = 64 entries and a walk can have one pending
  // sibling per tree level. SAH trees are not balanced, so past this depth fall back to the old
  // balanced median split for the subtree (worst case 40 + log2(16384) = 54 levels < 64).
  const BVH_SAH_MAX_DEPTH = 40;

  interface BvhBuildNode { bounds: AxisBounds; isLeaf: boolean; instanceIndex: number; left: BvhBuildNode | null; right: BvhBuildNode | null; }

  function surfaceArea(b: AxisBounds): number {
    const x = b.xMax - b.xMin, y = b.yMax - b.yMin, z = b.zMax - b.zMin;
    return 2 * (x * y + y * z + z * x);
  }
  function axisCentroid(b: AxisBounds, axis: number): number {
    return axis === 0 ? (b.xMin + b.xMax) / 2 : axis === 1 ? (b.yMin + b.yMax) / 2 : (b.zMin + b.zMax) / 2;
  }

  /** Best SAH partition of `indices` into two non-empty groups, or null if no axis separates them. */
  function sahSplit(indices: number[], boxes: AxisBounds[]): [number[], number[]] | null {
    const NB = BVH_SAH_BINS;
    let best = { cost: Infinity, axis: 0, lo: 0, scale: 0, split: 0 };
    const binCount = new Int32Array(NB);
    const binBox: (AxisBounds | null)[] = new Array(NB).fill(null);
    const rightArea = new Float64Array(NB), rightN = new Int32Array(NB);
    for (let axis = 0; axis < 3; axis++) {
      let lo = Infinity, hi = -Infinity;
      for (const i of indices) { const c = axisCentroid(boxes[i], axis); if (c < lo) lo = c; if (c > hi) hi = c; }
      if (!(hi - lo > 1e-12)) continue;                       // all centroids equal on this axis
      const scale = NB / (hi - lo);
      binCount.fill(0); binBox.fill(null);
      for (const i of indices) {
        const k = Math.min(NB - 1, Math.floor((axisCentroid(boxes[i], axis) - lo) * scale));
        binCount[k]++;
        binBox[k] = binBox[k] ? unionAABB(binBox[k]!, boxes[i]) : boxes[i];
      }
      // Two O(NB) sweeps (suffix then prefix) instead of re-merging bins per candidate split.
      let acc: AxisBounds | null = null, n = 0;
      for (let k = NB - 1; k >= 1; k--) {
        if (binBox[k]) acc = acc ? unionAABB(acc, binBox[k]!) : binBox[k];
        n += binCount[k];
        rightN[k] = n; rightArea[k] = acc ? surfaceArea(acc) : 0;
      }
      acc = null; n = 0;
      for (let k = 0; k < NB - 1; k++) {
        if (binBox[k]) acc = acc ? unionAABB(acc, binBox[k]!) : binBox[k];
        n += binCount[k];
        if (n === 0 || rightN[k + 1] === 0) continue;         // both sides must be non-empty
        const cost = n * surfaceArea(acc!) + rightN[k + 1] * rightArea[k + 1];
        if (cost < best.cost) best = { cost, axis, lo, scale, split: k + 1 };
      }
    }
    if (best.cost === Infinity) return null;
    const left: number[] = [], right: number[] = [];
    for (const i of indices) {
      const k = Math.min(NB - 1, Math.floor((axisCentroid(boxes[i], best.axis) - best.lo) * best.scale));
      (k < best.split ? left : right).push(i);
    }
    return left.length && right.length ? [left, right] : null;
  }

  function buildBvh(indices: number[], boxes: AxisBounds[], depth = 0): BvhBuildNode {
    if (indices.length === 1) {
      const i = indices[0];
      return { bounds: boxes[i], isLeaf: true, instanceIndex: i, left: null, right: null };
    }
    let leftIdx: number[] | null = null, rightIdx: number[] | null = null;
    if (depth < BVH_SAH_MAX_DEPTH) {
      const split = sahSplit(indices, boxes);
      if (split) [leftIdx, rightIdx] = split;
    }
    if (!leftIdx || !rightIdx) {
      // Fallback (deep subtree, or every centroid identical): balanced median split, longest axis.
      let combined = boxes[indices[0]];
      for (let k = 1; k < indices.length; k++) combined = unionAABB(combined, boxes[indices[k]]);
      const spanX = combined.xMax - combined.xMin, spanY = combined.yMax - combined.yMin, spanZ = combined.zMax - combined.zMin;
      const axis = spanX >= spanY && spanX >= spanZ ? 0 : spanY >= spanZ ? 1 : 2;
      const sorted = [...indices].sort((a, b) => axisCentroid(boxes[a], axis) - axisCentroid(boxes[b], axis));
      const mid = Math.floor(sorted.length / 2);
      leftIdx = sorted.slice(0, mid); rightIdx = sorted.slice(mid);
    }
    const left = buildBvh(leftIdx, boxes, depth + 1);
    const right = buildBvh(rightIdx, boxes, depth + 1);
    return { bounds: unionAABB(left.bounds, right.bounds), isLeaf: false, instanceIndex: -1, left, right };
  }

  function writeBvhTexel(data: Float32Array, nodeIdx: number, col: number, x: number, y: number, z: number, w: number) {
    const base = (nodeIdx * 3 + col) * 4;
    data[base] = x; data[base + 1] = y; data[base + 2] = z; data[base + 3] = w;
  }

  /** Leaves with index >= latticeLeafBase are lattice leaves (kind 2); the rest are instances (kind 1).
   *  The leaf's w in texel 1 holds the instance index, or the lattice index for a lattice leaf. */
  function flattenBvh(root: BvhBuildNode, data: Float32Array, latticeLeafBase: number): { count: number; truncated: boolean } {
    let counter = 0;
    function assign(node: BvhBuildNode): number {
      const idx = counter++;
      if (idx >= MAX_BVH_NODES) return idx;
      const b = node.bounds;
      const isLattice = node.isLeaf && node.instanceIndex >= latticeLeafBase;
      writeBvhTexel(data, idx, 0, b.xMin, b.yMin, b.zMin, node.isLeaf ? (isLattice ? 2 : 1) : 0);
      if (node.isLeaf) {
        writeBvhTexel(data, idx, 1, b.xMax, b.yMax, b.zMax, isLattice ? node.instanceIndex - latticeLeafBase : node.instanceIndex);
        writeBvhTexel(data, idx, 2, 0, 0, 0, 0);
      } else {
        writeBvhTexel(data, idx, 1, b.xMax, b.yMax, b.zMax, 0);
        const leftIdx = assign(node.left!);
        const rightIdx = assign(node.right!);
        writeBvhTexel(data, idx, 2, leftIdx, rightIdx, 0, 0);
      }
      return idx;
    }
    assign(root);
    return { count: Math.min(counter, MAX_BVH_NODES), truncated: counter > MAX_BVH_NODES };
  }

  let csg = $state<CsgGeometry | null>(null);
  let truncatedSurfaces = $state(false);
  let truncatedProtos = $state(false);
  let truncatedInstances = $state(false);
  let truncatedTokens = $state(false);
  let truncatedBvh = $state(false);
  let truncatedGrids = $state(false);
  let instanceCount = $state(0);
  let prototypeCount = $state(0);
  let materialLegend = $state<{ id: string; color: string }[]>([]);
  let usingInstancing = $state(false);

  // --- Bug-repro debug logging (back-wall disappearance investigation) ---
  // Toggle with the "Debug walls" button in the panel footer. When on,
  // every time the drag ends we print:
  //   <cam position and rotation>: world-space camera pos + theta/phi/dist
  //     + the forward/right/up basis actually sent to the shader
  //   <walls of a box displayed>: for every box-shaped (isFill) instance,
  //     which of its 6 axis-aligned faces geometrically face the camera
  //     (expected to be visible) vs. a readback of the framebuffer at a
  //     grid of sample points, so a mismatch (face expected visible but
  //     the sampled pixel is background color) points straight at the bug.
  let debugWalls = $state(false);
  interface DebugBoxEntry { name: string; aabb: AxisBounds; color: string; }
  // Built on demand by logCsgDebug() only (see the closure assigned in rebuildAndRender).
  let debugBoxSource: (() => DebugBoxEntry[]) | null = null;

  // The CSG comes from the shared store (the same request ObjectPanel makes),
  // so one edit costs one backend expansion instead of one per panel.
  const projectText = $derived(activeProject().text);
  $effect(() => {
    requestCsgRefresh(projectText);
  });

  const loading = $derived(csgState.loading);
  const loadError = $derived(csgState.error);

  // Repack the GPU buffers whenever the store delivers new geometry (or an error
  // clears it). untrack: rebuildAndRender reads a lot of state that must not
  // become a dependency of this effect.
  $effect(() => {
    const data = csgState.data;
    untrack(() => {
      csg = data;
      const t1 = performance.now();
      rebuildAndRender();
      console.log(`[perf] rebuildAndRender: ${(performance.now() - t1).toFixed(1)}ms`);
    });
  });

  // Re-pack protos/instances when an Objects-panel eye toggle flips.
  // Touch every key so deep $state mutations invalidate this effect
  // (reading only the object ref can miss property writes in some runes setups).
  $effect(() => {
    for (const k of Object.keys(visibility)) void visibility[k];
    // Also re-run when the map gains its first hide (keys go from 0 → 1).
    void Object.keys(visibility).length;
    if (shaderMaterial && csg) rebuildAndRender();
  });

  let canvasEl: HTMLCanvasElement;
  let containerEl: HTMLDivElement;
  let renderer: THREE.WebGLRenderer;
  let scene: THREE.Scene;
  let camera: THREE.OrthographicCamera;
  let shaderMaterial: THREE.ShaderMaterial;
  let tokenTexture: THREE.DataTexture | null = null;
  let bvhTexture: THREE.DataTexture | null = null;
  let instanceTexture: THREE.DataTexture | null = null;
  let surfTexture: THREE.DataTexture | null = null;
  let protoTexture: THREE.DataTexture | null = null;
  let gridTexture: THREE.DataTexture | null = null;
  let latTexture: THREE.DataTexture | null = null;

  /** A zeroed float buffer for `texels` RGBA texels of a table wrapped at `width`. */
  function allocTable(width: number, texels: number): { data: Float32Array; width: number; height: number } {
    const height = Math.max(1, Math.ceil(texels / width));
    return { data: new Float32Array(width * height * 4), width, height };
  }
  function tableTexture(t: { data: Float32Array; width: number; height: number }): THREE.DataTexture {
    const tex = new THREE.DataTexture(t.data, t.width, t.height, THREE.RGBAFormat, THREE.FloatType);
    tex.magFilter = THREE.NearestFilter; tex.minFilter = THREE.NearestFilter;
    tex.generateMipmaps = false; tex.needsUpdate = true;
    return tex;
  }

  let cameraTheta = $state(Math.PI / 4);
  let cameraPhi = $state(Math.PI / 3);
  let cameraDistance = $state(8);
  let cameraTarget = new THREE.Vector3(0, 0, 0);
  let hasFramedOnce = false;
  let interactTimer: ReturnType<typeof setTimeout> | undefined;

  // ---- GPU frame timing, adaptive interaction scale, cost heat-map (render-perf pass 1) ------
  // renderer.render() only SUBMITS work, so the old [perf] log (which timed that call) measured CPU
  // submit time, not what the shader costs. These timer queries measure the GPU itself.
  // RULE FOR FUTURE EDITORS: judge any shader change by the GPU-ms badge / [perf] GPU log, never
  // by timing renderer.render() on the CPU. Heat-map: bottom-left "Cost" button (see HEATMAP in the shader).
  interface GpuQuery { q: WebGLQuery; megapixels: number; }
  let gpuTimer: { ext: any; gl: WebGL2RenderingContext } | null = null;
  const gpuQueries: GpuQuery[] = [];
  let gpuPollHandle: number | null = null;
  let msPerMpx: number | null = null;   // smoothed GPU ms per megapixel for the current view
  let gpuLogSum = 0, gpuLogCount = 0, gpuLabelAt = 0;
  let gpuMsLabel = $state<string | null>(null);
  let interactDpr = INTERACT_DPR;       // current interaction scale (see interactionScale)

  function initGpuTimer() {
    try {
      const gl = renderer.getContext() as WebGL2RenderingContext;
      const ext = gl.getExtension('EXT_disjoint_timer_query_webgl2');
      if (ext) gpuTimer = { ext, gl };
    } catch (_) { /* stays null */ }
    console.log(`[csg-viewer] GPU timer queries: ${gpuTimer ? 'available' : 'unavailable -> fixed interaction scale'}`);
  }

  /** renderer.render() inside a GPU timer query (at most 4 results pending at once). */
  function timedRender() {
    if (!gpuTimer || gpuQueries.length >= 4) { renderer.render(scene, camera); return; }
    const { ext, gl } = gpuTimer;
    const q = gl.createQuery();
    if (!q) { renderer.render(scene, camera); return; }
    gl.beginQuery(ext.TIME_ELAPSED_EXT, q);
    renderer.render(scene, camera);
    gl.endQuery(ext.TIME_ELAPSED_EXT);
    renderer.getDrawingBufferSize(_bufSize);
    gpuQueries.push({ q, megapixels: (_bufSize.x * _bufSize.y) / 1e6 });
    if (gpuPollHandle === null) gpuPollHandle = requestAnimationFrame(pollGpuTimer);
  }

  // Results arrive 1-3 frames late, so poll on rAF until the queue drains (idle frames included).
  function pollGpuTimer() {
    gpuPollHandle = null;
    if (!gpuTimer) return;
    const { ext, gl } = gpuTimer;
    if (gl.getParameter(ext.GPU_DISJOINT_EXT)) {          // clock changed / GPU reset: results invalid
      for (const e of gpuQueries) gl.deleteQuery(e.q);
      gpuQueries.length = 0;
      return;
    }
    while (gpuQueries.length && gl.getQueryParameter(gpuQueries[0].q, gl.QUERY_RESULT_AVAILABLE)) {
      const e = gpuQueries.shift()!;
      onGpuFrameTime(gl.getQueryParameter(e.q, gl.QUERY_RESULT) / 1e6, e);
      gl.deleteQuery(e.q);
    }
    if (gpuQueries.length) gpuPollHandle = requestAnimationFrame(pollGpuTimer);
  }

  function onGpuFrameTime(ms: number, e: GpuQuery) {
    const perMpx = ms / Math.max(e.megapixels, 1e-6);
    msPerMpx = msPerMpx === null ? perMpx : 0.7 * msPerMpx + 0.3 * perMpx;   // EMA: stable across scale changes
    gpuLogSum += ms; gpuLogCount++;
    if (gpuLogCount === 30) {
      console.log(`[perf] GPU avg=${(gpuLogSum / 30).toFixed(2)}ms  ${msPerMpx.toFixed(2)} ms/Mpx  protos=${prototypeCount} instances=${instanceCount} instancing=${usingInstancing}`);
      gpuLogSum = 0; gpuLogCount = 0;
    }
    const now = performance.now();
    if (now - gpuLabelAt > 250) { gpuLabelAt = now; gpuMsLabel = `${ms.toFixed(1)} ms`; }   // throttled: it is reactive state
  }

  /** Render scale while the camera moves: the largest one predicted to fit TARGET_FRAME_MS. */
  function interactionScale(): number {
    if (!ADAPTIVE_INTERACT_DPR || msPerMpx === null || !containerEl) return INTERACT_DPR;
    const cssMpx = (containerEl.clientWidth * containerEl.clientHeight) / 1e6;
    if (cssMpx <= 0) return INTERACT_DPR;
    // frame ms ~= msPerMpx * cssMpx * dpr^2  ->  solve for the dpr that hits the target
    const want = Math.sqrt(TARGET_FRAME_MS / (msPerMpx * cssMpx));
    const snapped = Math.round(Math.min(MAX_INTERACT_DPR, Math.max(MIN_INTERACT_DPR, want)) * 20) / 20;
    // Dead-band: every change reallocates the canvas, so ignore moves smaller than 15 %.
    if (Math.abs(snapped - interactDpr) / interactDpr > 0.15) interactDpr = snapped;
    return interactDpr;
  }

  // Cost heat-map: per-pixel work counters, compiled in only while HEATMAP=1 (a recompile), so the
  // normal shader pays nothing. Modes change a uniform only (no recompile). blue = cheap, red = costly.
  const HEAT_MODES = ['off', 'total', 'BVH nodes', 'lattice steps', 'grid steps', 'token evals'] as const;
  let heatMode = $state(0);
  function cycleHeat() {
    const wasOn = heatMode > 0;
    heatMode = (heatMode + 1) % HEAT_MODES.length;
    const isOn = heatMode > 0;
    if (isOn !== wasOn) { shaderMaterial.defines.HEATMAP = isOn ? 1 : 0; shaderMaterial.needsUpdate = true; }
    shaderMaterial.uniforms.uHeatMode.value = heatMode;
    render();
  }

  const VERTEX_SHADER = `varying vec2 vUv; void main(){ vUv=uv; gl_Position=vec4(position.xy,0.0,1.0); }`;

  // Phase B: analytic intervals for primary hit; SDF tetrahedron normals; Phase C instancing.
  const FRAGMENT_SHADER = `
    precision highp float;
    #define MAX_PROTOTYPES ${MAX_PROTOTYPES}
    #define SURF_TEX_W ${SURF_TEX_W}.0
    #define PROTO_TEX_W ${PROTO_TEX_W}.0
    #define GRID_TEX_W ${GRID_TEX_W}.0
    #define INST_TEX_W ${INST_TEX_W}.0
    #define BVH_TEX_W ${BVH_TEX_W}.0
    #define TOK_TEX_W ${MAX_TOKENS_PER_PROTO}.0
    #define MAX_TOKEN_ITERS ${MAX_TOKENS_PER_PROTO}
    #define MAX_GRIDS ${MAX_GRIDS}
    #define LAT_TEX_W ${LAT_TEXELS}.0
    #define LAT_STRIDE ${LAT_TEXELS}
    #define MAX_LAT_LAYERS ${MAX_LAT_LAYERS}
    #define MAX_LAT_STEPS ${MAX_LAT_STEPS}
    #define MAX_LAT_OUTER ${MAX_LAT_OUTER}
    #define MAX_GROUP ${MAX_GROUP}
    #define GROUP_STRIDE ${GROUP_STRIDE}
    #define MAX_GRIDS_PER_PROTO ${MAX_GRIDS_PER_PROTO}
    // Large lattices (e.g. 17x17 x layers) need a high iter budget; 256 was
    // aborting traversal mid-tree and caused angle-dependent missing pins.
    #define MAX_BVH_STACK 64
    #define MAX_BVH_ITERS 8192
    #define INF 1.0e6
    #define NEG_INF -1.0e6
    #define LEAF_MARCH_STEPS 96

    uniform vec3 uCamPos, uCamForward, uCamRight, uCamUp;
    uniform float uTanHalfFov, uAspect;
    uniform int uSurfCount;
    uniform int uProtoCount;
    uniform sampler2D uTokenTex, uInstanceTex, uBvhTex, uSurfTex, uProtoTex, uGridTex, uLatTex;
    // Heights (rows) of the tables above; their widths are the *_TEX_W defines.
    uniform float uTokTexH, uInstTexH, uBvhTexH, uSurfTexH, uProtoTexH, uGridTexH, uLatTexH;
    uniform int uInstanceCount, uBvhNodeCount;
    uniform vec3 uSceneBMin, uSceneBMax;
    // Phase 1 (coincident faces). See epsAt()/sceneHit(). All three are set from JS.
    uniform float uEpsMin;        // depth-tolerance floor, scene-scaled (float32 noise guard)
    uniform float uPixelAngle;    // half a pixel, in radians: 0.5 * 2*tan(fov/2) / drawingBufferHeight
    uniform float uMarkOverlaps;  // >0.5 tints pixels where two solid cells genuinely overlap
    varying vec2 vUv;

    // ---- render-perf pass 1: switches, cost counters, shared per-ray state ------------------
    #define USE_TEXELFETCH ${SHADER_TEXELFETCH ? 1 : 0}
    uniform int uHeatMode;
    // HEATMAP is a MATERIAL DEFINE (see cycleHeat): 0 compiles every counter below away.
    #if HEATMAP
    int gNodes = 0, gLatSteps = 0, gGridSteps = 0, gTokEvals = 0;
    #define COUNT_NODE gNodes++;
    #define COUNT_LAT gLatSteps++;
    #define COUNT_GRID gGridSteps++;
    #define COUNT_TOK(n) gTokEvals += (n);
    #else
    #define COUNT_NODE
    #define COUNT_LAT
    #define COUNT_GRID
    #define COUNT_TOK(n)
    #endif
    vec3 heatRamp(float x) {   // jet-style: 0 = dark blue ... 1 = dark red
      x = clamp(x, 0.0, 1.0);
      return clamp(vec3(1.5 - abs(4.0 * x - 3.0), 1.5 - abs(4.0 * x - 2.0), 1.5 - abs(4.0 * x - 1.0)), 0.0, 1.0);
    }
    // 1 / rd of the PRIMARY (world-space) ray, near-zero components clamped exactly as boxInterval()
    // does. Set once in main(); used by boxIntervalInv(). Do not use it for local-frame rays.
    vec3 gInvRd;

    // Element idx of a linear table wrapped at width w (texels), h rows.
    #if USE_TEXELFETCH
    // Row via a float multiply (w is a compile-time constant, so this is one MAD; no integer divide,
    // which is slow on some GPUs). Exact: idx < ~1e5 keeps (idx + 0.5) / w well clear of a row edge.
    vec4 fetchLinear(sampler2D tex, int idx, float w, float h) {
      int row = int((float(idx) + 0.5) / w);
      return texelFetch(tex, ivec2(idx - row * int(w), row), 0);
    }
    #else
    vec4 fetchLinear(sampler2D tex, int idx, float w, float h) {
      float fi = float(idx);
      float row = floor((fi + 0.5) / w);
      return texture2D(tex, vec2((fi - row * w + 0.5) / w, (row + 0.5) / h));
    }
    #endif
    // Surface table: params = main vec4 (centre / radius); meta.x = type id (-1 unused),
    // meta.yz = extra scalars (torus b, c).
    vec4 surfParams(int i) { return fetchLinear(uSurfTex, 2 * i, SURF_TEX_W, uSurfTexH); }
    vec4 surfMeta(int i) { return fetchLinear(uSurfTex, 2 * i + 1, SURF_TEX_W, uSurfTexH); }
    // Prototype table.
    vec4 protoA(int p) { return fetchLinear(uProtoTex, 2 * p, PROTO_TEX_W, uProtoTexH); }
    vec4 protoB(int p) { return fetchLinear(uProtoTex, 2 * p + 1, PROTO_TEX_W, uProtoTexH); }
    vec3 protoColor(int p) { return protoA(p).rgb; }
    bool protoIsFill(int p) { return protoA(p).w > 0.5; }
    int protoTokenCount(int p) { return int(protoB(p).x + 0.5); }
    vec2 protoGrid(int p) { return protoB(p).yz; }   // (first grid, grid count)
    // Grid table: A = (origin.xy, pitch.xy), B = (nx, ny, envelope prototype, unused).
    vec4 gridA(int g) { return fetchLinear(uGridTex, 2 * g, GRID_TEX_W, uGridTexH); }
    vec4 gridB(int g) { return fetchLinear(uGridTex, 2 * g + 1, GRID_TEX_W, uGridTexH); }

    float boxSDF(vec3 p, vec3 bmin, vec3 bmax) {
      vec3 c = (bmin + bmax) * 0.5; vec3 h = (bmax - bmin) * 0.5;
      vec3 q = abs(p - c) - h;
      return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0);
    }
    // Ray vs AABB slab → (tEnter, tExit); empty if miss.
    // Guard against rd components near 0 (1/rd → Inf/NaN on some GPUs).
    vec2 boxInterval(vec3 ro, vec3 rd, vec3 bmin, vec3 bmax) {
      vec3 rdd = rd;
      if (abs(rdd.x) < 1e-8) rdd.x = (rdd.x >= 0.0) ? 1e-8 : -1e-8;
      if (abs(rdd.y) < 1e-8) rdd.y = (rdd.y >= 0.0) ? 1e-8 : -1e-8;
      if (abs(rdd.z) < 1e-8) rdd.z = (rdd.z >= 0.0) ? 1e-8 : -1e-8;
      vec3 inv = 1.0 / rdd;
      vec3 t0 = (bmin - ro) * inv;
      vec3 t1 = (bmax - ro) * inv;
      vec3 tsm = min(t0, t1);
      vec3 tlg = max(t0, t1);
      float tEnter = max(max(tsm.x, tsm.y), tsm.z);
      float tExit = min(min(tlg.x, tlg.y), tlg.z);
      if (tEnter > tExit) return vec2(INF, NEG_INF);
      return vec2(tEnter, tExit);
    }

    // boxInterval() for the PRIMARY ray (world frame), with 1/rd hoisted to once per pixel (gInvRd).
    // Same arithmetic, so results are bit-identical; the BVH walk calls this ~3x per node.
    // NOT for local-frame rays (rotated instances, torus): gInvRd belongs to the world ray.
    vec2 boxIntervalInv(vec3 ro, vec3 bmin, vec3 bmax) {
      vec3 t0 = (bmin - ro) * gInvRd;
      vec3 t1 = (bmax - ro) * gInvRd;
      vec3 tsm = min(t0, t1);
      vec3 tlg = max(t0, t1);
      float tEnter = max(max(tsm.x, tsm.y), tsm.z);
      float tExit = min(min(tlg.x, tlg.y), tlg.z);
      if (tEnter > tExit) return vec2(INF, NEG_INF);
      return vec2(tEnter, tExit);
    }

    // Surface type id of slot idx. floor(x + 0.5) rather than int(x + 0.5): int() truncates
    // toward zero, which would turn the "unused" id -1 into 0 (plane_x).
    int surfTypeOf(int idx) { return int(floor(surfMeta(idx).x + 0.5)); }

    // ---- Torus (type 8) helpers -------------------------------------------------
    // Field: f = (|(q_r, q_z)| - 1) * min(b, c), q_r = (rho - a)/b, q_z = dz/c.
    // f <= 0 is inside. It is exactly the Euclidean distance for a circular tube
    // (b == c) and a 1-Lipschitz LOWER BOUND on distance for an elliptical one, so
    // |f| is always a safe sphere-tracing step in both cases.
    float torusF(vec3 p, vec4 pr, vec2 bc) {
      vec3 d = p - pr.xyz;
      vec2 q = vec2((length(d.xy) - pr.w) / bc.x, d.z / bc.y);
      return (length(q) - 1.0) * min(bc.x, bc.y);
    }

    #define TORUS_MARCH_STEPS 72
    // The march always covers the window in at most TORUS_MIN_STRIDES strides (see below).
    #define TORUS_MIN_STRIDES 64.0
    #define TORUS_BISECT_STEPS 20
    // Up to four ray/torus crossings (ascending t) inside the window [tA, tB]; unused
    // slots hold INF. A ray can cross a torus 4 times (through both tube walls twice), so
    // returning only 2 would lose the far wall when a cell clips the near one away.
    //
    // WHY NOT A QUARTIC SOLVE: the closed form is badly conditioned in float32 at
    // reactor scale (a ~ 600 cm, b ~ 100 cm gives coefficients ~1e12 with catastrophic
    // cancellation). Instead we march with the distance bound: stepping |f| + delta
    // can never jump OVER a crossing (|f| bounds the distance to the surface) and the
    // +delta overshoot guarantees we step THROUGH it so the sign change is detected;
    // each bracket is then bisected. delta is half the scene's depth tolerance, so
    // features thinner than that are below rendering resolution anyway.
    //
    // GRAZING RAYS: |f| shrinks geometrically for a ray skimming a surface (each step
    // is only ~|f|*cos(angle) of real progress), so a pure distance-bound march burns its
    // whole step budget before reaching the far wall and silently loses the LAST
    // crossing (measured on a CPU port: ~10% of random rays lost an exit crossing).
    // Fix: every step is at least window/TORUS_MIN_STRIDES long, so the march ALWAYS
    // covers the window within TORUS_MARCH_STEPS iterations. Trade-off: where that
    // minimum stride is the one used, a tube chord shorter than a stride can be skipped,
    // but that only erodes the silhouette by ~stride^2/(8 r) (about 0.4% of the tube
    // radius at 64 strides), well under a pixel.
    vec4 torusCrossings(vec3 ro, vec3 rd, vec4 pr, vec2 bc, float tA, float tB) {
      vec4 res = vec4(INF);
      // Clip to the torus bounding box first: outside it nothing can be crossed.
      float reach = abs(pr.w) + bc.x;
      vec2 span = boxInterval(ro, rd, pr.xyz - vec3(reach, reach, bc.y), pr.xyz + vec3(reach, reach, bc.y));
      // Pad the window by one depth tolerance on each side. A ray that touches the torus
      // exactly where it enters the bounding box (the tube's extreme circle) would
      // otherwise START the march "inside" by float noise and never register that entry.
      float t = max(tA, span.x - uEpsMin);
      float tEnd = min(tB, span.y + uEpsMin);
      if (span.x > span.y || t >= tEnd) return res;
      float delta = 0.5 * uEpsMin;
      float minStep = (tEnd - t) / TORUS_MIN_STRIDES;
      int n = 0;
      float fPrev = torusF(ro + rd * t, pr, bc);
      for (int i = 0; i < TORUS_MARCH_STEPS; i++) {
        if (t >= tEnd || n >= 4) break;
        float tNext = min(t + max(abs(fPrev) + delta, minStep), tEnd);
        float fNext = torusF(ro + rd * tNext, pr, bc);
        if ((fPrev <= 0.0) != (fNext <= 0.0)) {
          float lo = t, hi = tNext;
          bool loIn = fPrev <= 0.0;
          for (int j = 0; j < TORUS_BISECT_STEPS; j++) {
            float mid = 0.5 * (lo + hi);
            if ((torusF(ro + rd * mid, pr, bc) <= 0.0) == loIn) lo = mid; else hi = mid;
          }
          float tc = 0.5 * (lo + hi);
          // vec4 components cannot be indexed by a loop-varying int in GLSL ES 1.00.
          if (n == 0) res.x = tc; else if (n == 1) res.y = tc; else if (n == 2) res.z = tc; else res.w = tc;
          n++;
        }
        t = tNext; fPrev = fNext;
      }
      return res;
    }

    // ---- Quadratic helpers (cone_z, type 7) ---------------------------------------
    // In the cone's local frame the surface is q(t) = a t^2 + b t + c = 0 with
    //   a = dx^2 + dy^2 - r2 dz^2 (ray direction), b = 2 (ox dx + oy dy - r2 oz dz), c = same in o.
    // Inside <=> q <= 0. a > 0: inside is the span between the roots (ray flatter than the
    // cone wall). a < 0: inside is OUTSIDE the roots, i.e. two half-lines, which one
    // interval cannot hold.
    vec3 coneQuad(vec3 ro, vec3 rd, vec4 pr, out float a) {
      vec3 o = ro - pr.xyz;
      a = rd.x * rd.x + rd.y * rd.y - pr.w * rd.z * rd.z;
      float b = 2.0 * (o.x * rd.x + o.y * rd.y - pr.w * o.z * rd.z);
      float c = o.x * o.x + o.y * o.y - pr.w * o.z * o.z;
      return vec3(a, b, c);
    }
    // Hull interval of {t : a t^2 + b t + c <= 0}. For a < 0 with real roots the true set
    // is two half-lines, whose hull is the whole line; that is only a SEED (see
    // evalIntervalEntry), and the exact crossings below are what leafExact relies on.
    vec2 quadInsideHull(float a, float b, float c) {
      if (abs(a) < 1e-9) {
        if (abs(b) < 1e-12) return c <= 0.0 ? vec2(NEG_INF, INF) : vec2(INF, NEG_INF);
        float tl = -c / b;
        return b > 0.0 ? vec2(NEG_INF, tl) : vec2(tl, INF);
      }
      float disc = b * b - 4.0 * a * c;
      if (disc < 0.0) return a < 0.0 ? vec2(NEG_INF, INF) : vec2(INF, NEG_INF);
      float sq = sqrt(disc);
      float r0 = (-b - sq) / (2.0 * a), r1 = (-b + sq) / (2.0 * a);
      return a > 0.0 ? vec2(min(r0, r1), max(r0, r1)) : vec2(NEG_INF, INF);
    }
    // The (up to two) real roots of the quadratic, ascending; INF when absent.
    vec4 quadCrossings(float a, float b, float c) {
      if (abs(a) < 1e-9) {
        if (abs(b) < 1e-12) return vec4(INF);
        return vec4(-c / b, INF, INF, INF);
      }
      float disc = b * b - 4.0 * a * c;
      if (disc < 0.0) return vec4(INF);
      float sq = sqrt(disc);
      float r0 = (-b - sq) / (2.0 * a), r1 = (-b + sq) / (2.0 * a);
      return vec4(min(r0, r1), max(r0, r1), INF, INF);
    }

    float surfaceSDF(int idx, vec3 p) {
      if (idx < 0) return INF;
      int t = surfTypeOf(idx); vec4 pr = surfParams(idx);
      if (t == 0) return p.x - pr.x;
      if (t == 1) return p.y - pr.y;
      if (t == 2) return p.z - pr.z;
      if (t == 3) return length(vec2(p.y - pr.y, p.z - pr.z)) - pr.w;
      if (t == 4) return length(vec2(p.x - pr.x, p.z - pr.z)) - pr.w;
      if (t == 5) return length(vec2(p.x - pr.x, p.y - pr.y)) - pr.w;
      if (t == 6) return length(p - pr.xyz) - pr.w;
      if (t == 7) {
        // Double-napped cone: inside <=> rho <= k |dz| with k = sqrt(r2). The field
        // (rho - k|dz|) / sqrt(1 + k^2) is the distance to the nearer nappe's generatrix
        // LINE, which never exceeds the true distance to the cone (the nappe is only a
        // ray of that line, and the other nappe is farther), so it is a safe lower bound
        // for sphere tracing. Its gradient at the surface is the surface normal, which is
        // all calcNormal needs. Not smooth at the apex (|dz|); harmless.
        vec3 d = p - pr.xyz;
        return (length(d.xy) - sqrt(pr.w) * abs(d.z)) * inversesqrt(1.0 + pr.w);
      }
      if (t == 8) return torusF(p, pr, surfMeta(idx).yz);
      return INF;
    }

    // Analytic ray ∩ inside-of-surface → (tEnter, tExit). Empty if miss.
    vec2 surfaceInterval(int idx, vec3 ro, vec3 rd) {
      if (idx < 0) return vec2(INF, NEG_INF);
      int t = surfTypeOf(idx); vec4 pr = surfParams(idx);
      // Planes: half-space surfaceSDF <= 0 is "inside"
      if (t == 0) {
        float denom = rd.x;
        if (abs(denom) < 1e-8) {
          return (ro.x - pr.x) <= 0.0 ? vec2(NEG_INF, INF) : vec2(INF, NEG_INF);
        }
        float th = (pr.x - ro.x) / denom;
        return denom > 0.0 ? vec2(NEG_INF, th) : vec2(th, INF);
      }
      if (t == 1) {
        float denom = rd.y;
        if (abs(denom) < 1e-8) {
          return (ro.y - pr.y) <= 0.0 ? vec2(NEG_INF, INF) : vec2(INF, NEG_INF);
        }
        float th = (pr.y - ro.y) / denom;
        return denom > 0.0 ? vec2(NEG_INF, th) : vec2(th, INF);
      }
      if (t == 2) {
        float denom = rd.z;
        if (abs(denom) < 1e-8) {
          return (ro.z - pr.z) <= 0.0 ? vec2(NEG_INF, INF) : vec2(INF, NEG_INF);
        }
        float th = (pr.z - ro.z) / denom;
        return denom > 0.0 ? vec2(NEG_INF, th) : vec2(th, INF);
      }
      // cone_z: a single interval cannot represent a double-napped cone in general (see
      // quadInsideHull), so this returns a conservative HULL. Exact crossings come from
      // surfaceCrossings(), which is what leafExact() uses.
      if (t == 7) {
        float qa; vec3 q = coneQuad(ro, rd, pr, qa);
        return quadInsideHull(q.x, q.y, q.z);
      }
      // torus: the inside is up to two intervals along a ray (two tube walls); return the
      // hull (first entry .. last exit) found by the crossing march. A hull only ever
      // starts EARLY, which is the safe direction for the entry seed.
      if (t == 8) {
        vec4 cr = torusCrossings(ro, rd, pr, surfMeta(idx).yz, NEG_INF, INF);
        if (cr.x >= INF * 0.5) return vec2(INF, NEG_INF);
        float last = cr.x;
        if (cr.y < INF * 0.5) last = cr.y;
        if (cr.z < INF * 0.5) last = cr.z;
        if (cr.w < INF * 0.5) last = cr.w;
        return vec2(cr.x, last);
      }
      // Infinite cylinders + sphere: quadratic in the perpendicular plane / 3D
      vec3 o, d; float r;
      if (t == 3) { o = vec3(0.0, ro.y - pr.y, ro.z - pr.z); d = vec3(0.0, rd.y, rd.z); r = pr.w; }
      else if (t == 4) { o = vec3(ro.x - pr.x, 0.0, ro.z - pr.z); d = vec3(rd.x, 0.0, rd.z); r = pr.w; }
      else if (t == 5) { o = vec3(ro.x - pr.x, ro.y - pr.y, 0.0); d = vec3(rd.x, rd.y, 0.0); r = pr.w; }
      else if (t == 6) { o = ro - pr.xyz; d = rd; r = pr.w; }
      else return vec2(INF, NEG_INF);
      float a = dot(d, d);
      if (a < 1e-12) {
        return dot(o, o) <= r * r ? vec2(NEG_INF, INF) : vec2(INF, NEG_INF);
      }
      float b = 2.0 * dot(o, d);
      float c = dot(o, o) - r * r;
      float disc = b * b - 4.0 * a * c;
      if (disc < 0.0) return vec2(INF, NEG_INF);
      float s = sqrt(disc);
      float t0 = (-b - s) / (2.0 * a);
      float t1 = (-b + s) / (2.0 * a);
      return vec2(min(t0, t1), max(t0, t1));
    }

    // Every point where the ray crosses surface idx's boundary inside the window [tA, tB],
    // ascending, INF in unused slots. This is what leafExact() consumes. It is NOT the
    // same thing as surfaceInterval(): an interval answers "where is the ray inside", and
    // for the double-napped cone and the torus that is not a single span, whereas the
    // crossings are always exactly the boundary points the entering-test needs.
    vec4 surfaceCrossings(int idx, vec3 ro, vec3 rd, float tA, float tB) {
      if (idx < 0) return vec4(INF);
      int t = surfTypeOf(idx); vec4 pr = surfParams(idx);
      if (t == 7) { float qa; vec3 q = coneQuad(ro, rd, pr, qa); return quadCrossings(q.x, q.y, q.z); }
      if (t == 8) return torusCrossings(ro, rd, pr, surfMeta(idx).yz, tA, tB);
      vec2 iv = surfaceInterval(idx, ro, rd);   // planes / cylinders / sphere: <= 2 crossings
      return vec4(iv.x, iv.y, INF, INF);
    }

    vec2 fetchToken(int protoIdx, int pairIdx) {
    #if USE_TEXELFETCH
      return texelFetch(uTokenTex, ivec2(pairIdx, protoIdx), 0).rg;
    #else
      return texture2D(uTokenTex, vec2((float(pairIdx)+0.5)/TOK_TEX_W, (float(protoIdx)+0.5)/uTokTexH)).rg;
    #endif
    }
    // Instance i is two texels: 2i = (offset.xyz, first prototype + GROUP_STRIDE * layer count),
    // 2i+1 = rotation quaternion (x, y, z, w), local -> world.
    vec4 fetchInstance(int instIdx) {
      return fetchLinear(uInstanceTex, 2 * instIdx, INST_TEX_W, uInstTexH);
    }
    vec4 fetchInstanceQuat(int instIdx) {
      return fetchLinear(uInstanceTex, 2 * instIdx + 1, INST_TEX_W, uInstTexH);
    }
    // v + 2 u x (u x v + w v)   (unit quaternion; rotates local -> world)
    vec3 qRot(vec4 q, vec3 v) { vec3 u = q.xyz; return v + 2.0 * cross(u, cross(u, v) + q.w * v); }
    // inverse rotation = conjugate (world -> local)
    vec3 qRotInv(vec4 q, vec3 v) { return qRot(vec4(-q.xyz, q.w), v); }
    // World point -> the instance's local frame (rigid, so ray parameters t are unchanged).
    vec3 instToLocal(int instIdx, vec3 pWorld) {
      return qRotInv(fetchInstanceQuat(instIdx), pWorld - fetchInstance(instIdx).xyz);
    }
    // A hit's placement is (instIdx, off): a normal instance (instIdx >= 0, off = its translation)
    // or a lattice pin (instIdx = -1: identity rotation, off = the pin's offset from its prototype).
    vec4 quatOf(int instIdx) { return instIdx < 0 ? vec4(0.0, 0.0, 0.0, 1.0) : fetchInstanceQuat(instIdx); }
    vec3 candToLocal(int instIdx, vec3 off, vec3 pWorld) { return qRotInv(quatOf(instIdx), pWorld - off); }
    // Lattice table: LAT_STRIDE texels per lattice (layout in latticeGrid.ts, packLatticeGrid).
    vec4 latT(int l, int k) { return fetchLinear(uLatTex, l * LAT_STRIDE + k, LAT_TEX_W, uLatTexH); }
    vec4 fetchBvhTexel(int nodeIdx, int col) {
      return fetchLinear(uBvhTex, nodeIdx * 3 + col, BVH_TEX_W, uBvhTexH);
    }
    float evalTokens(int protoIdx, vec3 pLocal) {
      int tokenCount = protoTokenCount(protoIdx);
      COUNT_TOK(tokenCount)
      // Deep AND-chains (fill cell vs many pins) need more than 16 slots.
      float stack[32]; int sp = 0;
      for (int i = 0; i < MAX_TOKEN_ITERS; i++) {
        if (i >= tokenCount) break;
        if (sp >= 31) break;
        vec2 tok = fetchToken(protoIdx, i);
        int op = int(tok.x + 0.5); int operand = int(tok.y + 0.5);
        if (op == 0) { stack[sp++] = surfaceSDF(operand, pLocal); }
        else if (op == 1) { stack[sp++] = -surfaceSDF(operand, pLocal); }
        else if (op == 2) { float b=stack[--sp]; float a=stack[--sp]; stack[sp++]=max(a,b); }
        else if (op == 3) { float b=stack[--sp]; float a=stack[--sp]; stack[sp++]=min(a,b); }
        else if (op == 4) { stack[sp-1] = -stack[sp-1]; }
      }
      return sp > 0 ? stack[0] : INF;
    }

    // Signed distance (<= 0 inside) to the pins of grid g. A grid is a regular xy
    // array, so only the pin nearest the point matters: fold the point into that
    // grid cell and test a single pin. Cost is independent of the pin count.
    float gridSDF(int g, vec3 p) {
      vec4 ga = gridA(g); vec4 gb = gridB(g);
      vec2 c = clamp(floor((p.xy - ga.xy) / ga.zw + 0.5), vec2(0.0), gb.xy - 1.0);
      return evalTokens(int(gb.z + 0.5), p - vec3(c * ga.zw, 0.0));
    }

    // A cell's region: its tokens, minus the pins of its grids (max(a, -b) = a AND NOT b).
    float evalRegion(int protoIdx, vec3 pLocal) {
      float d = evalTokens(protoIdx, pLocal);
      vec2 gr = protoGrid(protoIdx);
      int gn = int(gr.y + 0.5);
      if (gn > 0) {
        int g0 = int(gr.x + 0.5);
        for (int k = 0; k < MAX_GRIDS_PER_PROTO; k++) {
          if (k >= gn) break;
          d = max(d, -gridSDF(g0 + k, pLocal));
        }
      }
      return d;
    }

    // Phase B: interval CSG along ray in local frame → entry t (or INF)
    float evalIntervalEntry(int protoIdx, vec3 ro, vec3 rd) {
      int tokenCount = protoTokenCount(protoIdx);
      vec2 stack[32]; int sp = 0;
      for (int i = 0; i < MAX_TOKEN_ITERS; i++) {
        if (i >= tokenCount) break;
        if (sp >= 31) break;
        vec2 tok = fetchToken(protoIdx, i);
        int op = int(tok.x + 0.5); int operand = int(tok.y + 0.5);
        if (op == 0) { stack[sp++] = surfaceInterval(operand, ro, rd); }
        else if (op == 1) {
          // Outside = complement of inside interval, clipped to ray domain
          vec2 inn = surfaceInterval(operand, ro, rd);
          int st = surfTypeOf(operand);
          if (st == 0 || st == 1 || st == 2) {
            // Planes: surfaceInterval always returns an exact unbounded
            // half-line, either (-INF, th) or (th, INF), depending on the
            // sign of the ray direction along that axis. The complement is
            // therefore *exactly* the other half-line — swap, don't guess.
            // (Previously this always assumed the (-INF, th) case and
            // returned (th, INF), which is wrong whenever the ray travels
            // in the negative direction along that axis — e.g. looking at
            // a box wall from the far side along -x/-y/-z. That produced a
            // bogus (INF, INF) token which usually got masked by the
            // tHit-fallback-to-tStart safety net below, but wasn't always:
            // fix it properly instead of relying on the net.)
            if (inn.x <= NEG_INF + 1.0) { stack[sp++] = vec2(inn.y, INF); }
            else if (inn.y >= INF - 1.0) { stack[sp++] = vec2(NEG_INF, inn.x); }
            else { stack[sp++] = vec2(NEG_INF, INF); } // total miss → outside everywhere
          } else if (inn.x > inn.y) {
            stack[sp++] = vec2(NEG_INF, INF);
          } else {
            // Bounded shapes (cylinders/spheres): true complement is two
            // pieces, (-INF, tEnter) U (tExit, +INF), which can't be
            // represented as one interval. This used to guess "beyond
            // exit" — reasoning that within one object's own concentric
            // layers (e.g. Outside(gap_outer) AND Inside(clad_outer) for a
            // cladding shell) the ray would already be past the inner
            // surface by the time this token is seeded.
            //
            // That's backwards for the overwhelmingly common case: a fresh
            // camera ray approaching the pin from outside for the first
            // time. For such a ray, order along t is cladEntry < gapEntry
            // < pelletEntry < pelletExit < gapExit < cladExit — so "beyond
            // gap's exit" AND'ed with "inside clad" seeds the CLAD leaf at
            // t=gapExit (deep on the far side of the shell) instead of the
            // true near surface at cladEntry. Meanwhile pellet's own token
            // is a plain Inside() with an exact seed at t=pelletEntry — and
            // since pelletEntry < gapExit, pellet's (correct, but
            // physically farther) hit comes out *nearer* than clad's/gap's
            // (bogus, further) one, so the pellet wins the nearest-hit
            // race and renders in front of its own cladding. (Symptom:
            // fuel visible straight through the side of a pin, cladding
            // only showing correctly on straight-down rays where this
            // token never gets exercised.)
            // Contribute no information instead of a wrong guess — the
            // exact SDF march below still finds the true surface either
            // way, just without the head start.
            stack[sp++] = vec2(NEG_INF, INF);
          }
        }
        else if (op == 2) { // AND = intersection of intervals
          vec2 B = stack[--sp]; vec2 A = stack[--sp];
          stack[sp++] = vec2(max(A.x, B.x), min(A.y, B.y));
        }
        else if (op == 3) { // OR = union (single-interval hull — exact for disjoint is hard)
          vec2 B = stack[--sp]; vec2 A = stack[--sp];
          if (A.x > A.y) { stack[sp++] = B; }
          else if (B.x > B.y) { stack[sp++] = A; }
          else stack[sp++] = vec2(min(A.x, B.x), max(A.y, B.y));
        }
        else if (op == 4) {
          // NOT of a compound sub-region (e.g. Subtraction(a, b) compiles to
          // "a AND NOT(b)", where b's own tokens already form a proper
          // bounded interval — a whole box, not just one plane). Same bug
          // as the bounded-shape Outside() case above, just one level up:
          // guessing "beyond b's exit" is wrong for the common case of a
          // fresh ray hitting the outer shape (a) before ever reaching the
          // subtracted region (b) — e.g. looking into a box with a notch
          // cut partway into it ("incision"). The ray reaches the notch's
          // near wall at NOT(b)'s true near piece (-INF, bEntry), but this
          // used to seed at (bExit, INF) instead — deep on the far side of
          // the subtracted volume — skipping the incision's visible wall
          // entirely and reporting whatever's further along (or nothing).
          // Contribute no information instead of a wrong guess.
          stack[sp-1] = vec2(NEG_INF, INF);
        }
      }
      if (sp <= 0) return INF;
      vec2 iv = stack[0];
      if (iv.x > iv.y) return INF;
      // Closest non-negative entry
      float tEnter = iv.x;
      if (tEnter < 0.0) {
        if (iv.y < 0.0) return INF;
        tEnter = 0.0; // ray origin inside solid
      }
      return tEnter;
    }

    // ======================================================================
    // Hit tolerance + coincident-face (tie) handling  -- Phase 1
    // ======================================================================
    // PROBLEM: when two cells share a face (adjacent boxes, pellet|gap|clad
    // rings on the same z-plane, box wall == fill wall) both leaves "hit" at
    // the same depth. The old code kept whichever leaf the BVH visited first,
    // so the winner depended on camera angle (shimmering seams). On top of
    // that the march stop test (d < 0.002) dilates every cell by ~0.002, so
    // two neighbours overlap in a thin sliver and BOTH report a hit there.
    //
    // FIX, in three parts:
    //  1. epsAt(): the tolerance is half a pixel of world-space footprint at
    //     distance t (floored by uEpsMin) instead of a hard-coded 0.002, so it
    //     scales with zoom and scene size.
    //  2. Hits whose depths differ by < tieAt(t) are a TIE. All tied leaves are
    //     kept as candidates instead of "first found wins".
    //  3. Ties are resolved by EXACT containment: sample every candidate's
    //     region (no dilation) at a point just past the hit, and pick the cell
    //     that genuinely contains it. If several contain it (a real overlap),
    //     solid cells beat the plane-only fill stand-in, then the later-defined
    //     cell (higher prototype index) wins, and the pixel is flagged so it
    //     can be tinted (uMarkOverlaps).
    // Result: deterministic, camera-angle independent, no dilation slivers.
    float epsAt(float t) { return max(uEpsMin, t * uPixelAngle); }
    float tieAt(float t) { return 2.0 * epsAt(t); }
    #define MAX_CAND 8
    // Probe depth past the hit, in units of eps. leafExact() returns the EXACT crossing
    // depth, so the probe only has to clear float noise, and every extra bit of depth
    // drifts the probe sideways on tilted rays (misattributing boundary pixels by
    // ~PROBE_MUL*eps*sin(tilt)). On a CPU port of this logic 0.1 gave zero order-
    // dependence and ~1.5% wrong-owner rays (all within noise of a boundary), vs ~45%
    // at the 1.25 that the old sphere-trace needed to clear its own stopping error.
    // NOTE: the oversized-prototype march fallback has up to ~1 eps stopping error, so
    // ties between two such cells can still misattribute at tilted angles.
    #define PROBE_MUL 0.1

    // ======================================================================
    // Exact leaf hit (Phase 2) -- replaces sphere-tracing for ordinary cells
    // ======================================================================
    // PROBLEM: the old leaf hit sphere-traced region SDF with a 96-step cap.
    // Inside a narrow, deep cavity (a pin-cell shaft, a slot, a hole) the rays
    // run almost parallel to the walls, so each step (= distance to the
    // nearest wall) barely advances and convergence is geometric with a ratio
    // of (1 - cos(angle to wall normal)) ~ 1. The cap is hit before the ray
    // reaches the wall, the leaf reports "no hit", and the wall / floor
    // vanishes (showing whatever lies behind, or the background).
    //
    // FIX: a cell's boundary can only be entered at a ray/surface crossing of
    // one of ITS OWN surfaces, and every surface type we render is analytic.
    // So: intersect the ray with each surface in the token stream (<= 2 t's
    // each), and take the nearest crossing where the region flips from
    // outside to inside. Cost is O(tokens^2) per leaf but independent of ray
    // angle, so it cannot run out of steps, and the returned t is exact (no
    // early-stop error), which also makes Phase-1 ties cleaner.
    //
    // Falls back to the old march only for oversized token streams (> MAX_EXACT_TOKENS),
    // where trying every crossing would be too expensive.
    #define MAX_EXACT_TOKENS ${MAX_EXACT_TOKENS}
    // Entries into a cell through the pins of one of its grids. The pins' surfaces are
    // too many to test one by one, so walk the ray through the grid cells it crosses
    // (a 2D DDA) and test only the pin in each: O(cells crossed), not O(pins).
    #define MAX_GRID_STEPS 192
    float gridEntry(int protoIdx, int g, vec3 roL, vec3 rd, float tStart, float tEnd, float best) {
      vec4 ga = gridA(g); vec4 gb = gridB(g);
      int env = int(gb.z + 0.5);
      int envTokens = min(protoTokenCount(env), MAX_EXACT_TOKENS);
      vec2 pit = ga.zw; vec2 cnt = gb.xy;
      float pad = 4.0 * uEpsMin;
      vec2 gLo = ga.xy - 0.5 * pit;            // lower corner of grid cell [0, 0]
      vec2 lo = gLo - pad; vec2 hi = gLo + cnt * pit + pad;
      float tA = tStart; float tB = min(tEnd, best);
      // Clip the ray to the grid's xy footprint.
      if (abs(rd.x) < 1.0e-9) { if (roL.x < lo.x || roL.x > hi.x) return best; }
      else { float a = (lo.x - roL.x) / rd.x; float b = (hi.x - roL.x) / rd.x; tA = max(tA, min(a, b)); tB = min(tB, max(a, b)); }
      if (abs(rd.y) < 1.0e-9) { if (roL.y < lo.y || roL.y > hi.y) return best; }
      else { float a = (lo.y - roL.y) / rd.y; float b = (hi.y - roL.y) / rd.y; tA = max(tA, min(a, b)); tB = min(tB, max(a, b)); }
      if (tA >= tB) return best;

      vec2 cell = clamp(floor((roL.xy + rd.xy * tA - gLo) / pit), vec2(0.0), cnt - 1.0);
      vec2 stp = vec2(rd.x >= 0.0 ? 1.0 : -1.0, rd.y >= 0.0 ? 1.0 : -1.0);
      vec2 nextB = gLo + (cell + vec2(rd.x >= 0.0 ? 1.0 : 0.0, rd.y >= 0.0 ? 1.0 : 0.0)) * pit;
      vec2 tMax = vec2(abs(rd.x) < 1.0e-9 ? INF : (nextB.x - roL.x) / rd.x,
                       abs(rd.y) < 1.0e-9 ? INF : (nextB.y - roL.y) / rd.y);
      vec2 tDelta = vec2(abs(rd.x) < 1.0e-9 ? INF : pit.x / abs(rd.x),
                         abs(rd.y) < 1.0e-9 ? INF : pit.y / abs(rd.y));
      float tCur = tA;
      for (int s = 0; s < MAX_GRID_STEPS; s++) {
        COUNT_GRID
        float tNext = min(min(tMax.x, tMax.y), tB);
        // This grid cell's pin is the shared envelope shifted by cell * pitch.
        vec3 roP = roL - vec3(cell * pit, 0.0);
        float wLo = max(tCur - pad, tStart);
        // EXACT early-out (render-perf pass 1). Cells are visited in increasing t, so this and every
        // later window starts at >= wLo, and the crossing filter below rejects tc >= best. Once the
        // walk has passed best, nothing can improve it. Before this, a hit found early still left
        // the walk running to the far side of the grid (every remaining cell paid its full token loop).
        if (wLo >= best) break;
        float wHi = min(tNext + pad, min(tEnd, best));
        for (int i = 0; i < MAX_EXACT_TOKENS; i++) {
          if (i >= envTokens) break;
          vec2 tok = fetchToken(env, i);
          if (tok.x > 1.5) continue;
          int sIdx = int(tok.y + 0.5);
          if (sIdx < 0) continue;
          vec4 cr = surfaceCrossings(sIdx, roP, rd, wLo, wHi);
          for (int k = 0; k < 4; k++) {
            float tc = (k == 0) ? cr.x : (k == 1) ? cr.y : (k == 2) ? cr.z : cr.w;
            if (abs(tc) >= INF * 0.5 || tc < wLo || tc >= min(wHi, best)) continue;
            float dl = max(0.1 * epsAt(tc), 1.0e-6);
            if (evalRegion(protoIdx, roL + rd * (tc - dl)) > 0.0 &&
                evalRegion(protoIdx, roL + rd * (tc + dl)) <= 0.0) best = tc;
          }
        }
        if (tNext >= tB) break;
        if (tMax.x < tMax.y) { cell.x += stp.x; tMax.x += tDelta.x; }
        else { cell.y += stp.y; tMax.y += tDelta.y; }
        if (cell.x < 0.0 || cell.y < 0.0 || cell.x >= cnt.x || cell.y >= cnt.y) break;
        tCur = tNext;
      }
      return best;
    }

    // Returns the first t in [tStart, tEnd) where the ray enters the region, else INF.
    float leafExact(int protoIdx, vec3 roL, vec3 rd, float tStart, float tEnd) {
      // Ray already inside the cell at the window start (camera inside geometry).
      if (evalRegion(protoIdx, roL + rd * tStart) <= 0.0) return tStart;
      int tokenCount = protoTokenCount(protoIdx);
      float best = INF;
      for (int i = 0; i < MAX_EXACT_TOKENS; i++) {
        if (i >= tokenCount) break;
        vec2 tok = fetchToken(protoIdx, i);
        int op = int(tok.x + 0.5);
        if (op > 1) continue;                    // only Inside/Outside tokens name a surface
        int sIdx = int(tok.y + 0.5);
        if (sIdx < 0) continue;
        // Up to 4 crossings (a torus can have 4; everything else <= 2). Unused slots and
        // open-ended half-lines are +/-INF and are skipped just below.
        vec4 cr = surfaceCrossings(sIdx, roL, rd, tStart, min(tEnd, best));
        for (int k = 0; k < 4; k++) {
          float tc = (k == 0) ? cr.x : (k == 1) ? cr.y : (k == 2) ? cr.z : cr.w;
          if (abs(tc) >= INF * 0.5 || tc < tStart || tc >= min(tEnd, best)) continue;
          // Entering test: outside just before the crossing, inside just after.
          // Rejects crossings of internal faces that are not part of the cell boundary.
          // dl is a fraction of a pixel, so thinner-than-that slivers are skipped.
          float dl = max(0.1 * epsAt(tc), 1.0e-6);
          if (evalRegion(protoIdx, roL + rd * (tc - dl)) > 0.0 &&
              evalRegion(protoIdx, roL + rd * (tc + dl)) <= 0.0) best = tc;
        }
      }
      vec2 gr = protoGrid(protoIdx);
      for (int k = 0; k < MAX_GRIDS_PER_PROTO; k++) {
        if (k >= int(gr.y + 0.5)) break;
        best = gridEntry(protoIdx, int(gr.x + 0.5) + k, roL, rd, tStart, tEnd, best);
      }
      return best;
    }

    // ======================================================================================
    // Hit bookkeeping shared by the BVH instance leaves and the lattice leaf.
    // ======================================================================================
    // Candidates are every hit within tieAt() of the nearest one; ties are resolved by
    // containment at the end of sceneHit(). Globals (not locals) so considerHit() can be shared.
    float tBest;                    // nearest hit so far (tie group is [tBest, tBest + tieAt])
    float candT[MAX_CAND]; int candProto[MAX_CAND]; int candInst[MAX_CAND]; vec3 candOff[MAX_CAND];
    int candCount;

    // Offer one hit: (depth, prototype, instance index or -1, translation of its frame).
    void considerHit(float tHit, int protoIdx, int instIdx, vec3 off) {
      float tTolM = (tBest < INF) ? tieAt(tBest) : 0.0;
      // Clearly nearer than the whole current group -> start a new group.
      if (tHit < tBest - tTolM) { candCount = 0; tBest = tHit; }
      // Within the tie window -> keep as a candidate (never first-wins).
      if (tHit <= tBest + tTolM) {
        int slot = candCount;
        if (candCount >= MAX_CAND) {
          // Full: evict the farthest candidate if this one is nearer.
          slot = 0; float worst = candT[0];
          for (int c = 1; c < MAX_CAND; c++) { if (candT[c] > worst) { worst = candT[c]; slot = c; } }
          if (tHit >= worst) slot = -1;
        } else {
          candCount++;
        }
        if (slot >= 0) { candT[slot] = tHit; candProto[slot] = protoIdx; candInst[slot] = instIdx; candOff[slot] = off; }
        tBest = min(tBest, tHit);
      }
    }

    // ======================================================================================
    // Concentric-cylinder pin stack: closed-form layer entry (replaces leafExact for such pins).
    // ======================================================================================
    // A FuelPin's layers (pellet, gap, clad ...) are { rho in (r[m-1], r[m]] } AND { z in (zLo, zHi] }.
    // leafExact() reached the same answer by interpreting the layer's token stream: a texture fetch
    // per token, a crossing solve per surface, and two evalRegion() calls (each a full token walk)
    // per crossing. That dominated hit cost in a lattice. Here each radius costs ONE quadratic,
    // shared by the two layers it bounds, and the axial planes one slab. No token fetches at all.
    // Set per lattice by latticeVisit() (all zero radius = no stack, generic path):
    vec4 gStackAxial;      // (cx, cy, zLo, zHi) in the prototype frame
    vec4 gStackR0, gStackR1;   // radii of layers 0..3 and 4..7
    float stackR(int m) {
      vec4 v = (m < 4) ? gStackR0 : gStackR1;
      int k = m - 4 * (m / 4);
      return (k == 0) ? v.x : (k == 1) ? v.y : (k == 2) ? v.z : v.w;
    }
    // Ray vs the disc rho <= r about the axis: (enter, exit); (INF, NEG_INF) = miss.
    vec2 discInterval(float a, float b, float cc, float r) {
      float c = cc - r * r;
      if (a < 1.0e-12) return (c <= 0.0) ? vec2(NEG_INF, INF) : vec2(INF, NEG_INF);
      float disc = b * b - a * c;
      if (disc < 0.0) return vec2(INF, NEG_INF);
      float sq = sqrt(disc);
      return vec2((-b - sq) / a, (-b + sq) / a);
    }
    // One piece [g0, g1] of a layer along the ray -> entry depth in the window [wLo, wH), or INF.
    // Same contract as leafExact(): a ray already inside at wLo hits at wLo; a layer thinner than
    // a fraction of a pixel (the sliver test) is skipped.
    float stackPiece(float g0, float g1, float wLo, float wH) {
      if (g0 > g1) return INF;
      if (g0 < wLo) return (g1 >= wLo) ? wLo : INF;
      float dl = max(0.1 * epsAt(g0), 1.0e-6);
      return (g0 < wH && g1 - g0 > dl) ? g0 : INF;
    }
    // Offer every layer of the pin whose frame origin is off (roL = ro - off). Mirrors the
    // generic loop in latticePins: front to back, stop once the window is behind the tie group.
    void pinStackLayers(vec3 roL, vec3 rd, float wLo, float wHi, int baseProto, int nLayers, vec3 off) {
      vec2 d = roL.xy - gStackAxial.xy;
      float a = dot(rd.xy, rd.xy), b = dot(d, rd.xy), cc = dot(d, d);
      // Axial slab.
      vec2 sl;
      if (abs(rd.z) < 1.0e-9) {
        sl = (roL.z > gStackAxial.z && roL.z < gStackAxial.w) ? vec2(NEG_INF, INF) : vec2(INF, NEG_INF);
      } else {
        float u = (gStackAxial.z - roL.z) / rd.z; float v = (gStackAxial.w - roL.z) / rd.z;
        sl = vec2(min(u, v), max(u, v));
      }
      if (sl.x > sl.y) return;
      vec2 inn = vec2(INF, NEG_INF);   // previous layer's outer disc = this layer's inner disc
      for (int m = 0; m < MAX_LAT_LAYERS; m++) {
        if (m >= nLayers) break;
        float tTolM = (tBest < INF) ? tieAt(tBest) : 0.0;
        float wH = min(wHi, tBest + tTolM);
        if (wLo >= wH) break;
        vec2 o = discInterval(a, b, cc, stackR(m));
        float lo = max(o.x, sl.x); float hi = min(o.y, sl.y);
        float hitT = INF;
        if (o.x <= o.y && lo <= hi) {
          if (inn.x <= inn.y) {
            // Before the ray reaches the inner cylinder, else after it leaves.
            hitT = stackPiece(lo, min(hi, inn.x), wLo, wH);
            if (hitT >= INF) hitT = stackPiece(max(lo, inn.y), hi, wLo, wH);
          } else {
            hitT = stackPiece(lo, hi, wLo, wH);
          }
        }
        if (hitT < INF && hitT >= 0.0) considerHit(hitT, baseProto + m, -1, off);
        inn = o;
      }
    }

    // ======================================================================================
    // Lattice leaf: a rectangular pin grid walked cell by cell.
    // ======================================================================================
    // The whole lattice is ONE BVH leaf. Instead of a BVH over every pin, the ray steps through
    // the pitch cells it crosses (2D DDA, nearest first) and tests only the pin in each, so
    // the cost follows the cells crossed, not the number of pins. A pin always fits inside
    // its own cell (checked on the CPU, buildLatticeGrid), so cell (i, j) can only hold pin
    // (i, j). Layers of one pin are tried in order and all feed considerHit(), exactly like
    // the layers of an instance, so coincident layer faces still tie-resolve by containment.
    //
    // Pins of ONE assembly. [tA, tB] = depths at which the ray may be inside this assembly's
    // xy footprint. pinLo = lower xy corner of pin cell (0,0); asmOff = offset (vs the
    // prototype) of that cell's pin; ip = pin pitch; icnt = pin counts.
    // bLo/bHi = one pin's exact box in the prototype frame; each cell's window is clipped to it
    // (plus pad). env = pin envelope circle (centre xy, radius; radius 0 = none).
    void latticePins(vec3 ro, vec3 rd, float tA, float tB, vec2 pinLo, vec2 asmOff, float offZ,
                     vec2 ip, vec2 icnt, int baseProto, int nLayers, vec3 bLo, vec3 bHi, vec3 env) {
      float pad = 4.0 * uEpsMin;
      vec2 lo = pinLo - pad; vec2 hi = pinLo + icnt * ip + pad;
      float tS = tA; float tE = tB;
      // Clip the ray to the assembly's xy footprint.
      if (abs(rd.x) < 1.0e-9) { if (ro.x < lo.x || ro.x > hi.x) return; }
      else { float a = (lo.x - ro.x) / rd.x; float b = (hi.x - ro.x) / rd.x; tS = max(tS, min(a, b)); tE = min(tE, max(a, b)); }
      if (abs(rd.y) < 1.0e-9) { if (ro.y < lo.y || ro.y > hi.y) return; }
      else { float a = (lo.y - ro.y) / rd.y; float b = (hi.y - ro.y) / rd.y; tS = max(tS, min(a, b)); tE = min(tE, max(a, b)); }
      if (tS >= tE) return;

      vec2 cell = clamp(floor((ro.xy + rd.xy * tS - pinLo) / ip), vec2(0.0), icnt - 1.0);
      vec2 stp = vec2(rd.x >= 0.0 ? 1.0 : -1.0, rd.y >= 0.0 ? 1.0 : -1.0);
      vec2 nextB = pinLo + (cell + vec2(rd.x >= 0.0 ? 1.0 : 0.0, rd.y >= 0.0 ? 1.0 : 0.0)) * ip;
      vec2 tMax = vec2(abs(rd.x) < 1.0e-9 ? INF : (nextB.x - ro.x) / rd.x,
                       abs(rd.y) < 1.0e-9 ? INF : (nextB.y - ro.y) / rd.y);
      vec2 tDelta = vec2(abs(rd.x) < 1.0e-9 ? INF : ip.x / abs(rd.x),
                         abs(rd.y) < 1.0e-9 ? INF : ip.y / abs(rd.y));
      float tCur = tS;
      for (int s = 0; s < MAX_LAT_STEPS; s++) {
        COUNT_LAT
        float tNext = min(min(tMax.x, tMax.y), tE);
        // Front to back: once a hit is known, a cell entered beyond its tie window cannot win.
        float tTol = (tBest < INF) ? tieAt(tBest) : 0.0;
        if (tCur - pad > tBest + tTol) return;
        float wLo = max(tCur - pad, tA);
        float wHi = min(tNext + pad, tB);
        vec3 off = vec3(asmOff + cell * ip, offZ);
        // The ray only matters while it is inside THIS pin's box: skip cells it merely crosses
        // (the gaps between pins) and start the exact test where it enters the box.
        vec2 pbi = boxIntervalInv(ro, bLo + off - pad, bHi + off + pad);
        // Then to the pin's envelope circle (env = centre xy, radius; radius 0 = none). A ray that
        // only clips the box CORNER misses the circle and costs no exact test at all.
        if (pbi.x <= pbi.y && env.z > 0.0) {
          vec2 dc = ro.xy - (env.xy + off.xy);
          float rr = env.z + 2.0 * pad;
          float qa = dot(rd.xy, rd.xy);
          float qc = dot(dc, dc) - rr * rr;
          if (qa > 1.0e-12) {
            float qb = dot(dc, rd.xy);
            float disc = qb * qb - qa * qc;
            if (disc < 0.0) pbi = vec2(INF, NEG_INF);
            else { float sq = sqrt(disc); pbi = vec2(max(pbi.x, (-qb - sq) / qa), min(pbi.y, (-qb + sq) / qa)); }
          } else if (qc > 0.0) pbi = vec2(INF, NEG_INF);
        }
        if (pbi.x <= pbi.y) {
          wLo = max(wLo, pbi.x); wHi = min(wHi, pbi.y);
          vec3 roL = ro - off;
          if (gStackR0.x > 0.0) {
            // Concentric cylinders: closed form (see pinStackLayers).
            pinStackLayers(roL, rd, wLo, wHi, baseProto, nLayers, off);
          } else {
            for (int m = 0; m < MAX_LAT_LAYERS; m++) {
              if (m >= nLayers) break;
              float tTolM = (tBest < INF) ? tieAt(tBest) : 0.0;
              float wH = min(wHi, tBest + tTolM);
              if (wLo >= wH) break;
              float hitT = leafExact(baseProto + m, roL, rd, wLo, wH);
              if (hitT < INF && hitT >= 0.0) considerHit(hitT, baseProto + m, -1, off);
            }
          }
        }
        if (tNext >= tE) return;
        if (tMax.x < tMax.y) { cell.x += stp.x; tMax.x += tDelta.x; } else { cell.y += stp.y; tMax.y += tDelta.y; }
        if (cell.x < 0.0 || cell.y < 0.0 || cell.x >= icnt.x || cell.y >= icnt.y) return;
        tCur = tNext;
      }
    }

    // One lattice leaf. A flat lattice is one assembly; a nested one walks the assembly grid
    // the same way and runs latticePins() for each assembly the ray crosses. [tS0, tE0] is the
    // ray's span inside the leaf's box.
    void latticeVisit(int lat, vec3 ro, vec3 rd, float tS0, float tE0) {
      vec4 a = latT(lat, 0); vec4 b = latT(lat, 1); vec4 c = latT(lat, 2);
      vec4 d = latT(lat, 3); vec4 e = latT(lat, 4);
      vec2 oLo = a.xy; vec2 op = a.zw; vec2 ocnt = b.xy; vec2 ip = b.zw; vec2 icnt = c.xy;
      int baseProto = int(c.z + 0.5); int nLayers = int(c.w + 0.5);
      vec2 pinLo0 = d.xy; float offZ = d.z; vec2 asmOff0 = e.xy;
      vec3 bLo = latT(lat, 5).xyz; vec3 bHi = latT(lat, 6).xyz;
      vec3 env = vec3(e.zw, d.w);   // envelope circle: centre xy, radius
      gStackAxial = latT(lat, 7); gStackR0 = latT(lat, 8); gStackR1 = latT(lat, 9);
      if (ocnt.x < 1.5 && ocnt.y < 1.5) {
        latticePins(ro, rd, tS0, tE0, pinLo0, asmOff0, offZ, ip, icnt, baseProto, nLayers, bLo, bHi, env);
        return;
      }
      float pad = 4.0 * uEpsMin;
      vec2 lo = oLo - pad; vec2 hi = oLo + ocnt * op + pad;
      float tS = tS0; float tE = tE0;
      if (abs(rd.x) < 1.0e-9) { if (ro.x < lo.x || ro.x > hi.x) return; }
      else { float u = (lo.x - ro.x) / rd.x; float v = (hi.x - ro.x) / rd.x; tS = max(tS, min(u, v)); tE = min(tE, max(u, v)); }
      if (abs(rd.y) < 1.0e-9) { if (ro.y < lo.y || ro.y > hi.y) return; }
      else { float u = (lo.y - ro.y) / rd.y; float v = (hi.y - ro.y) / rd.y; tS = max(tS, min(u, v)); tE = min(tE, max(u, v)); }
      if (tS >= tE) return;
      vec2 cell = clamp(floor((ro.xy + rd.xy * tS - oLo) / op), vec2(0.0), ocnt - 1.0);
      vec2 stp = vec2(rd.x >= 0.0 ? 1.0 : -1.0, rd.y >= 0.0 ? 1.0 : -1.0);
      vec2 nextB = oLo + (cell + vec2(rd.x >= 0.0 ? 1.0 : 0.0, rd.y >= 0.0 ? 1.0 : 0.0)) * op;
      vec2 tMax = vec2(abs(rd.x) < 1.0e-9 ? INF : (nextB.x - ro.x) / rd.x,
                       abs(rd.y) < 1.0e-9 ? INF : (nextB.y - ro.y) / rd.y);
      vec2 tDelta = vec2(abs(rd.x) < 1.0e-9 ? INF : op.x / abs(rd.x),
                         abs(rd.y) < 1.0e-9 ? INF : op.y / abs(rd.y));
      float tCur = tS;
      for (int s = 0; s < MAX_LAT_OUTER; s++) {
        COUNT_LAT
        float tNext = min(min(tMax.x, tMax.y), tE);
        float tTol = (tBest < INF) ? tieAt(tBest) : 0.0;
        if (tCur - pad > tBest + tTol) return;
        vec2 shift = cell * op;
        latticePins(ro, rd, max(tCur - pad, tS0), min(tNext + pad, tE0),
                    pinLo0 + shift, asmOff0 + shift, offZ, ip, icnt, baseProto, nLayers, bLo, bHi, env);
        if (tNext >= tE) return;
        if (tMax.x < tMax.y) { cell.x += stp.x; tMax.x += tDelta.x; } else { cell.y += stp.y; tMax.y += tDelta.y; }
        if (cell.x < 0.0 || cell.y < 0.0 || cell.x >= ocnt.x || cell.y >= ocnt.y) return;
        tCur = tNext;
      }
    }

    // Traverse BVH; for each leaf hybrid-hit in local frame:
    //   1) analytic interval as a seed (fast when CSG is simple Inside-heavy)
    //   2) SDF sphere-trace clamped to the leaf AABB interval (robust for
    //      Outside/Complement, which single-interval CSG cannot represent)
    // Collects every hit within tieAt() of the nearest one, then resolves ties
    // by containment. Returns best t; outs proto + instance offset for shading
    // and whether two solid (non-fill) cells genuinely overlap at this pixel.
    float sceneHit(vec3 ro, vec3 rd, out int hitProto, out int hitInst, out vec3 hitOff, out bool hitOverlap) {
      hitProto = -1; hitInst = -1; hitOff = vec3(0.0); hitOverlap = false;
      candCount = 0;
      tBest = INF;
      if (uBvhNodeCount <= 0) return INF;
      int stack[MAX_BVH_STACK]; int sp = 0; stack[sp++] = 0;
      for (int iter = 0; iter < MAX_BVH_ITERS; iter++) {
        if (sp <= 0) break;
        int nodeIdx = stack[--sp];
        if (nodeIdx < 0 || nodeIdx >= uBvhNodeCount) continue;
        COUNT_NODE
        vec4 t0 = fetchBvhTexel(nodeIdx, 0);
        vec4 t1 = fetchBvhTexel(nodeIdx, 1);
        // Tie window for the current best. Zero until the first hit (tBest = INF).
        float tTol = (tBest < INF) ? tieAt(tBest) : 0.0;
        vec2 bi = boxIntervalInv(ro, t0.xyz, t1.xyz);
        // Prune only boxes that start beyond the tie window: a box that begins
        // inside it can still hold a tied candidate.
        if (bi.x > bi.y || bi.x > tBest + tTol) continue;
        if (t0.w > 1.5) {
          // Lattice leaf (t1.w = lattice index): walk its pin grid.
          latticeVisit(int(t1.w + 0.5), ro, rd, max(bi.x, 0.0), bi.y);
        } else if (t0.w > 0.5) {
          int instIdx = int(t1.w + 0.5);
          if (instIdx < 0 || instIdx >= uInstanceCount) continue;
          vec4 inst = fetchInstance(instIdx);
          // An instance covers groupN consecutive prototypes (a pin's layers).
          int packedW = int(inst.w + 0.5);
          int groupN = max(packedW / GROUP_STRIDE, 1);
          int baseProto = packedW - (packedW / GROUP_STRIDE) * GROUP_STRIDE;
          // Ray in the instance's local frame. The transform is rigid, so t is shared.
          vec4 iq = fetchInstanceQuat(instIdx);
          vec3 roL = qRotInv(iq, ro - inst.xyz);
          vec3 rdL = qRotInv(iq, rd);
          float tStart = max(bi.x, 0.0);
          for (int m = 0; m < MAX_GROUP; m++) {
            if (m >= groupN) break;
            int protoIdx = baseProto + m;
            if (protoIdx < 0 || protoIdx >= uProtoCount) break;
            // tBest may have improved on an earlier layer: re-derive the window.
            float tTolM = (tBest < INF) ? tieAt(tBest) : 0.0;
            float tEnd = min(bi.y, tBest + tTolM);
            if (tStart >= tEnd) break;

            // hitT = depth of the first entry into this cell, or INF.
            float hitT = INF;
            if (protoTokenCount(protoIdx) <= MAX_EXACT_TOKENS) {
              hitT = leafExact(protoIdx, roL, rdL, tStart, tEnd);
            } else {
              // Fallback for huge token streams: analytic seed + SDF sphere-trace.
              // Can still run out of steps at grazing angles (known limitation).
              float tHit = evalIntervalEntry(protoIdx, roL, rdL);
              if (tHit < tStart || tHit >= tEnd) tHit = tStart;
              for (int k = 0; k < LEAF_MARCH_STEPS; k++) {
                if (tHit >= tEnd) break;
                float eps = epsAt(tHit);
                float d = evalRegion(protoIdx, roL + rdL * tHit);
                if (d < eps) { hitT = tHit; break; }
                tHit += max(d, eps);
              }
            }

            if (hitT < INF && hitT >= 0.0) considerHit(hitT, protoIdx, instIdx, inst.xyz);
          }
        } else {
          vec4 t2 = fetchBvhTexel(nodeIdx, 2);
          int leftIdx = int(t2.x + 0.5);
          int rightIdx = int(t2.y + 0.5);
          vec4 l0 = fetchBvhTexel(leftIdx, 0);
          vec4 l1 = fetchBvhTexel(leftIdx, 1);
          vec4 r0 = fetchBvhTexel(rightIdx, 0);
          vec4 r1 = fetchBvhTexel(rightIdx, 1);
          float tL = boxIntervalInv(ro, l0.xyz, l1.xyz).x;
          float tR = boxIntervalInv(ro, r0.xyz, r1.xyz).x;
          if (tL > tR) {
            if (sp < MAX_BVH_STACK) stack[sp++] = leftIdx;
            if (sp < MAX_BVH_STACK) stack[sp++] = rightIdx;
          } else {
            if (sp < MAX_BVH_STACK) stack[sp++] = rightIdx;
            if (sp < MAX_BVH_STACK) stack[sp++] = leftIdx;
          }
        }
      }
      if (candCount == 0) return INF;

      // Group may have shrunk after later, nearer hits: re-filter against final tBest.
      float tFinalTol = tieAt(tBest);

      // Fast path: a single candidate in the window is not a tie (the common case).
      // Behaviour is identical to the pre-Phase-1 renderer here.
      int only = -1; int inWindow = 0;
      for (int c = 0; c < MAX_CAND; c++) {
        if (c >= candCount) break;
        if (candT[c] <= tBest + tFinalTol) { inWindow++; only = c; }
      }
      if (inWindow == 1) { hitProto = candProto[only]; hitInst = candInst[only]; hitOff = candOff[only]; return candT[only]; }

      // ---- Tie resolution by exact containment ----
      // Probe slightly PAST the surface along the ray (deeper than the march's
      // own ~1 eps stopping error) and ask each candidate's un-dilated region
      // whether it really contains that point (SDF <= 0).
      vec3 pProbe = ro + rd * (tBest + PROBE_MUL * epsAt(tBest));
      int win = -1; bool winContains = false; float winD = INF; bool winFill = true; int winProto = -1;
      int solidContaining = 0;
      for (int c = 0; c < MAX_CAND; c++) {
        if (c >= candCount) break;
        if (candT[c] > tBest + tFinalTol) continue;
        int pc = candProto[c];
        float d2 = evalRegion(pc, candToLocal(candInst[c], candOff[c], pProbe));
        bool contains = d2 <= 0.0;
        bool isFill = protoIsFill(pc);
        if (contains && !isFill) solidContaining++;
        bool better;
        if (win < 0) better = true;
        // 1. a cell that truly contains the probe beats one that does not
        else if (contains != winContains) better = contains;
        // 2. neither contains it (thin cell / grazing): take the closer one,
        //    falling through to priority when the two are indistinguishable
        else if (!contains && abs(d2 - winD) > epsAt(tBest)) better = d2 < winD;
        // 3. true overlap: solid cells beat the plane-only fill stand-in
        else if (isFill != winFill) better = !isFill;
        // 4. still tied: the later-defined cell (higher prototype index) wins
        else better = pc > winProto;
        if (better) { win = c; winContains = contains; winD = d2; winFill = isFill; winProto = pc; }
      }
      if (win < 0) return INF;
      hitProto = candProto[win]; hitInst = candInst[win]; hitOff = candOff[win];
      // Fill-vs-solid overlap is expected (fill is a deliberate stand-in under
      // instancing); only flag two *solid* cells occupying the same volume.
      hitOverlap = solidContaining >= 2;
      return candT[win];
    }

    // Tetrahedron technique — 4 samples
    vec3 calcNormal(vec3 pLocal, int protoIdx) {
      const float e = 0.001;
      vec2 k = vec2(1.0, -1.0);
      return normalize(
        k.xyy * evalRegion(protoIdx, pLocal + k.xyy * e) +
        k.yyx * evalRegion(protoIdx, pLocal + k.yyx * e) +
        k.yxy * evalRegion(protoIdx, pLocal + k.yxy * e) +
        k.xxx * evalRegion(protoIdx, pLocal + k.xxx * e)
      );
    }

    void main() {
      vec2 ndc = vUv * 2.0 - 1.0;
      vec3 rd = normalize(uCamForward + uCamRight * (ndc.x * uTanHalfFov * uAspect) + uCamUp * (ndc.y * uTanHalfFov));
      vec3 ro = uCamPos;
      vec3 col = vec3(0.059, 0.090, 0.165);

      // Phase A: root AABB pre-test
      // Per-ray constant for every world-space box test below (see boxIntervalInv).
      gInvRd = 1.0 / vec3(abs(rd.x) < 1.0e-8 ? (rd.x >= 0.0 ? 1.0e-8 : -1.0e-8) : rd.x,
                          abs(rd.y) < 1.0e-8 ? (rd.y >= 0.0 ? 1.0e-8 : -1.0e-8) : rd.y,
                          abs(rd.z) < 1.0e-8 ? (rd.z >= 0.0 ? 1.0e-8 : -1.0e-8) : rd.z);
      vec2 rootHit = boxIntervalInv(ro, uSceneBMin, uSceneBMax);
      if (rootHit.x <= rootHit.y && rootHit.y >= 0.0) {
        int hitProto; int hitInst; vec3 hitOff; bool hitOverlap;
        float t = sceneHit(ro, rd, hitProto, hitInst, hitOff, hitOverlap);
        if (hitProto >= 0 && t < INF) {
          vec3 p = ro + rd * t;
          // Normal is computed in the instance's local frame, then rotated back to world.
          vec3 n = qRot(quatOf(hitInst), calcNormal(candToLocal(hitInst, hitOff, p), hitProto));
          float diff = max(dot(n, normalize(vec3(0.4, 0.6, 0.8))), 0.0);
          col = protoColor(hitProto) * (0.35 + 0.65 * diff);
          // Genuine solid-on-solid overlap (OpenMC would reject this geometry).
          if (uMarkOverlaps > 0.5 && hitOverlap) col = mix(col, vec3(1.0, 0.1, 0.8), 0.5);
        }
      }
      #if HEATMAP
      {
        // Normalisers are "this pixel is heavy" levels for each counter; tune freely. blue cheap -> red costly.
        float n = float(gNodes) / 48.0, l = float(gLatSteps) / 96.0, g = float(gGridSteps) / 96.0, k = float(gTokEvals) / 3000.0;
        float h = (uHeatMode == 2) ? n : (uHeatMode == 3) ? l : (uHeatMode == 4) ? g : (uHeatMode == 5) ? k : 0.5 * (n + l + g + k);
        col = heatRamp(h);
      }
      #endif
      gl_FragColor = vec4(col, 1.0);
    }
  `;

  function makeUniforms() {
    return {
      uCamPos: { value: new THREE.Vector3() },
      uCamForward: { value: new THREE.Vector3(0, 1, 0) },
      uCamRight: { value: new THREE.Vector3(1, 0, 0) },
      uCamUp: { value: new THREE.Vector3(0, 0, 1) },
      uTanHalfFov: { value: Math.tan(THREE.MathUtils.degToRad(25)) },
      uAspect: { value: 1 },
      uSurfCount: { value: 0 },
      uProtoCount: { value: 0 },
      uTokenTex: { value: null as THREE.DataTexture | null },
      uInstanceTex: { value: null as THREE.DataTexture | null },
      uInstanceCount: { value: 0 },
      uBvhTex: { value: null as THREE.DataTexture | null },
      uBvhNodeCount: { value: 0 },
      uSurfTex: { value: null as THREE.DataTexture | null },
      uProtoTex: { value: null as THREE.DataTexture | null },
      uGridTex: { value: null as THREE.DataTexture | null },
      uLatTex: { value: null as THREE.DataTexture | null }, uLatTexH: { value: 1 },
      uTokTexH: { value: 1 }, uInstTexH: { value: 1 }, uBvhTexH: { value: 1 },
      uSurfTexH: { value: 1 }, uProtoTexH: { value: 1 }, uGridTexH: { value: 1 },
      uSceneBMin: { value: new THREE.Vector3(-1e3, -1e3, -1e3) },
      uSceneBMax: { value: new THREE.Vector3(1e3, 1e3, 1e3) },
      // Phase 1 coincident-face tolerance (see epsAt() in the shader).
      uEpsMin: { value: 1e-4 },        // set per scene in rebuildAndRender()
      uPixelAngle: { value: 1e-3 },    // set per frame in updateCameraUniforms()
      uMarkOverlaps: { value: MARK_OVERLAPS ? 1 : 0 },
      uHeatMode: { value: 0 },          // cost heat-map mode (only read when HEATMAP=1)
    };
  }

  const _camPos = new THREE.Vector3();
  const _bufSize = new THREE.Vector2();
  const _forward = new THREE.Vector3();
  const _worldUp = new THREE.Vector3(0, 0, 1);
  const _right = new THREE.Vector3();
  const _up = new THREE.Vector3();

  function updateCameraUniforms() {
    if (!shaderMaterial) return;
    const camX = cameraTarget.x + cameraDistance * Math.sin(cameraPhi) * Math.cos(cameraTheta);
    const camY = cameraTarget.y + cameraDistance * Math.sin(cameraPhi) * Math.sin(cameraTheta);
    const camZ = cameraTarget.z + cameraDistance * Math.cos(cameraPhi);
    _camPos.set(camX, camY, camZ);
    _forward.copy(cameraTarget).sub(_camPos).normalize();
    _right.crossVectors(_forward, _worldUp);
    if (_right.lengthSq() < 1e-8) _right.set(1, 0, 0);
    _right.normalize();
    _up.crossVectors(_right, _forward).normalize();
    const u = shaderMaterial.uniforms;
    u.uCamPos.value.copy(_camPos);
    u.uCamForward.value.copy(_forward);
    u.uCamRight.value.copy(_right);
    u.uCamUp.value.copy(_up);
    // Half a pixel of angular footprint, from the *actual* drawing-buffer height
    // (it changes between INTERACT_DPR and IDLE_DPR, so read it every frame).
    renderer?.getDrawingBufferSize(_bufSize);
    const hPx = Math.max(1, _bufSize.y || 1);
    u.uPixelAngle.value = 0.5 * (2 * u.uTanHalfFov.value) / hPx;
  }

  // Prints:
  //   <cam position and rotation> — world-space cam pos, theta/phi/distance,
  //     and the forward/right/up basis actually uploaded to the shader
  //   <walls of a box displayed> — per box, which of the 6 axis-aligned
  //     faces geometrically face the camera ("expected") vs. what a probe
  //     grid of framebuffer pixels actually shows (background color vs.
  //     that box's material color). A face marked expected=true whose
  //     nearest probe pixel reads as background is the disappearing-wall
  //     symptom caught in the act — paste this log verbatim.
  function logCsgDebug() {
    if (!renderer || !canvasEl) return;
    const camPos = _camPos.clone();
    console.log(
      `<cam position and rotation> pos=(${camPos.x.toFixed(2)}, ${camPos.y.toFixed(2)}, ${camPos.z.toFixed(2)}) ` +
      `theta=${THREE.MathUtils.radToDeg(cameraTheta).toFixed(1)}deg phi=${THREE.MathUtils.radToDeg(cameraPhi).toFixed(1)}deg ` +
      `dist=${cameraDistance.toFixed(2)} target=(${cameraTarget.x.toFixed(2)}, ${cameraTarget.y.toFixed(2)}, ${cameraTarget.z.toFixed(2)}) ` +
      `forward=(${_forward.x.toFixed(3)}, ${_forward.y.toFixed(3)}, ${_forward.z.toFixed(3)}) ` +
      `right=(${_right.x.toFixed(3)}, ${_right.y.toFixed(3)}, ${_right.z.toFixed(3)}) ` +
      `up=(${_up.x.toFixed(3)}, ${_up.y.toFixed(3)}, ${_up.z.toFixed(3)})`
    );

    const w = canvasEl.width, h = canvasEl.height;
    const gl = renderer.getContext();
    const px = new Uint8Array(4);
    const bgHex = (rx: number, ry: number) => {
      // WebGL readPixels origin is bottom-left.
      gl.readPixels(rx, h - 1 - ry, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
      return `#${px[0].toString(16).padStart(2, '0')}${px[1].toString(16).padStart(2, '0')}${px[2].toString(16).padStart(2, '0')}`;
    };
    const BG = '#0f1729'; // matches vec3(0.059,0.090,0.165) background in the shader

    const faces: { axis: 'x' | 'y' | 'z'; sign: 1 | -1; label: string }[] = [
      { axis: 'x', sign: 1, label: '+X' }, { axis: 'x', sign: -1, label: '-X' },
      { axis: 'y', sign: 1, label: '+Y' }, { axis: 'y', sign: -1, label: '-Y' },
      { axis: 'z', sign: 1, label: '+Z' }, { axis: 'z', sign: -1, label: '-Z' },
    ];

    console.log('<walls of a box displayed>');
    for (const box of debugBoxSource?.() ?? []) {
      const cx = (box.aabb.xMin + box.aabb.xMax) / 2;
      const cy = (box.aabb.yMin + box.aabb.yMax) / 2;
      const cz = (box.aabb.zMin + box.aabb.zMax) / 2;
      const toCam = new THREE.Vector3(camPos.x - cx, camPos.y - cy, camPos.z - cz).normalize();
      const rows: string[] = [];
      for (const f of faces) {
        const n = new THREE.Vector3(f.axis === 'x' ? f.sign : 0, f.axis === 'y' ? f.sign : 0, f.axis === 'z' ? f.sign : 0);
        const expected = n.dot(toCam) > 0.05; // face normal points roughly toward camera
        if (!expected) continue;
        // Sample near the center of that face, projected into screen space.
        const fx = f.axis === 'x' ? (f.sign > 0 ? box.aabb.xMax : box.aabb.xMin) : cx;
        const fy = f.axis === 'y' ? (f.sign > 0 ? box.aabb.yMax : box.aabb.yMin) : cy;
        const fz = f.axis === 'z' ? (f.sign > 0 ? box.aabb.zMax : box.aabb.zMin) : cz;
        const world = new THREE.Vector3(fx, fy, fz);
        const toFace = world.clone().sub(camPos);
        const depth = toFace.dot(_forward);
        const ndcX = depth > 1e-6 ? toFace.dot(_right) / (depth * u_tanHalfFov() * aspectRatio()) : NaN;
        const ndcY = depth > 1e-6 ? toFace.dot(_up) / (depth * u_tanHalfFov()) : NaN;
        let sampled = 'off-screen';
        if (Number.isFinite(ndcX) && Number.isFinite(ndcY) && Math.abs(ndcX) < 1 && Math.abs(ndcY) < 1) {
          const px_ = Math.round(((ndcX + 1) / 2) * (w - 1));
          const py_ = Math.round(((1 - ndcY) / 2) * (h - 1));
          sampled = bgHex(px_, py_);
        }
        const isBackground = sampled.toLowerCase() === BG;
        rows.push(`${f.label}: expected=visible sampled=${sampled}${isBackground ? '  <-- MISSING (reads as background)' : ''}`);
      }
      console.log(`  box "${box.name}" (color ${box.color}): ${rows.length ? '\n    ' + rows.join('\n    ') : '(no faces geometrically facing camera)'}`);
    }
  }
  function u_tanHalfFov() { return shaderMaterial?.uniforms.uTanHalfFov.value ?? Math.tan(THREE.MathUtils.degToRad(25)); }
  function aspectRatio() { return shaderMaterial?.uniforms.uAspect.value ?? 1; }

  // Change the render resolution WITHOUT ever presenting a blank frame.
  //
  // WHY THIS EXISTS: renderer.setPixelRatio() resizes the <canvas>, and resizing a
  // canvas wipes its drawing buffer. The old code changed the ratio and then only
  // *scheduled* a redraw (requestRender -> rAF), or on pointer-down scheduled none at
  // all, so the browser could present the cleared buffer: the whole scene vanished for
  // a frame every time you clicked to rotate (and again when settling back to full res).
  //
  // RULE FOR FUTURE EDITORS: never call renderer.setPixelRatio()/setSize() directly.
  // Go through here (or resize()), which redraws synchronously in the SAME task. A
  // buffer is only presented after the task ends, so the blank one is never shown.
  // It is also a no-op when the ratio is unchanged, so per-move/per-wheel calls don't
  // needlessly clear and redraw the canvas.
  function setRenderScale(dpr: number) {
    if (!renderer || renderer.getPixelRatio() === dpr) return;
    renderer.setPixelRatio(dpr);
    // The synchronous render below supersedes any queued one.
    if (pendingRenderFrame !== null) { cancelAnimationFrame(pendingRenderFrame); pendingRenderFrame = null; }
    renderQueued = false;
    render();
  }

  function beginInteraction() {
    if (!renderer) return;
    setRenderScale(interactionScale());
    clearTimeout(interactTimer);
    interactTimer = setTimeout(() => setRenderScale(IDLE_DPR), 150);
  }

  function latticeCellNameSet(lis: LatticeInstance[]): Set<string> {
    // Bugfix: this used to special-case the "_0_layerN" FuelPin naming
    // pattern and, for any other cell name shape, fall back to re-adding
    // the prototype's own (index-0) literal name on every loop iteration
    // instead of building a per-instance name. That silently only ever
    // excluded pin 0 from the non-instanced fallback loop below — every
    // other pin instance in a lattice would leak through as a separate,
    // non-instanced prototype and blow past MAX_PROTOTYPES for any lattice
    // of real size. Generalized here: derive each cell's suffix by
    // stripping the `${lattice_name}_0` prefix (which every composite
    // template's cell_name_prefix follows, not just FuelPin's `_layerN`),
    // then rebuild the name for every instance index.
    const set = new Set<string>();
    for (const li of lis) {
      const prefix0 = `${li.lattice_name}_0`;
      const suffixes: string[] = [];
      for (const c of li.prototype_cells) {
        const name = c.name ?? '';
        if (!name) continue;
        suffixes.push(name.startsWith(prefix0) ? name.slice(prefix0.length) : name);
      }
      for (let i = 0; i < li.instances.length; i++) {
        for (const suf of suffixes) set.add(`${li.lattice_name}_${i}${suf}`);
      }
    }
    return set;
  }

  function rebuildAndRender() {
    if (!shaderMaterial) return;
    const u = shaderMaterial.uniforms;
    if (!csg) {
      tokenTexture?.dispose(); tokenTexture = null;
      instanceTexture?.dispose(); instanceTexture = null;
      bvhTexture?.dispose(); bvhTexture = null;
      surfTexture?.dispose(); surfTexture = null;
      protoTexture?.dispose(); protoTexture = null;
      gridTexture?.dispose(); gridTexture = null;
      latTexture?.dispose(); latTexture = null; u.uLatTex.value = null;
      u.uTokenTex.value = null; u.uInstanceTex.value = null; u.uBvhTex.value = null;
      u.uSurfTex.value = null; u.uProtoTex.value = null; u.uGridTex.value = null;
      u.uProtoCount.value = 0; u.uInstanceCount.value = 0; u.uSurfCount.value = 0; u.uBvhNodeCount.value = 0;
      instanceCount = 0; prototypeCount = 0; materialLegend = []; usingInstancing = false;
      truncatedSurfaces = truncatedProtos = truncatedInstances = truncatedTokens = truncatedBvh = truncatedGrids = false;
      render(); return;
    }
    // Shader surface table. It used to be csg.surfaces.slice(0, MAX_SURFACES), i.e. the
    // first 256 surfaces BY POSITION. A big lattice contributes ~4 surfaces per pin to
    // csg.surfaces, so anything placed after it (a second lattice's prototype, later
    // cells) landed past index 255, its tokens resolved to surface -1, and the cell
    // silently vanished. Now only surfaces that a visible proto/fill region actually
    // references are registered (registerRegion), so the table holds what the shader
    // can use, and overflow is reported only when it genuinely drops something.
    const surfaceById = new Map(csg.surfaces.map((s) => [s.id, s]));
    const surfacesUsed: CsgSurface[] = [];
    const surfIndex = new Map<string, number>();
    let surfOverflow = false;
    const registerRegion = (region: RegionNode, lookup: Map<string, CsgSurface>) => {
      const ids: string[] = [];
      surfaceIdsInRegion(region, ids);
      for (const id of ids) {
        if (surfIndex.has(id)) continue;
        const s = lookup.get(id);
        if (!s) continue;
        if (surfacesUsed.length >= MAX_SURFACES) { surfOverflow = true; continue; }
        surfIndex.set(id, surfacesUsed.length);
        surfacesUsed.push(s);
      }
    };
    const sceneBounds = computeBounds(csg);
    // Pad root AABB slightly so grazing rays / tight plane bounds don't miss.
    {
      const pad = 0.05 * Math.max(
        sceneBounds.xMax - sceneBounds.xMin,
        sceneBounds.yMax - sceneBounds.yMin,
        sceneBounds.zMax - sceneBounds.zMin,
        1,
      );
      u.uSceneBMin.value.set(sceneBounds.xMin - pad, sceneBounds.yMin - pad, sceneBounds.zMin - pad);
      u.uSceneBMax.value.set(sceneBounds.xMax + pad, sceneBounds.yMax + pad, sceneBounds.zMax + pad);
      u.uEpsMin.value = EPS_MIN_FRACTION * Math.max(
        sceneBounds.xMax - sceneBounds.xMin, sceneBounds.yMax - sceneBounds.yMin,
        sceneBounds.zMax - sceneBounds.zMin, 1);
    }

    interface ProtoEntry {
      region: RegionNode; material_id: string; aabb: AxisBounds; isFill?: boolean; name?: string;
      /** Holds one pin's envelope for a grid; never instanced or drawn itself. */
      isEnvelope?: boolean;
      gridStart?: number; gridCount?: number;
    }
    const protos: ProtoEntry[] = [];
    interface GridEntry { origin: [number, number]; pitch: [number, number]; count: [number, number]; envProto: number; }
    const grids: GridEntry[] = [];
    let gridOverflow = false;
    const instances: { dx: number; dy: number; dz: number; protoIdx: number; count: number; q?: Quat }[] = [];
    // Lattices drawn by the lattice leaf instead of per-pin instances (see latticeGrid.ts).
    const latticeEntries: { grid: LatticeGrid; baseProto: number; nLayers: number }[] = [];
    // Drawable cells with the transform that places each in the world (identity for root cells).
    const worldCells = resolveWorldCells(csg.cells);
    const lis = csg.lattice_instances ?? [];
    usingInstancing = lis.length > 0;

    // One non-lattice cell: its own prototype plus, for each pin grid it is cut by, a
    // prototype holding that grid's single-pin envelope (see GridBlocker).
    const addCell = (wc: WorldCell<CsgCell>, region: RegionNode, isFill: boolean) => {
      const cell = wc.cell;
      const protoIdx = protos.length;
      const entry: ProtoEntry = {
        region, material_id: cell.material_id!, aabb: cellAABB(region, surfaceById, sceneBounds),
        isFill, name: cell.name ?? undefined,
      };
      protos.push(entry);
      registerRegion(region, surfaceById);
      for (const gb of cell.grid_blockers ?? []) {
        // A grid we cannot hold would silently leave its pins filled in: report it instead.
        if (grids.length >= MAX_GRIDS || (entry.gridCount ?? 0) >= MAX_GRIDS_PER_PROTO
            || protos.length >= MAX_PROTOTYPES) { gridOverflow = true; continue; }
        if (entry.gridCount === undefined) { entry.gridStart = grids.length; entry.gridCount = 0; }
        grids.push({ origin: gb.origin, pitch: gb.pitch, count: gb.count, envProto: protos.length });
        entry.gridCount++;
        protos.push({ region: gb.envelope, material_id: '', aabb: sceneBounds, isEnvelope: true });
        registerRegion(gb.envelope, surfaceById);
      }
      if (instances.length < MAX_INSTANCES) instances.push({ dx: wc.offset[0], dy: wc.offset[1], dz: wc.offset[2], protoIdx, count: 1, q: wc.quat });
    };

    if (usingInstancing) {
      for (const li of lis) {
        // Objects-panel eye toggle is keyed by lattice placement name.
        if (!isVisible(li.lattice_name)) continue;

        const baseProtoIdx = protos.length;
        const protoCells = li.prototype_cells.filter((c) => c.material_id != null);
        const protoSurfById = new Map(surfaceById);
        for (const s of li.prototype_surfaces) protoSurfById.set(s.id, s);
        for (const cell of protoCells) {
          if (protos.length >= MAX_PROTOTYPES) break;
          protos.push({ region: cell.region, material_id: cell.material_id!, aabb: cellAABB(cell.region, protoSurfById, sceneBounds), isFill: false });
          registerRegion(cell.region, protoSurfById);
        }
        const numLayers = protos.length - baseProtoIdx;
        // Phase D: inner_offsets non-empty → nested lattice
        // world = assembly_offset + pin_offset; prototype stays one pin.
        const innerOffs = (li as LatticeInstance & { inner_offsets?: [number, number, number][] }).inner_offsets ?? [];
        const outerOffs = li.instances;

        // Fast path: a regular rectangular grid becomes ONE BVH leaf that the shader walks cell
        // by cell, so cost no longer grows with pin count and MAX_INSTANCES stops applying.
        // Anything that does not qualify (hex, holes, overlapping pins, layers the exact leaf
        // test cannot hold) keeps the per-pin instances below, unchanged.
        if (numLayers > 0 && numLayers <= MAX_LAT_LAYERS && latticeEntries.length < MAX_LATTICES) {
          let union = protos[baseProtoIdx].aabb;
          // Exact (unpadded) extent: the fit test must not depend on how big the scene is.
          let tight = cellAABB(protos[baseProtoIdx].region, protoSurfById, sceneBounds, false);
          let exactOk = true;
          for (let k = 0; k < numLayers; k++) {
            union = unionAABB(union, protos[baseProtoIdx + k].aabb);
            tight = unionAABB(tight, cellAABB(protos[baseProtoIdx + k].region, protoSurfById, sceneBounds, false));
            const toks: number[] = [];
            flattenRegion(protos[baseProtoIdx + k].region, new Map(), toks);
            if (toks.length / 2 > MAX_EXACT_TOKENS) exactOk = false;
          }
          const layerRegions = protos.slice(baseProtoIdx, baseProtoIdx + numLayers).map((p) => p.region);
          const grid = exactOk ? buildLatticeGrid(
            {
              origin: li.origin, instances: outerOffs, innerOffsets: innerOffs, proto: union, fit: tight,
              envelope: pinEnvelope(layerRegions, (id) => protoSurfById.get(id)),
              // Concentric cylinders (FuelPin): the shader intersects them in closed form.
              stack: pinStack(layerRegions, (id) => protoSurfById.get(id)),
            },
            { maxInnerSteps: MAX_LAT_STEPS, maxOuterSteps: MAX_LAT_OUTER },
          ) : null;
          if (grid) { latticeEntries.push({ grid, baseProto: baseProtoIdx, nLayers: numLayers }); continue; }
        }
        // One instance per pin covers all of its layers (see MAX_GROUP).
        const pushPin = (dx: number, dy: number, dz: number) => {
          for (let k = 0; k < numLayers; k += MAX_GROUP) {
            if (instances.length >= MAX_INSTANCES) return;
            instances.push({ dx, dy, dz, protoIdx: baseProtoIdx + k, count: Math.min(MAX_GROUP, numLayers - k) });
          }
        };
        if (innerOffs.length > 0) {
          for (const a of outerOffs) {
            for (const pin of innerOffs) pushPin(a[0] + pin[0], a[1] + pin[1], a[2] + pin[2]);
          }
        } else {
          for (const off of outerOffs) pushPin(off[0], off[1], off[2]);
        }
      }
      const latticeNames = latticeCellNameSet(lis);
      for (const wc of worldCells) {
        const cell = wc.cell;
        if (cell.material_id == null) continue;
        if (cell.name && latticeNames.has(cell.name)) continue;
        // Hide Box / fill / SinglePlacement cells when their placement is toggled off.
        const key = visibilityKey(cell.name);
        if (key && !isVisible(key)) continue;
        if (protos.length >= MAX_PROTOTYPES) break;
        // Phase D fill scaling: moderator fill = box ∩ Outside(every pin).
        // Outside(pin) is redundant under opaque nearest-hit (pin protos own
        // those volumes) and blows the token budget past ~6 pins (6 planes +
        // 7 outsides > old threshold of 12 → plane-only kicked in and the
        // solid lid hid every pin). Always strip to plane half-spaces when
        // instancing; pin-vs-fill priority in the shader restores visibility.
        let region = cell.region;
        // The plane-only stand-in is for the root moderator fill. An instanced (rotated)
        // cell is a real object in its own local frame: keep its exact region. So is a
        // cell cut by pin grids.
        const gridded = (cell.grid_blockers?.length ?? 0) > 0;
        // Only the moderator fill is simplified; a real solid (CylinderZ, a boolean, ...)
        // keeps its exact region and is NOT flagged as a fill, so it wins fill ties.
        const standIn = !wc.instanced && !gridded && !isSolidRegion(region, surfaceById);
        const simplified = standIn ? planeOnlyRegion(region, surfaceById) : null;
        if (simplified) region = simplified;
        addCell(wc, region, simplified !== null);
      }
    } else {
      for (const wc of worldCells) {
        const cell = wc.cell;
        const key = visibilityKey(cell.name);
        if (key && !isVisible(key)) continue;
        if (protos.length >= MAX_PROTOTYPES) break;
        addCell(wc, cell.region, false);
      }
    }

    // Pack the surface table now that every visible proto/fill has registered what it uses.
    const surfTab = allocTable(SURF_TEX_W, 2 * surfacesUsed.length);
    surfacesUsed.forEach((sf, i) => {
      const packed = packSurface(sf);
      surfTab.data.set([packed.v.x, packed.v.y, packed.v.z, packed.v.w], 2 * i * 4);
      surfTab.data.set([packed.type, packed.b, packed.c, 0], (2 * i + 1) * 4);
    });
    surfTexture?.dispose();
    surfTexture = tableTexture(surfTab);
    u.uSurfTex.value = surfTexture; u.uSurfTexH.value = surfTab.height;
    u.uSurfCount.value = surfacesUsed.length;
    truncatedSurfaces = surfOverflow;

    // Snapshot every named proto's AABB + resolved color for the wall-debug
    // logger (see debugWalls below). Instance offsets are folded in so this
    // is world-space, matching what the shader actually renders.
    // LAZY on purpose: the old eager version ran an O(protos x instances) scan on EVERY rebuild
    // (measured ~110 ms worst case at 1024 protos x 16384 instances) for a log nobody had open.
    // RULE FOR FUTURE EDITORS: keep debug-only work out of rebuildAndRender's hot path.
    debugBoxSource = () => {
      // first instance that covers each prototype (same answer as the old per-proto find())
      const firstInst = new Map<number, (typeof instances)[number]>();
      for (const ins of instances) {
        for (let k = 0; k < ins.count; k++) if (!firstInst.has(ins.protoIdx + k)) firstInst.set(ins.protoIdx + k, ins);
      }
      return protos
        .map((p, pi) => {
          if (p.isEnvelope) return null;
          const inst = firstInst.get(pi);
          const offset = inst ? { dx: inst.dx, dy: inst.dy, dz: inst.dz } : { dx: 0, dy: 0, dz: 0 };
          const [r, g, b] = resolveMaterialColor(p.material_id);
          return {
            name: p.name ?? `${p.isFill ? 'fill' : 'cell'}#${pi}`,
            color: `#${Math.round(r * 255).toString(16).padStart(2, '0')}${Math.round(g * 255).toString(16).padStart(2, '0')}${Math.round(b * 255).toString(16).padStart(2, '0')}`,
            aabb: transformAABB(p.aabb, [offset.dx, offset.dy, offset.dz], inst?.q ?? QUAT_IDENTITY),
          };
        })
        .filter((b): b is DebugBoxEntry => b !== null && Number.isFinite(b.aabb.xMin) && Number.isFinite(b.aabb.xMax));
    };

    truncatedProtos = protos.length >= MAX_PROTOTYPES;
    truncatedInstances = instances.length >= MAX_INSTANCES;

    const protoTab = allocTable(PROTO_TEX_W, 2 * protos.length);
    const protoTokenCount: number[] = [];
    const tokRows = Math.max(1, protos.length);
    const texData = new Float32Array(MAX_TOKENS_PER_PROTO * tokRows * 4);
    let anyTokenTrunc = false;
    const legendSeen = new Map<string, string>();
    protos.forEach((proto, pi) => {
      const [r, g, b] = resolveMaterialColor(proto.material_id);
      if (!proto.isEnvelope && !legendSeen.has(proto.material_id)) legendSeen.set(proto.material_id, `#${new THREE.Color(r, g, b).getHexString()}`);
      const tokens: number[] = [];
      flattenRegion(proto.region, surfIndex, tokens);
      const pairCount = tokens.length / 2;
      const used = Math.min(pairCount, MAX_TOKENS_PER_PROTO);
      if (pairCount > MAX_TOKENS_PER_PROTO) anyTokenTrunc = true;
      protoTokenCount[pi] = used;
      protoTab.data.set([r, g, b, proto.isFill ? 1 : 0], 2 * pi * 4);
      protoTab.data.set([used, proto.gridStart ?? 0, proto.gridCount ?? 0, 0], (2 * pi + 1) * 4);
      for (let p = 0; p < used; p++) {
        const texelIdx = (pi * MAX_TOKENS_PER_PROTO + p) * 4;
        texData[texelIdx] = tokens[p * 2]; texData[texelIdx + 1] = tokens[p * 2 + 1];
      }
    });
    truncatedTokens = anyTokenTrunc;
    const gridTab = allocTable(GRID_TEX_W, 2 * grids.length);
    grids.forEach((g, i) => {
      gridTab.data.set([g.origin[0], g.origin[1], g.pitch[0], g.pitch[1]], 2 * i * 4);
      gridTab.data.set([g.count[0], g.count[1], g.envProto, 0], (2 * i + 1) * 4);
      // The shader tests an envelope with at most MAX_EXACT_TOKENS tokens.
      if (protoTokenCount[g.envProto] > MAX_EXACT_TOKENS) gridOverflow = true;
    });
    truncatedGrids = gridOverflow;
    prototypeCount = protos.length;
    instanceCount = instances.length + latticeEntries.reduce((n, e) => n + e.grid.pinCount, 0);
    materialLegend = [...legendSeen.entries()].map(([id, color]) => ({ id, color }));
    u.uProtoCount.value = protos.length;
    protoTexture?.dispose(); protoTexture = tableTexture(protoTab);
    u.uProtoTex.value = protoTexture; u.uProtoTexH.value = protoTab.height;
    gridTexture?.dispose(); gridTexture = tableTexture(gridTab);
    u.uGridTex.value = gridTexture; u.uGridTexH.value = gridTab.height;

    tokenTexture?.dispose();
    tokenTexture = tableTexture({ data: texData, width: MAX_TOKENS_PER_PROTO, height: tokRows });
    u.uTokenTex.value = tokenTexture; u.uTokTexH.value = tokRows;

    const instTab = allocTable(INST_TEX_W, 2 * instances.length);   // 2 texels per instance
    const instData = instTab.data;
    const instanceBoxes: AxisBounds[] = [];
    // Texel 2i = (offset, first prototype + layer count), texel 2i+1 = rotation quaternion
    // (identity for an unrotated instance).
    instances.forEach((inst, i) => {
      const q = inst.q ?? QUAT_IDENTITY;
      instData.set([inst.dx, inst.dy, inst.dz, inst.protoIdx + GROUP_STRIDE * inst.count], 2 * i * 4);
      instData.set([q[0], q[1], q[2], q[3]], (2 * i + 1) * 4);
      // The leaf box has to hold every layer the instance covers; the BVH needs the WORLD
      // box of the (possibly rotated) local AABB.
      let box = protos[inst.protoIdx].aabb;
      for (let m = 1; m < inst.count; m++) box = unionAABB(box, protos[inst.protoIdx + m].aabb);
      instanceBoxes.push(transformAABB(box, [inst.dx, inst.dy, inst.dz], q));
    });
    instanceTexture?.dispose();
    instanceTexture = tableTexture(instTab);
    u.uInstanceTex.value = instanceTexture; u.uInstTexH.value = instTab.height; u.uInstanceCount.value = instances.length;

    // Lattice table + one BVH leaf per lattice (leaf indices follow the instance leaves).
    const latTab = allocTable(LAT_TEXELS, LAT_TEXELS * Math.max(1, latticeEntries.length));
    latticeEntries.forEach((e, i) => {
      packLatticeGrid(e.grid, e.baseProto, e.nLayers, latTab.data, i);
      instanceBoxes.push(e.grid.box);
    });
    latTexture?.dispose(); latTexture = tableTexture(latTab);
    u.uLatTex.value = latTexture; u.uLatTexH.value = latTab.height;
    const leafCount = instances.length + latticeEntries.length;

    const tBvh0 = performance.now();
    const bvhTab = allocTable(BVH_TEX_W, 3 * Math.min(MAX_BVH_NODES, Math.max(1, 2 * leafCount)));
    const bvhTexData = bvhTab.data;
    let bvhNodeCount = 0;
    truncatedBvh = false;
    if (leafCount > 0) {
      const flat = flattenBvh(buildBvh(Array.from({ length: leafCount }, (_, i) => i), instanceBoxes), bvhTexData, instances.length);
      bvhNodeCount = flat.count;
      truncatedBvh = flat.truncated;
    }
    u.uBvhNodeCount.value = bvhNodeCount;
    console.log(`[perf] BVH build: ${(performance.now() - tBvh0).toFixed(2)}ms, nodes=${bvhNodeCount}, protos=${protos.length}, instances=${instances.length}, lattices=${latticeEntries.length}, instancing=${usingInstancing}`);

    bvhTexture?.dispose();
    bvhTexture = tableTexture(bvhTab);
    u.uBvhTex.value = bvhTexture; u.uBvhTexH.value = bvhTab.height;

    if (!hasFramedOnce) { resetCamera(); hasFramedOnce = true; } else render();
  }

  function resetCamera() {
    cameraTheta = Math.PI / 4; cameraPhi = Math.PI / 3;
    if (csg) {
      const b = computeBounds(csg);
      const span = Math.max(b.xMax - b.xMin, b.yMax - b.yMin, b.zMax - b.zMin, 1);
      cameraDistance = span * 1.8;
      cameraTarget = new THREE.Vector3((b.xMin + b.xMax) / 2, (b.yMin + b.yMax) / 2, (b.zMin + b.zMax) / 2);
    } else { cameraDistance = 8; cameraTarget = new THREE.Vector3(0, 0, 0); }
    updateCameraUniforms(); render();
  }

  let pointerMoveCount = 0;
  function render() {
    if (!renderer) return;
    updateCameraUniforms();
    timedRender();
  }

  let isDragging = false, lastX = 0, lastY = 0, renderQueued = false, pendingRenderFrame: number | null = null;
  function requestRender() {
    if (renderQueued) return;
    renderQueued = true;
    pendingRenderFrame = requestAnimationFrame(() => { renderQueued = false; pendingRenderFrame = null; render(); });
  }
  // Deliberately NOT calling beginInteraction() here: a plain click (no drag) should not
  // drop the resolution at all. The first pointermove of a real drag does that, and
  // setRenderScale() redraws in the same task, so there is no blank frame either way.
  function onPointerDown(e: PointerEvent) { isDragging = true; lastX = e.clientX; lastY = e.clientY; canvasEl.setPointerCapture(e.pointerId); }
  function onPointerMove(e: PointerEvent) {
    if (!isDragging) return;
    pointerMoveCount++;
    const dx = e.clientX - lastX, dy = e.clientY - lastY;
    lastX = e.clientX; lastY = e.clientY;
    cameraTheta -= dx * 0.005;
    cameraPhi = Math.max(0.05, Math.min(Math.PI - 0.05, cameraPhi - dy * 0.005));
    beginInteraction(); requestRender();
  }
  function onPointerUp(e: PointerEvent) {
    isDragging = false;
    canvasEl.releasePointerCapture(e.pointerId);
    if (debugWalls) {
      // Wait one rAF so the just-finished drag's frame is actually in the
      // (idle-DPR) framebuffer before we read it back.
      requestAnimationFrame(() => { render(); logCsgDebug(); });
    }
  }
  function onWheel(e: WheelEvent) {
    e.preventDefault();
    cameraDistance = Math.max(0.5, Math.min(500, cameraDistance * (1 + e.deltaY * 0.001)));
    beginInteraction(); requestRender();
  }
  function resize() {
    if (!containerEl || !renderer || !shaderMaterial) return;
    const w = containerEl.clientWidth, h = containerEl.clientHeight;
    renderer.setSize(w, h, false);       // wipes the drawing buffer ...
    shaderMaterial.uniforms.uAspect.value = w / Math.max(1, h);
    render();                            // ... so redraw in the same task (see setRenderScale)
  }

  onMount(() => {
    scene = new THREE.Scene();
    camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    shaderMaterial = new THREE.ShaderMaterial({ uniforms: makeUniforms(), vertexShader: VERTEX_SHADER, fragmentShader: FRAGMENT_SHADER, defines: { HEATMAP: 0 } });
    scene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), shaderMaterial));
    renderer = new THREE.WebGLRenderer({ canvas: canvasEl, antialias: false });
    renderer.setPixelRatio(IDLE_DPR);
    initGpuTimer();
    // Force one compile pass and log diagnostics
    const gl = renderer.getContext();
    renderer.compile(scene, camera);
    try {
      const matProg = (renderer as any).properties?.get?.(shaderMaterial)?.program;
      if (matProg?.diagnostics) {
        console.warn('[csg-viewer] shader diagnostics', matProg.diagnostics);
      }
    } catch (_) { /* ignore */ }
    const maxTex = gl.getParameter(gl.MAX_TEXTURE_SIZE);
    const maxFragUniforms = gl.getParameter(gl.MAX_FRAGMENT_UNIFORM_VECTORS);
    console.log(`[csg-viewer] WebGL MAX_TEXTURE_SIZE=${maxTex} MAX_FRAGMENT_UNIFORM_VECTORS=${maxFragUniforms}`);
    resize(); rebuildAndRender();
    const ro = new ResizeObserver(resize); ro.observe(containerEl);
    return () => ro.disconnect();
  });

  onDestroy(() => {
    if (pendingRenderFrame !== null) cancelAnimationFrame(pendingRenderFrame);
    clearTimeout(interactTimer);
    if (gpuPollHandle !== null) cancelAnimationFrame(gpuPollHandle);
    if (gpuTimer) for (const e of gpuQueries) gpuTimer.gl.deleteQuery(e.q);
    tokenTexture?.dispose(); bvhTexture?.dispose(); instanceTexture?.dispose(); latTexture?.dispose(); renderer?.dispose();
  });
</script>

<div class="csg-viewport" bind:this={containerEl}>
  <canvas bind:this={canvasEl} onpointerdown={onPointerDown} onpointermove={onPointerMove} onpointerup={onPointerUp} onwheel={onWheel}></canvas>
  <div class="overlay">
    <button class="viewport-btn" onclick={resetCamera} title="Reset camera">Reset view</button>
    <button
      class="viewport-btn"
      class:active={debugWalls}
      onclick={() => { debugWalls = !debugWalls; if (debugWalls) logCsgDebug(); }}
      title="Log camera pose + expected-vs-rendered box walls to the console after every drag"
    >Debug walls{debugWalls ? ' ●' : ''}</button>
    <button
      class="viewport-btn"
      class:active={heatMode > 0}
      onclick={cycleHeat}
      title="Cycle the render-cost heat-map: per-pixel work, blue = cheap, red = expensive. Entering/leaving it recompiles the shader once."
    >Cost: {HEAT_MODES[heatMode]}</button>
    {#if !loading && !loadError}
      <span class="count-badge">
        {#if usingInstancing}{prototypeCount} proto · {instanceCount} inst{:else}{instanceCount} cell{instanceCount === 1 ? '' : 's'}{/if}{#if gpuMsLabel} · {gpuMsLabel}{/if}
      </span>
    {/if}
  </div>
  {#if materialLegend.length > 0}
    <div class="legend">{#each materialLegend as m}<span class="legend-item"><i style="background:{m.color}"></i>{m.id}</span>{/each}</div>
  {/if}
  {#if loading}<div class="badge">Loading CSG…</div>{/if}
  {#if loadError}<div class="badge error">{loadError}</div>{/if}
  {#if truncatedSurfaces || truncatedProtos || truncatedInstances || truncatedTokens || truncatedBvh || truncatedGrids}
    <div class="badge warning">Geometry exceeds viewer capacity (max {MAX_PROTOTYPES} prototypes / {MAX_INSTANCES} instances) — partial render.</div>
  {/if}
</div>

<style>
  .csg-viewport { position: relative; width: 100%; height: 100%; background: var(--color-bg-deep); }
  canvas { display: block; width: 100%; height: 100%; cursor: grab; touch-action: none; }
  canvas:active { cursor: grabbing; }
  .overlay { position: absolute; bottom: 12px; left: 12px; display: flex; align-items: center; gap: 8px; }
  .viewport-btn { background: var(--color-bg-panel); border: 1px solid var(--color-border); color: var(--color-subtext); font-size: 11px; padding: 5px 9px; border-radius: 6px; cursor: pointer; }
  .viewport-btn:hover { color: var(--color-text); border-color: var(--color-accent); }
  .viewport-btn.active { color: var(--color-accent-hi); border-color: var(--color-accent); }
  .count-badge { font-size: 10px; font-family: var(--font-mono); color: var(--color-subtext); }
  .legend { position: absolute; top: 12px; left: 12px; display: flex; flex-direction: column; gap: 3px; background: var(--color-bg-panel); border: 1px solid var(--color-border); border-radius: 6px; padding: 6px 8px; max-height: 40%; overflow-y: auto; }
  .legend-item { display: flex; align-items: center; gap: 5px; font-size: 10px; font-family: var(--font-mono); color: var(--color-subtext); }
  .legend-item i { width: 9px; height: 9px; border-radius: 2px; display: inline-block; flex-shrink: 0; }
  .badge { position: absolute; top: 12px; right: 12px; max-width: 320px; font-size: 11px; padding: 6px 10px; border-radius: 6px; background: rgba(6, 182, 212, 0.15); color: var(--color-accent-hi); border: 1px solid var(--color-accent); }
  .badge.error { background: rgba(239, 68, 68, 0.15); color: #f87171; border-color: #ef4444; }
  .badge.warning { background: rgba(245, 158, 11, 0.15); color: #f59e0b; border-color: #f59e0b; top: auto; bottom: 12px; }
</style>