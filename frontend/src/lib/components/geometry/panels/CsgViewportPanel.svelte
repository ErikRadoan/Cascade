<script lang="ts">
  // CsgViewportPanel — renders the FULLY EXPANDED CSG (surfaces + region
  // trees), not the template-aware SceneBuilder output ViewportPanel uses.
  //
  // Why this exists: SceneBuilder (scene_builder_service.py) only knows
  // how to draw FuelPin/Box specifically — see its _build_placed(). It has
  // no path for arbitrary Union/Subtraction component types, however the
  // expander builds them, because it never looks at Region trees at all.
  // This panel does — it fetches /geometry/csg (surfaces + Region trees)
  // and renders it with a raymarched signed-distance-field shader, so it
  // renders whatever the geometry actually is, not a fixed pair of shapes.
  //
  // Why raymarching, not a Three.js mesh:
  //   - domain/geometry.py's Region set (Inside/Outside/Intersection/
  //     Union/Complement) maps exactly onto SDF combinators: an Inside is
  //     a plane/cylinder/sphere distance field (all exact SDFs), Outside
  //     negates it, Intersection = max(), Union = min(), Complement =
  //     negate(). So union/subtraction (Intersection + Complement) render
  //     correctly here with NO new rendering code once the DSL/expander
  //     grow those component types — only the region tree needs to change.
  //   - Mesh-based CSG (boolean ops on triangle meshes) would need a full
  //     mesh rebuild on every keystroke in the editor; a raymarch shader
  //     just re-uploads uniforms/a texture and redraws.
  //
  // PERFORMANCE (BVH-pruned raymarch — see plan doc, "lag fix"):
  //   sceneSDF() previously ran the expensive per-cell evalRegion() (a
  //   full RPN region-tree walk) for EVERY cell, at EVERY one of the 160
  //   raymarch steps, per pixel — O(cells x steps x pixels), completely
  //   unconditionally. An earlier interim patch added a flat per-cell
  //   bounding-sphere test (one sphere per cell, no hierarchy); this is
  //   the real fix that supersedes it.
  //
  //   A BVH is built on the CPU (buildBvh()/flattenBvh() below) over each
  //   cell's AABB (computed from the surfaces its region tree actually
  //   references — cellAABB()), leaves = 1 cell each, split on the
  //   longest axis at the median (no SAH — not worth it at <=48 leaves).
  //   It's flattened into a small texture (3 texels/node: bmin+isLeaf,
  //   bmax+cellIndex, leftChild+rightChild) and traversed in the shader
  //   with an explicit stack (GLSL ES 1.00 has no recursion).
  //
  //   Soundness: pruning uses the EXACT signed box distance (boxSDF, see
  //   shader below), not a sphere. For a box B that fully contains a
  //   shape S, boxSDF(p) <= trueSDF_S(p) for every point p, inside or
  //   outside B — any ball around p that fits inside S also fits inside
  //   B (S subset-of B), so B's "room to grow" at p can never be smaller
  //   than S's. That inequality holds regardless of sign, which is what
  //   lets internal BVH nodes (unions of children's boxes) be pruned
  //   too, not just leaves — the sphere patch this replaces only had a
  //   sound bound for points OUTSIDE the sphere, which is why it had to
  //   fall back to "evaluate everything the point is inside" instead of
  //   skipping whole subtrees. A node is skipped outright whenever its
  //   box distance is already >= the best hit found so far in the
  //   traversal; only surviving leaves ever call the expensive
  //   evalRegion(). This is a genuine spatial hierarchy (O(log cells)
  //   box tests instead of O(cells)), not a heuristic margin — it scales
  //   to far more than 48 cells without the pruning quality degrading,
  //   which is what would eventually be needed if MAX_CELLS is raised.

  import { onMount, onDestroy } from 'svelte';
  import * as THREE from 'three';
  import * as api from '$lib/api';
  import { activeProject } from '../stores/projects.svelte.js';
  import type { CsgGeometry, CsgSurface, RegionNode } from '$lib/types';

  // ---- Capacity caps — see block comment above ----------------------------
  const MAX_SURFACES = 96;
  const MAX_CELLS = 48;
  const MAX_TOKENS_PER_CELL = 96; // region-tree token PAIRS per cell

  // ---- Region tree -> flat RPN token stream --------------------------------
  // opcode, operand pairs. INSIDE/OUTSIDE operand = surface index;
  // AND/OR/NOT ignore their operand slot (kept for a uniform stride of 2).
  const OP_INSIDE = 0, OP_OUTSIDE = 1, OP_AND = 2, OP_OR = 3, OP_NOT = 4;

  function flattenRegion(node: RegionNode, surfIndex: Map<string, number>, out: number[]) {
    switch (node.op) {
      case 'inside':
        out.push(OP_INSIDE, surfIndex.get(node.surface) ?? -1);
        return;
      case 'outside':
        out.push(OP_OUTSIDE, surfIndex.get(node.surface) ?? -1);
        return;
      case 'and': {
        if (node.items.length === 0) { out.push(OP_INSIDE, -1); return; }
        flattenRegion(node.items[0], surfIndex, out);
        for (let i = 1; i < node.items.length; i++) {
          flattenRegion(node.items[i], surfIndex, out);
          out.push(OP_AND, 0);
        }
        return;
      }
      case 'or': {
        if (node.items.length === 0) { out.push(OP_INSIDE, -1); return; }
        flattenRegion(node.items[0], surfIndex, out);
        for (let i = 1; i < node.items.length; i++) {
          flattenRegion(node.items[i], surfIndex, out);
          out.push(OP_OR, 0);
        }
        return;
      }
      case 'not':
        flattenRegion(node.item, surfIndex, out);
        out.push(OP_NOT, 0);
        return;
    }
  }

  // Collect every surface NAME referenced anywhere in a region tree
  // (Inside AND Outside both count — see cellAABB()'s doc comment
  // for why restricting to just Inside would under-bound a Box-shaped
  // cell, whose interior is Outside(lo) + Inside(hi) per axis).
  function surfaceIdsInRegion(node: RegionNode, out: string[]) {
    switch (node.op) {
      case 'inside':
      case 'outside':
        out.push(node.surface);
        return;
      case 'and':
      case 'or':
        for (const item of node.items) surfaceIdsInRegion(item, out);
        return;
      case 'not':
        surfaceIdsInRegion(node.item, out);
        return;
    }
  }

  // ---- Surface packing ------------------------------------------------------
  // Matches the shader's surfaceSDF() reading convention exactly — see
  // fragment shader below. Aliases (x/x0 etc.) mirror the backend adapter's
  // _resolve_param in openmc_adapter.py.
  function num(params: Record<string, number>, canon: string, alias: string, fallback = 0): number {
    if (params[canon] != null) return params[canon];
    if (params[alias] != null) return params[alias];
    return fallback;
  }

  function packSurface(s: CsgSurface): { type: number; v: THREE.Vector4 } {
    const p = s.params;
    switch (s.type) {
      case 'plane_x': return { type: 0, v: new THREE.Vector4(num(p, 'x0', 'x'), 0, 0, 0) };
      case 'plane_y': return { type: 1, v: new THREE.Vector4(0, num(p, 'y0', 'y'), 0, 0) };
      case 'plane_z': return { type: 2, v: new THREE.Vector4(0, 0, num(p, 'z0', 'z'), 0) };
      case 'cylinder_x':
        return { type: 3, v: new THREE.Vector4(0, num(p, 'y0', 'y'), num(p, 'z0', 'z'), num(p, 'r', 'r', 1)) };
      case 'cylinder_y':
        return { type: 4, v: new THREE.Vector4(num(p, 'x0', 'x'), 0, num(p, 'z0', 'z'), num(p, 'r', 'r', 1)) };
      case 'cylinder_z':
        return { type: 5, v: new THREE.Vector4(num(p, 'x0', 'x'), num(p, 'y0', 'y'), 0, num(p, 'r', 'r', 1)) };
      case 'sphere':
        return { type: 6, v: new THREE.Vector4(num(p, 'x0', 'x'), num(p, 'y0', 'y'), num(p, 'z0', 'z'), num(p, 'r', 'r', 1)) };
      default:
        // cone_z / torus — not emitted by the expander today (same gap
        // GeometryPlotPanel.svelte's surfaceF() flags). Render as "never
        // matches" rather than throw.
        return { type: -1, v: new THREE.Vector4(0, 0, 0, 0) };
    }
  }

  // Deterministic material -> color, independent of SceneBuilder's palette
  // (that palette only knows named materials it recognizes; this works for
  // any material_id, including ones from future component types).
  function hashColor(id: string): [number, number, number] {
    let h = 2166136261;
    for (let i = 0; i < id.length; i++) { h ^= id.charCodeAt(i); h = Math.imul(h, 16777619); }
    return hslToRgb(((h >>> 0) % 360) / 360, 0.55, 0.55);
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

  // ---- Bounding-box helpers -------------------------------------------------
  // Shared shape: accumulate min/max per axis over a set of surfaces,
  // exactly like computeBounds() does for the whole scene below — a
  // surface's own offset is treated as a candidate bound on EITHER side of
  // an axis, regardless of whether it's referenced via Inside or Outside
  // in the region tree. That's a loose-but-safe approximation (it doesn't
  // know which side of a plane the cell's material is actually on), but
  // for real reactor geometry (each axis bounded by one "lo" and one "hi"
  // plane, or one cylinder radius) it produces a tight box in practice.
  interface AxisBounds { xMin: number; xMax: number; yMin: number; yMax: number; zMin: number; zMax: number; }

  function accumulateSurfaceBounds(b: AxisBounds, s: CsgSurface): AxisBounds {
    const p = s.params;
    if (s.type === 'plane_x') {
      const v = num(p, 'x0', 'x');
      return { ...b, xMin: Math.min(b.xMin, v), xMax: Math.max(b.xMax, v) };
    }
    if (s.type === 'plane_y') {
      const v = num(p, 'y0', 'y');
      return { ...b, yMin: Math.min(b.yMin, v), yMax: Math.max(b.yMax, v) };
    }
    if (s.type === 'plane_z') {
      const v = num(p, 'z0', 'z');
      return { ...b, zMin: Math.min(b.zMin, v), zMax: Math.max(b.zMax, v) };
    }
    if (s.type === 'cylinder_z') {
      const x0 = num(p, 'x0', 'x'), y0 = num(p, 'y0', 'y'), r = num(p, 'r', 'r', 1);
      return {
        ...b,
        xMin: Math.min(b.xMin, x0 - r), xMax: Math.max(b.xMax, x0 + r),
        yMin: Math.min(b.yMin, y0 - r), yMax: Math.max(b.yMax, y0 + r),
      };
    }
    if (s.type === 'cylinder_x') {
      const y0 = num(p, 'y0', 'y'), z0 = num(p, 'z0', 'z'), r = num(p, 'r', 'r', 1);
      return {
        ...b,
        yMin: Math.min(b.yMin, y0 - r), yMax: Math.max(b.yMax, y0 + r),
        zMin: Math.min(b.zMin, z0 - r), zMax: Math.max(b.zMax, z0 + r),
      };
    }
    if (s.type === 'cylinder_y') {
      const x0 = num(p, 'x0', 'x'), z0 = num(p, 'z0', 'z'), r = num(p, 'r', 'r', 1);
      return {
        ...b,
        xMin: Math.min(b.xMin, x0 - r), xMax: Math.max(b.xMax, x0 + r),
        zMin: Math.min(b.zMin, z0 - r), zMax: Math.max(b.zMax, z0 + r),
      };
    }
    if (s.type === 'sphere') {
      const x0 = num(p, 'x0', 'x'), y0 = num(p, 'y0', 'y'), z0 = num(p, 'z0', 'z'), r = num(p, 'r', 'r', 1);
      return {
        ...b,
        xMin: Math.min(b.xMin, x0 - r), xMax: Math.max(b.xMax, x0 + r),
        yMin: Math.min(b.yMin, y0 - r), yMax: Math.max(b.yMax, y0 + r),
        zMin: Math.min(b.zMin, z0 - r), zMax: Math.max(b.zMax, z0 + r),
      };
    }
    return b; // cone_z / torus — unsupported, contributes nothing
  }

  function computeBounds(csg: CsgGeometry): AxisBounds {
    let b: AxisBounds = {
      xMin: Infinity, xMax: -Infinity, yMin: Infinity, yMax: -Infinity, zMin: Infinity, zMax: -Infinity,
    };
    for (const s of csg.surfaces) b = accumulateSurfaceBounds(b, s);
    if (!isFinite(b.xMin)) return { xMin: -5, xMax: 5, yMin: -5, yMax: 5, zMin: -5, zMax: 5 };
    if (!isFinite(b.zMin)) { b = { ...b, zMin: -5, zMax: 5 }; }
    return b;
  }

  // Unbounded-region detection — cellAABB() below only looks at WHICH
  // surfaces a region tree references, not whether Inside/Outside/
  // Complement actually bounds the resulting solid. That's safe for
  // FuelPin/Box (every referenced surface there imposes a real bound),
  // but not in general: `Complement(Inside(sphere))` ("everywhere
  // outside this sphere") references only the sphere, whose own params
  // are perfectly finite — so cellAABB would return a tight box hugging
  // the sphere for a region that's actually unbounded. Since every
  // number involved is finite, the existing `!isFinite(...)` guard in
  // cellAABB doesn't catch it, and the BVH would then prune away
  // camera-ray points that are geometrically inside the cell but outside
  // its undersized box — silent holes in the render, no error.
  //
  // Walks the tree looking for the two patterns that break boundedness:
  // a Complement anywhere, or a bare Outside(cylinder/sphere)
  // (Outside(plane) is fine — it only bounds from one side, same as
  // Inside(plane), so it doesn't introduce unboundedness on its own; a
  // cylinder/sphere's Outside is the one that opens up to infinity).
  // Deliberately conservative, not exact: doesn't try to prove that some
  // deeper AND-branch still bounds things (e.g. `Box AND NOT Sphere` IS
  // actually bounded, but this flags it anyway) — false positives just
  // cost pruning quality for that one cell, never correctness.
  function regionIsPotentiallyUnbounded(node: RegionNode): boolean {
    switch (node.op) {
      case 'not':
        return true;
      case 'outside':
        return true; // conservative: covers cylinder/sphere; harmless overkill for plane
      case 'inside':
        return false;
      case 'and':
      case 'or':
        return node.items.some(regionIsPotentiallyUnbounded);
    }
  }

  /**
   * AABB for ONE cell, derived only from the surfaces its own region tree
   * references (see surfaceIdsInRegion), padded 2% so a BVH leaf strictly
   * contains the true shape. Falls back to `fallback` (the whole scene's
   * bounds) when a cell references no bounding-relevant surfaces at all,
   * or when the region is potentially unbounded by construction (see
   * regionIsPotentiallyUnbounded) — must never produce an under-sized box
   * for a shape that isn't actually contained by it, since that would
   * silently break the BVH's pruning correctness.
   */
  function cellAABB(
    region: RegionNode,
    surfaceById: Map<string, CsgSurface>,
    fallback: AxisBounds,
  ): AxisBounds {
    if (regionIsPotentiallyUnbounded(region)) {
      return fallback;
    }

    const names: string[] = [];
    surfaceIdsInRegion(region, names);

    let b: AxisBounds = {
      xMin: Infinity, xMax: -Infinity, yMin: Infinity, yMax: -Infinity, zMin: Infinity, zMax: -Infinity,
    };
    for (const n of names) {
      const s = surfaceById.get(n);
      if (s) b = accumulateSurfaceBounds(b, s);
    }
    if (!isFinite(b.xMin) || !isFinite(b.yMin) || !isFinite(b.zMin)) b = fallback;
    if (!isFinite(b.zMin)) b = { ...b, zMin: fallback.zMin, zMax: fallback.zMax };

    const pad = 0.02 * Math.max(b.xMax - b.xMin, b.yMax - b.yMin, b.zMax - b.zMin, 1);
    return {
      xMin: b.xMin - pad, xMax: b.xMax + pad,
      yMin: b.yMin - pad, yMax: b.yMax + pad,
      zMin: b.zMin - pad, zMax: b.zMax + pad,
    };
  }

  function unionAABB(a: AxisBounds, b: AxisBounds): AxisBounds {
    return {
      xMin: Math.min(a.xMin, b.xMin), xMax: Math.max(a.xMax, b.xMax),
      yMin: Math.min(a.yMin, b.yMin), yMax: Math.max(a.yMax, b.yMax),
      zMin: Math.min(a.zMin, b.zMin), zMax: Math.max(a.zMax, b.zMax),
    };
  }

  // ---- BVH construction (CPU) ------------------------------------------
  // Simple top-down median-split over cell AABBs, one cell per leaf. No
  // SAH (surface-area heuristic) — at <=MAX_CELLS=48 leaves the build
  // cost and tree quality difference aren't worth the complexity; revisit
  // if MAX_CELLS is ever raised significantly. See the PERFORMANCE block
  // comment at the top of this file for why the resulting tree is safe to
  // prune with plain box-distance tests (both leaves AND internal nodes).

  const MAX_BVH_NODES = 128; // full binary tree over 48 leaves needs <= 95 nodes — comfortable headroom

  interface BvhBuildNode {
    bounds: AxisBounds;
    isLeaf: boolean;
    cellIndex: number;          // valid only when isLeaf
    left: BvhBuildNode | null;  // valid only when !isLeaf
    right: BvhBuildNode | null;
  }

  function buildBvh(indices: number[], boxes: AxisBounds[]): BvhBuildNode {
    if (indices.length === 1) {
      const i = indices[0];
      return { bounds: boxes[i], isLeaf: true, cellIndex: i, left: null, right: null };
    }

    let combined = boxes[indices[0]];
    for (let k = 1; k < indices.length; k++) combined = unionAABB(combined, boxes[indices[k]]);

    const spanX = combined.xMax - combined.xMin;
    const spanY = combined.yMax - combined.yMin;
    const spanZ = combined.zMax - combined.zMin;
    const axis: 'x' | 'y' | 'z' = spanX >= spanY && spanX >= spanZ ? 'x' : spanY >= spanZ ? 'y' : 'z';

    const centroid = (i: number) => {
      const b = boxes[i];
      if (axis === 'x') return (b.xMin + b.xMax) / 2;
      if (axis === 'y') return (b.yMin + b.yMax) / 2;
      return (b.zMin + b.zMax) / 2;
    };
    const sorted = [...indices].sort((a, b) => centroid(a) - centroid(b));
    const mid = Math.floor(sorted.length / 2);

    const left = buildBvh(sorted.slice(0, mid), boxes);
    const right = buildBvh(sorted.slice(mid), boxes);
    return { bounds: unionAABB(left.bounds, right.bounds), isLeaf: false, cellIndex: -1, left, right };
  }

  // Row layout per node: 3 texels (RGBA) —
  //   col 0: (bmin.x, bmin.y, bmin.z, isLeaf ? 1 : 0)
  //   col 1: (bmax.x, bmax.y, bmax.z, cellIndex)        [leaf only]
  //   col 2: (leftChildIdx, rightChildIdx, 0, 0)        [internal only]
  function writeBvhTexel(data: Float32Array, nodeIdx: number, col: number, x: number, y: number, z: number, w: number) {
    const base = (nodeIdx * 3 + col) * 4;
    data[base] = x; data[base + 1] = y; data[base + 2] = z; data[base + 3] = w;
  }

  /**
   * Flatten the built tree into `data` (pre-order: a node's own texels are
   * written on entry, its children's subtree indices are back-filled into
   * its own col-2 texel once known). Returns the total node count used
   * (root is always index 0). Silently stops writing past MAX_BVH_NODES —
   * unreachable in practice since the cell cap already bounds tree size,
   * kept as defensive insurance rather than an unchecked assumption.
   */
  function flattenBvh(root: BvhBuildNode, data: Float32Array): number {
    let counter = 0;

    function assign(node: BvhBuildNode): number {
      const idx = counter;
      counter += 1;

      if (idx < MAX_BVH_NODES) {
        const b = node.bounds;
        writeBvhTexel(data, idx, 0, b.xMin, b.yMin, b.zMin, node.isLeaf ? 1 : 0);
        if (node.isLeaf) {
          writeBvhTexel(data, idx, 1, b.xMax, b.yMax, b.zMax, node.cellIndex);
          writeBvhTexel(data, idx, 2, 0, 0, 0, 0);
        } else {
          writeBvhTexel(data, idx, 1, b.xMax, b.yMax, b.zMax, 0);
        }
      }

      if (!node.isLeaf) {
        const leftIdx = counter;
        assign(node.left!);
        const rightIdx = counter;
        assign(node.right!);
        if (idx < MAX_BVH_NODES) {
          writeBvhTexel(data, idx, 2, leftIdx, rightIdx, 0, 0);
        }
      }

      return idx;
    }

    assign(root);
    return Math.min(counter, MAX_BVH_NODES);
  }

  // ---- Component state ------------------------------------------------------
  let csg = $state<CsgGeometry | null>(null);
  let loading = $state(false);
  let loadError = $state<string | null>(null);
  let truncatedSurfaces = $state(false);
  let truncatedCells = $state(false);
  let truncatedTokens = $state(false);
  let cellCount = $state(0);
  let materialLegend = $state<{ id: string; color: string }[]>([]);

  const projectText = $derived(activeProject().text);

  let fetchHandle: ReturnType<typeof setTimeout> | undefined;
  $effect(() => {
    const text = projectText; // dependency
    clearTimeout(fetchHandle);
    fetchHandle = setTimeout(() => load(text), 400);
  });

  async function load(text: string) {
    loading = true;
    loadError = null;
    try {
      csg = await api.geometry.csg(text);
    } catch (e) {
      loadError = e instanceof Error ? e.message : String(e);
      csg = null;
    } finally {
      loading = false;
      rebuildAndRender();
    }
  }

  // ---- Three.js scaffolding: one full-screen quad, no meshes -----------------
  let canvasEl: HTMLCanvasElement;
  let containerEl: HTMLDivElement;
  let renderer: THREE.WebGLRenderer;
  let scene: THREE.Scene;
  let camera: THREE.OrthographicCamera;
  let shaderMaterial: THREE.ShaderMaterial;
  let tokenTexture: THREE.DataTexture | null = null;
  let bvhTexture: THREE.DataTexture | null = null;

  let cameraTheta = $state(Math.PI / 4);
  let cameraPhi = $state(Math.PI / 3);
  let cameraDistance = $state(8);
  let cameraTarget = new THREE.Vector3(0, 0, 0);
  let hasFramedOnce = false;

  const VERTEX_SHADER = `
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = vec4(position.xy, 0.0, 1.0);
    }
  `;

  const FRAGMENT_SHADER = `
    precision highp float;

    #define MAX_SURFACES ${MAX_SURFACES}
    #define MAX_CELLS ${MAX_CELLS}
    #define TOK_TEX_W ${MAX_TOKENS_PER_CELL}.0
    #define TOK_TEX_H ${MAX_CELLS}.0
    #define MAX_TOKEN_ITERS ${MAX_TOKENS_PER_CELL}

    #define MAX_BVH_NODES ${MAX_BVH_NODES}
    #define BVH_TEX_H ${MAX_BVH_NODES}.0
    #define MAX_BVH_STACK 32
    #define MAX_BVH_ITERS 160

    uniform vec3 uCamPos;
    uniform vec3 uCamForward;
    uniform vec3 uCamRight;
    uniform vec3 uCamUp;
    uniform float uTanHalfFov;
    uniform float uAspect;

    uniform int uSurfCount;
    uniform int uSurfType[MAX_SURFACES];
    uniform vec4 uSurfParams[MAX_SURFACES];

    uniform int uCellCount;
    uniform vec3 uCellColor[MAX_CELLS];
    uniform int uCellTokenCount[MAX_CELLS];
    uniform sampler2D uTokenTex;

    // BVH over cell AABBs — see PERFORMANCE comment at the top of the
    // <script> block. 3 texels per node (width=3, height=MAX_BVH_NODES):
    //   col 0: (bmin.x, bmin.y, bmin.z, isLeaf ? 1 : 0)
    //   col 1: (bmax.x, bmax.y, bmax.z, cellIndex)         [leaf only]
    //   col 2: (leftChildIdx, rightChildIdx, -, -)         [internal only]
    uniform sampler2D uBvhTex;
    uniform int uBvhNodeCount;

    varying vec2 vUv;

    float surfaceSDF(int idx, vec3 p) {
      if (idx < 0) return 1.0e6;
      int t = uSurfType[idx];
      vec4 pr = uSurfParams[idx];
      if (t == 0) return p.x - pr.x;
      if (t == 1) return p.y - pr.y;
      if (t == 2) return p.z - pr.z;
      if (t == 3) { vec2 d = vec2(p.y - pr.y, p.z - pr.z); return length(d) - pr.w; }
      if (t == 4) { vec2 d = vec2(p.x - pr.x, p.z - pr.z); return length(d) - pr.w; }
      if (t == 5) { vec2 d = vec2(p.x - pr.x, p.y - pr.y); return length(d) - pr.w; }
      if (t == 6) { return length(p - pr.xyz) - pr.w; }
      return 1.0e6;
    }

    vec2 fetchToken(int cellIdx, int pairIdx) {
      float u = (float(pairIdx) + 0.5) / TOK_TEX_W;
      float v = (float(cellIdx) + 0.5) / TOK_TEX_H;
      return texture2D(uTokenTex, vec2(u, v)).rg;
    }

    vec4 fetchBvhTexel(int nodeIdx, int col) {
      float u = (float(col) + 0.5) / 3.0;
      float v = (float(nodeIdx) + 0.5) / BVH_TEX_H;
      return texture2D(uBvhTex, vec2(u, v));
    }

    // Exact signed distance to an axis-aligned box — negative inside,
    // positive outside, zero on the boundary. Valid as a BVH pruning
    // bound for BOTH leaves and internal nodes — see the PERFORMANCE
    // comment at the top of the <script> block for the proof sketch
    // (any ball around p that fits inside the true shape also fits
    // inside a box containing that shape, so the box can never claim
    // less "room" at p than the shape actually has).
    float boxSDF(vec3 p, vec3 bmin, vec3 bmax) {
      vec3 c = (bmin + bmax) * 0.5;
      vec3 h = (bmax - bmin) * 0.5;
      vec3 q = abs(p - c) - h;
      return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0);
    }

    // Region -> signed distance. Inside/Outside read a surface's own exact
    // SDF (negated for Outside); Intersection = max (AND), Union = min
    // (OR), Complement = negate (NOT) — the standard SDF-CSG combinators.
    // This is what makes union/subtraction "just work" once the DSL grows
    // component types that produce those Region shapes. This is the
    // EXPENSIVE per-cell evaluation the BVH traversal below exists to
    // avoid calling except at surviving leaves.
    float evalRegion(int cellIdx, vec3 p) {
      int tokenCount = uCellTokenCount[cellIdx];
      float stack[16];
      int sp = 0;
      for (int i = 0; i < MAX_TOKEN_ITERS; i++) {
        if (i >= tokenCount) break;
        vec2 tok = fetchToken(cellIdx, i);
        int op = int(tok.x + 0.5);
        int operand = int(tok.y + 0.5);
        if (op == 0) {
          stack[sp] = surfaceSDF(operand, p); sp++;
        } else if (op == 1) {
          stack[sp] = -surfaceSDF(operand, p); sp++;
        } else if (op == 2) {
          float b = stack[sp-1]; sp--;
          float a = stack[sp-1]; sp--;
          stack[sp] = max(a, b); sp++;
        } else if (op == 3) {
          float b = stack[sp-1]; sp--;
          float a = stack[sp-1]; sp--;
          stack[sp] = min(a, b); sp++;
        } else if (op == 4) {
          stack[sp-1] = -stack[sp-1];
        }
      }
      return sp > 0 ? stack[0] : 1.0e6;
    }

    // Iterative BVH descent (GLSL ES 1.00 has no recursion — explicit
    // stack, same dynamic-local-array-indexing pattern evalRegion already
    // uses above). At each popped node: box-test it against the best hit
    // found so far and skip the whole subtree if it can't possibly beat
    // that (boxSDF is a valid lower bound for every cell in that
    // subtree — see the comment on boxSDF()). Only leaves that survive
    // pruning ever call the expensive evalRegion().
    float sceneSDF(vec3 p, out int hitCell) {
      hitCell = -1;
      float best = 1.0e6;

      if (uBvhNodeCount <= 0) return best;

      int stack[MAX_BVH_STACK];
      int sp = 0;
      stack[sp] = 0; sp++; // root is always node index 0

      for (int iter = 0; iter < MAX_BVH_ITERS; iter++) {
        if (sp <= 0) break;
        sp--;
        int nodeIdx = stack[sp];
        if (nodeIdx < 0 || nodeIdx >= uBvhNodeCount) continue;

        vec4 t0 = fetchBvhTexel(nodeIdx, 0);
        vec4 t1 = fetchBvhTexel(nodeIdx, 1);
        vec3 bmin = t0.xyz;
        vec3 bmax = t1.xyz;

        float boxD = boxSDF(p, bmin, bmax);
        if (boxD >= best) continue; // subtree cannot contain anything closer than what we already have

        if (t0.w > 0.5) {
          // Leaf — exact evaluation.
          int cellIdx = int(t1.w + 0.5);
          float exact = evalRegion(cellIdx, p);
          if (exact < best) { best = exact; hitCell = cellIdx; }
        } else {
          // Internal — push both children, let the box test on their own
          // pop prune them if they turn out not to matter.
          vec4 t2 = fetchBvhTexel(nodeIdx, 2);
          int leftIdx = int(t2.x + 0.5);
          int rightIdx = int(t2.y + 0.5);
          if (sp < MAX_BVH_STACK) { stack[sp] = leftIdx; sp++; }
          if (sp < MAX_BVH_STACK) { stack[sp] = rightIdx; sp++; }
        }
      }

      return best;
    }

    vec3 calcNormal(vec3 p, int cellIdx) {
      vec2 e = vec2(0.001, 0.0);
      float dx = evalRegion(cellIdx, p + e.xyy) - evalRegion(cellIdx, p - e.xyy);
      float dy = evalRegion(cellIdx, p + e.yxy) - evalRegion(cellIdx, p - e.yxy);
      float dz = evalRegion(cellIdx, p + e.yyx) - evalRegion(cellIdx, p - e.yyx);
      return normalize(vec3(dx, dy, dz));
    }

    void main() {
      vec2 ndc = vUv * 2.0 - 1.0;
      vec3 rd = normalize(
        uCamForward
        + uCamRight * (ndc.x * uTanHalfFov * uAspect)
        + uCamUp    * (ndc.y * uTanHalfFov)
      );
      vec3 ro = uCamPos;

      vec3 col = vec3(0.059, 0.090, 0.165);

      float t = 0.0;
      int hitCell = -1;
      for (int i = 0; i < 160; i++) {
        vec3 p = ro + rd * t;
        int cell;
        float d = sceneSDF(p, cell);
        if (d < 0.002) { hitCell = cell; break; }
        t += max(d, 0.002);
        if (t > 800.0) break;
      }

      if (hitCell >= 0) {
        vec3 p = ro + rd * t;
        vec3 n = calcNormal(p, hitCell);
        float diff = max(dot(n, normalize(vec3(0.4, 0.6, 0.8))), 0.0);
        float ambient = 0.35;
        col = uCellColor[hitCell] * (ambient + (1.0 - ambient) * diff);
      }

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
      uSurfType: { value: new Array(MAX_SURFACES).fill(-1) },
      uSurfParams: { value: Array.from({ length: MAX_SURFACES }, () => new THREE.Vector4()) },
      uCellCount: { value: 0 },
      uCellColor: { value: Array.from({ length: MAX_CELLS }, () => new THREE.Color(0, 0, 0)) },
      uCellTokenCount: { value: new Array(MAX_CELLS).fill(0) },
      uTokenTex: { value: null as THREE.DataTexture | null },
      uBvhTex: { value: null as THREE.DataTexture | null },
      uBvhNodeCount: { value: 0 },
    };
  }

  function updateCameraUniforms() {
    if (!shaderMaterial) return;
    const camX = cameraTarget.x + cameraDistance * Math.sin(cameraPhi) * Math.cos(cameraTheta);
    const camY = cameraTarget.y + cameraDistance * Math.sin(cameraPhi) * Math.sin(cameraTheta);
    const camZ = cameraTarget.z + cameraDistance * Math.cos(cameraPhi);
    const camPos = new THREE.Vector3(camX, camY, camZ);

    const forward = cameraTarget.clone().sub(camPos).normalize();
    const worldUp = new THREE.Vector3(0, 0, 1); // OpenMC z is "up" — same convention as Viewport3D's toThree()
    let right = new THREE.Vector3().crossVectors(forward, worldUp);
    if (right.lengthSq() < 1e-8) right.set(1, 0, 0);
    right.normalize();
    const up = new THREE.Vector3().crossVectors(right, forward).normalize();

    const u = shaderMaterial.uniforms;
    u.uCamPos.value.copy(camPos);
    u.uCamForward.value.copy(forward);
    u.uCamRight.value.copy(right);
    u.uCamUp.value.copy(up);
  }

  function rebuildAndRender() {
    if (!shaderMaterial) return;
    const u = shaderMaterial.uniforms;

    if (!csg) {
      u.uCellCount.value = 0;
      u.uSurfCount.value = 0;
      cellCount = 0;
      materialLegend = [];
      render();
      return;
    }

    const surfacesUsed = csg.surfaces.slice(0, MAX_SURFACES);
    truncatedSurfaces = csg.surfaces.length > MAX_SURFACES;
    const surfIndex = new Map(surfacesUsed.map((s, i) => [s.id, i]));
    const surfaceById = new Map(surfacesUsed.map((s) => [s.id, s]));
    const sceneBounds = computeBounds(csg);

    const surfType = u.uSurfType.value as number[];
    const surfParams = u.uSurfParams.value as THREE.Vector4[];
    surfacesUsed.forEach((s, i) => {
      const packed = packSurface(s);
      surfType[i] = packed.type;
      surfParams[i].copy(packed.v);
    });
    for (let i = surfacesUsed.length; i < MAX_SURFACES; i++) { surfType[i] = -1; surfParams[i].set(0, 0, 0, 0); }

    const nonVoid = csg.cells.filter((c) => c.material_id != null);
    const cellsUsed = nonVoid.slice(0, MAX_CELLS);
    truncatedCells = nonVoid.length > MAX_CELLS;

    const cellColor = u.uCellColor.value as THREE.Color[];
    const cellTokenCount = u.uCellTokenCount.value as number[];
    const texData = new Float32Array(MAX_TOKENS_PER_CELL * MAX_CELLS * 4);
    let anyCellTruncated = false;
    const legendSeen = new Map<string, string>();
    const cellBoxes: AxisBounds[] = [];

    cellsUsed.forEach((cell, ci) => {
      const matId = cell.material_id!;
      const [r, g, b] = hashColor(matId);
      cellColor[ci].setRGB(r, g, b);
      if (!legendSeen.has(matId)) legendSeen.set(matId, `#${cellColor[ci].getHexString()}`);

      const tokens: number[] = [];
      flattenRegion(cell.region, surfIndex, tokens);
      const pairCount = tokens.length / 2;
      const used = Math.min(pairCount, MAX_TOKENS_PER_CELL);
      if (pairCount > MAX_TOKENS_PER_CELL) anyCellTruncated = true;
      cellTokenCount[ci] = used;
      for (let p = 0; p < used; p++) {
        const texelIdx = (ci * MAX_TOKENS_PER_CELL + p) * 4;
        texData[texelIdx] = tokens[p * 2];
        texData[texelIdx + 1] = tokens[p * 2 + 1];
      }

      cellBoxes.push(cellAABB(cell.region, surfaceById, sceneBounds));
    });
    for (let ci = cellsUsed.length; ci < MAX_CELLS; ci++) {
      cellColor[ci].setRGB(0, 0, 0);
      cellTokenCount[ci] = 0;
    }

    truncatedTokens = anyCellTruncated;
    cellCount = cellsUsed.length;
    materialLegend = [...legendSeen.entries()].map(([id, color]) => ({ id, color }));

    u.uSurfCount.value = surfacesUsed.length;
    u.uCellCount.value = cellsUsed.length;

    tokenTexture?.dispose();
    tokenTexture = new THREE.DataTexture(texData, MAX_TOKENS_PER_CELL, MAX_CELLS, THREE.RGBAFormat, THREE.FloatType);
    tokenTexture.magFilter = THREE.NearestFilter;
    tokenTexture.minFilter = THREE.NearestFilter;
    tokenTexture.generateMipmaps = false;
    tokenTexture.needsUpdate = true;
    u.uTokenTex.value = tokenTexture;

    // Build the BVH over this frame's cell AABBs and upload it as a
    // texture — see the PERFORMANCE comment at the top of the <script>
    // block. Node count 0 (no cells) is a valid, cheap no-op case the
    // shader handles directly (sceneSDF returns early).
    const bvhTexData = new Float32Array(3 * MAX_BVH_NODES * 4);
    let bvhNodeCount = 0;
    if (cellsUsed.length > 0) {
      const root = buildBvh(cellsUsed.map((_, i) => i), cellBoxes);
      bvhNodeCount = flattenBvh(root, bvhTexData);
    }
    u.uBvhNodeCount.value = bvhNodeCount;

    bvhTexture?.dispose();
    bvhTexture = new THREE.DataTexture(bvhTexData, 3, MAX_BVH_NODES, THREE.RGBAFormat, THREE.FloatType);
    bvhTexture.magFilter = THREE.NearestFilter;
    bvhTexture.minFilter = THREE.NearestFilter;
    bvhTexture.generateMipmaps = false;
    bvhTexture.needsUpdate = true;
    u.uBvhTex.value = bvhTexture;

    if (!hasFramedOnce) { resetCamera(); hasFramedOnce = true; }
    else { render(); }
  }

  function resetCamera() {
    cameraTheta = Math.PI / 4;
    cameraPhi = Math.PI / 3;
    if (csg) {
      const b = computeBounds(csg);
      const span = Math.max(b.xMax - b.xMin, b.yMax - b.yMin, b.zMax - b.zMin, 1);
      cameraDistance = span * 1.8;
      cameraTarget = new THREE.Vector3((b.xMin + b.xMax) / 2, (b.yMin + b.yMax) / 2, (b.zMin + b.zMax) / 2);
    } else {
      cameraDistance = 8;
      cameraTarget = new THREE.Vector3(0, 0, 0);
    }
    updateCameraUniforms();
    render();
  }

  function render() {
    if (!renderer) return;
    updateCameraUniforms();
    renderer.render(scene, camera);
  }

  // ---- Orbit / zoom (same feel as Viewport3D / ResultsViewport3D) -----------
  let isDragging = false;
  let lastX = 0, lastY = 0;

  // pointermove can fire far faster than the display refresh rate — browsers
  // don't coalesce it to rAF the way they do some other input events — and
  // render() is a full per-pixel raymarch. Calling render() synchronously
  // from the DOM event handler meant an orbit-drag could issue many full
  // raymarches per displayed frame. Camera-state math stays cheap and
  // per-event; the expensive draw call is batched to at most once per
  // animation frame. This throttles how OFTEN a frame draws — it's
  // complementary to, not a substitute for, the bounding-sphere pruning
  // above, which reduces how expensive EACH frame is.
  let renderQueued = false;
  let pendingRenderFrame: number | null = null;

  function requestRender() {
    if (renderQueued) return;
    renderQueued = true;
    pendingRenderFrame = requestAnimationFrame(() => {
      renderQueued = false;
      pendingRenderFrame = null;
      render();
    });
  }

  function onPointerDown(e: PointerEvent) {
    isDragging = true; lastX = e.clientX; lastY = e.clientY;
    canvasEl.setPointerCapture(e.pointerId);
  }
  function onPointerMove(e: PointerEvent) {
    if (!isDragging) return;
    const dx = e.clientX - lastX, dy = e.clientY - lastY;
    lastX = e.clientX; lastY = e.clientY;
    cameraTheta -= dx * 0.005;
    cameraPhi = Math.max(0.05, Math.min(Math.PI - 0.05, cameraPhi - dy * 0.005));
    requestRender();
  }
  function onPointerUp(e: PointerEvent) {
    isDragging = false;
    canvasEl.releasePointerCapture(e.pointerId);
  }
  function onWheel(e: WheelEvent) {
    e.preventDefault();
    cameraDistance = Math.max(0.5, Math.min(500, cameraDistance * (1 + e.deltaY * 0.001)));
    requestRender();
  }

  function resize() {
    if (!containerEl || !renderer || !shaderMaterial) return;
    const w = containerEl.clientWidth, h = containerEl.clientHeight;
    renderer.setSize(w, h, false);
    shaderMaterial.uniforms.uAspect.value = w / Math.max(1, h);
    requestRender();
  }

  onMount(() => {
    scene = new THREE.Scene();
    camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);

    shaderMaterial = new THREE.ShaderMaterial({
      uniforms: makeUniforms(),
      vertexShader: VERTEX_SHADER,
      fragmentShader: FRAGMENT_SHADER,
    });
    const quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), shaderMaterial);
    scene.add(quad);

    renderer = new THREE.WebGLRenderer({ canvas: canvasEl, antialias: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5)); // shader is per-pixel expensive — cap DPR

    resize();
    rebuildAndRender();

    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(containerEl);
    return () => resizeObserver.disconnect();
  });

  onDestroy(() => {
    if (pendingRenderFrame !== null) cancelAnimationFrame(pendingRenderFrame);
    tokenTexture?.dispose();
    bvhTexture?.dispose();
    renderer?.dispose();
  });
</script>

<div class="csg-viewport" bind:this={containerEl}>
  <canvas
    bind:this={canvasEl}
    onpointerdown={onPointerDown}
    onpointermove={onPointerMove}
    onpointerup={onPointerUp}
    onwheel={onWheel}
  ></canvas>

  <div class="overlay">
    <button class="viewport-btn" onclick={resetCamera} title="Reset camera">Reset view</button>
    {#if !loading && !loadError}
      <span class="count-badge">{cellCount} cell{cellCount === 1 ? '' : 's'}</span>
    {/if}
  </div>

  {#if materialLegend.length > 0}
    <div class="legend">
      {#each materialLegend as m}
        <span class="legend-item"><i style="background:{m.color}"></i>{m.id}</span>
      {/each}
    </div>
  {/if}

  {#if loading}
    <div class="badge">Loading CSG…</div>
  {/if}
  {#if loadError}
    <div class="badge error">{loadError}</div>
  {/if}
  {#if truncatedSurfaces || truncatedCells || truncatedTokens}
    <div class="badge warning">
      Geometry exceeds this viewer's current capacity (max {MAX_CELLS} cells / {MAX_SURFACES} surfaces) —
      showing a partial render. Needs a spatial index to scale further, see file header.
    </div>
  {/if}
</div>

<style>
  .csg-viewport { position: relative; width: 100%; height: 100%; background: var(--color-bg-deep); }
  canvas { display: block; width: 100%; height: 100%; cursor: grab; touch-action: none; }
  canvas:active { cursor: grabbing; }

  .overlay {
    position: absolute; bottom: 12px; left: 12px;
    display: flex; align-items: center; gap: 8px;
  }
  .viewport-btn {
    background: var(--color-bg-panel); border: 1px solid var(--color-border);
    color: var(--color-subtext); font-size: 11px; padding: 5px 9px; border-radius: 6px; cursor: pointer;
  }
  .viewport-btn:hover { color: var(--color-text); border-color: var(--color-accent); }
  .count-badge { font-size: 10px; font-family: var(--font-mono); color: var(--color-subtext); }

  .legend {
    position: absolute; top: 12px; left: 12px;
    display: flex; flex-direction: column; gap: 3px;
    background: var(--color-bg-panel); border: 1px solid var(--color-border);
    border-radius: 6px; padding: 6px 8px; max-height: 40%; overflow-y: auto;
  }
  .legend-item { display: flex; align-items: center; gap: 5px; font-size: 10px; font-family: var(--font-mono); color: var(--color-subtext); }
  .legend-item i { width: 9px; height: 9px; border-radius: 2px; display: inline-block; flex-shrink: 0; }

  .badge {
    position: absolute; top: 12px; right: 12px; max-width: 320px;
    font-size: 11px; padding: 6px 10px; border-radius: 6px;
    background: rgba(6, 182, 212, 0.15); color: var(--color-accent-hi); border: 1px solid var(--color-accent);
  }
  .badge.error { background: rgba(239, 68, 68, 0.15); color: #f87171; border-color: #ef4444; }
  .badge.warning { background: rgba(245, 158, 11, 0.15); color: #f59e0b; border-color: #f59e0b; top: auto; bottom: 12px; }
</style>