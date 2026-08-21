<script lang="ts">
  // CsgViewportPanel — raymarches the fully expanded CSG (surfaces +
  // region trees) from POST /geometry/csg.
  //
  // Phase C (CSG_VIEWER_SCALING_PLAN.md): when lattice_instances is
  // present, pack one token-texture row per unique prototype cell and one
  // BVH leaf per instance. On a surviving leaf the hit point is shifted
  // into the prototype's local frame before evalRegion runs against the
  // shared token row. Cost becomes O(unique templates) for the expensive
  // work; instance count only affects cheap BVH depth.
  //
  // Non-lattice geometry (Box fill, SinglePlacement, Tier-1/2 cells) is
  // folded in as single-instance "prototypes" so one shader path covers
  // everything. Empty lattice_instances keeps the pre-Phase-C flat path
  // behaviour (still subject to MAX_PROTOTYPES as a soft ceiling).

  import { onMount, onDestroy } from 'svelte';
  import * as THREE from 'three';
  import * as api from '$lib/api';
  import { activeProject } from '../stores/projects.svelte.js';
  import type { CsgGeometry, CsgSurface, RegionNode, LatticeInstance } from '$lib/types';

  // ---- Capacity caps -------------------------------------------------------
  // Surfaces stay high enough for a full assembly's radial layers + box planes.
  // Prototypes = unique region trees (pin layers + non-lattice cells).
  // Instances = placements (pins × layers for lattices, 1 for singles).
  const MAX_SURFACES = 256;
  const MAX_PROTOTYPES = 64;
  const MAX_INSTANCES = 4096;
  const MAX_TOKENS_PER_PROTO = 96;
  const MAX_BVH_NODES = 8192; // binary tree over MAX_INSTANCES leaves

  // ---- Region tree -> flat RPN token stream --------------------------------
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
        return { type: -1, v: new THREE.Vector4(0, 0, 0, 0) };
    }
  }

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
    return b;
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

  function regionIsPotentiallyUnbounded(node: RegionNode): boolean {
    switch (node.op) {
      case 'not':
        return true;
      case 'outside':
        return true;
      case 'inside':
        return false;
      case 'and':
      case 'or':
        return node.items.some(regionIsPotentiallyUnbounded);
    }
  }

  function cellAABB(
    region: RegionNode,
    surfaceById: Map<string, CsgSurface>,
    fallback: AxisBounds,
  ): AxisBounds {
    if (regionIsPotentiallyUnbounded(region)) return fallback;

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

  function translateAABB(b: AxisBounds, dx: number, dy: number, dz: number): AxisBounds {
    return {
      xMin: b.xMin + dx, xMax: b.xMax + dx,
      yMin: b.yMin + dy, yMax: b.yMax + dy,
      zMin: b.zMin + dz, zMax: b.zMax + dz,
    };
  }

  function unionAABB(a: AxisBounds, b: AxisBounds): AxisBounds {
    return {
      xMin: Math.min(a.xMin, b.xMin), xMax: Math.max(a.xMax, b.xMax),
      yMin: Math.min(a.yMin, b.yMin), yMax: Math.max(a.yMax, b.yMax),
      zMin: Math.min(a.zMin, b.zMin), zMax: Math.max(a.zMax, b.zMax),
    };
  }

  // ---- BVH construction (CPU) — leaves are INSTANCES -----------------------
  interface BvhBuildNode {
    bounds: AxisBounds;
    isLeaf: boolean;
    instanceIndex: number;
    left: BvhBuildNode | null;
    right: BvhBuildNode | null;
  }

  function buildBvh(indices: number[], boxes: AxisBounds[]): BvhBuildNode {
    if (indices.length === 1) {
      const i = indices[0];
      return { bounds: boxes[i], isLeaf: true, instanceIndex: i, left: null, right: null };
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
    return { bounds: unionAABB(left.bounds, right.bounds), isLeaf: false, instanceIndex: -1, left, right };
  }

  function writeBvhTexel(data: Float32Array, nodeIdx: number, col: number, x: number, y: number, z: number, w: number) {
    const base = (nodeIdx * 3 + col) * 4;
    data[base] = x; data[base + 1] = y; data[base + 2] = z; data[base + 3] = w;
  }

  function flattenBvh(root: BvhBuildNode, data: Float32Array): number {
    let counter = 0;

    function assign(node: BvhBuildNode): number {
      const idx = counter;
      counter += 1;

      if (idx < MAX_BVH_NODES) {
        const b = node.bounds;
        writeBvhTexel(data, idx, 0, b.xMin, b.yMin, b.zMin, node.isLeaf ? 1 : 0);
        if (node.isLeaf) {
          writeBvhTexel(data, idx, 1, b.xMax, b.yMax, b.zMax, node.instanceIndex);
          writeBvhTexel(data, idx, 2, 0, 0, 0, 0);
        } else {
          writeBvhTexel(data, idx, 1, b.xMax, b.yMax, b.zMax, 0);
        }
      }

      if (!node.isLeaf) {
        assign(node.left!);
        const rightIdx = counter;
        assign(node.right!);
        // left was assigned starting at idx+1; re-read via counter before right
        // Actually we need left index — assign returns root of subtree.
        // Fix: capture left index properly.
        void rightIdx;
      }

      return idx;
    }

    // Proper pre-order with back-filled children:
    counter = 0;
    function assign2(node: BvhBuildNode): number {
      const idx = counter++;
      if (idx >= MAX_BVH_NODES) return idx;

      const b = node.bounds;
      writeBvhTexel(data, idx, 0, b.xMin, b.yMin, b.zMin, node.isLeaf ? 1 : 0);

      if (node.isLeaf) {
        writeBvhTexel(data, idx, 1, b.xMax, b.yMax, b.zMax, node.instanceIndex);
        writeBvhTexel(data, idx, 2, 0, 0, 0, 0);
      } else {
        writeBvhTexel(data, idx, 1, b.xMax, b.yMax, b.zMax, 0);
        const leftIdx = assign2(node.left!);
        const rightIdx = assign2(node.right!);
        writeBvhTexel(data, idx, 2, leftIdx, rightIdx, 0, 0);
      }
      return idx;
    }

    assign2(root);
    return Math.min(counter, MAX_BVH_NODES);
  }

  // ---- Component state ------------------------------------------------------
  let csg = $state<CsgGeometry | null>(null);
  let loading = $state(false);
  let loadError = $state<string | null>(null);
  let truncatedSurfaces = $state(false);
  let truncatedProtos = $state(false);
  let truncatedInstances = $state(false);
  let truncatedTokens = $state(false);
  let instanceCount = $state(0);
  let prototypeCount = $state(0);
  let materialLegend = $state<{ id: string; color: string }[]>([]);
  let usingInstancing = $state(false);

  const projectText = $derived(activeProject().text);

  let fetchHandle: ReturnType<typeof setTimeout> | undefined;
  $effect(() => {
    const text = projectText;
    clearTimeout(fetchHandle);
    fetchHandle = setTimeout(() => load(text), 400);
  });

  async function load(text: string) {
    const t0 = performance.now();
    loading = true;
    loadError = null;
    try {
      csg = await api.geometry.csg(text);
      console.log(`[perf] /geometry/csg fetch: ${(performance.now() - t0).toFixed(1)}ms`);
    } catch (e) {
      loadError = e instanceof Error ? e.message : String(e);
      csg = null;
    } finally {
      loading = false;
      const tRebuild0 = performance.now();
      rebuildAndRender();
      console.log(`[perf] rebuildAndRender: ${(performance.now() - tRebuild0).toFixed(1)}ms`);
    }
  }

  // ---- Three.js scaffolding -------------------------------------------------
  let canvasEl: HTMLCanvasElement;
  let containerEl: HTMLDivElement;
  let renderer: THREE.WebGLRenderer;
  let scene: THREE.Scene;
  let camera: THREE.OrthographicCamera;
  let shaderMaterial: THREE.ShaderMaterial;
  let tokenTexture: THREE.DataTexture | null = null;
  let bvhTexture: THREE.DataTexture | null = null;
  let instanceTexture: THREE.DataTexture | null = null;

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
    #define MAX_PROTOTYPES ${MAX_PROTOTYPES}
    #define MAX_INSTANCES ${MAX_INSTANCES}
    #define TOK_TEX_W ${MAX_TOKENS_PER_PROTO}.0
    #define TOK_TEX_H ${MAX_PROTOTYPES}.0
    #define MAX_TOKEN_ITERS ${MAX_TOKENS_PER_PROTO}

    #define MAX_BVH_NODES ${MAX_BVH_NODES}
    #define BVH_TEX_H ${MAX_BVH_NODES}.0
    #define MAX_BVH_STACK 48
    #define MAX_BVH_ITERS 256

    uniform vec3 uCamPos;
    uniform vec3 uCamForward;
    uniform vec3 uCamRight;
    uniform vec3 uCamUp;
    uniform float uTanHalfFov;
    uniform float uAspect;

    uniform int uSurfCount;
    uniform int uSurfType[MAX_SURFACES];
    uniform vec4 uSurfParams[MAX_SURFACES];

    uniform int uProtoCount;
    uniform vec3 uProtoColor[MAX_PROTOTYPES];
    uniform int uProtoTokenCount[MAX_PROTOTYPES];
    uniform sampler2D uTokenTex;

    // Instance texture: 1 texel per instance — (dx, dy, dz, prototypeIndex)
    uniform sampler2D uInstanceTex;
    uniform int uInstanceCount;

    // BVH over instance AABBs. Leaf w-channel stores instanceIndex.
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

    vec2 fetchToken(int protoIdx, int pairIdx) {
      float u = (float(pairIdx) + 0.5) / TOK_TEX_W;
      float v = (float(protoIdx) + 0.5) / TOK_TEX_H;
      return texture2D(uTokenTex, vec2(u, v)).rg;
    }

    vec4 fetchInstance(int instIdx) {
      float u = (float(instIdx) + 0.5) / float(MAX_INSTANCES);
      return texture2D(uInstanceTex, vec2(u, 0.5));
    }

    vec4 fetchBvhTexel(int nodeIdx, int col) {
      float u = (float(col) + 0.5) / 3.0;
      float v = (float(nodeIdx) + 0.5) / BVH_TEX_H;
      return texture2D(uBvhTex, vec2(u, v));
    }

    float boxSDF(vec3 p, vec3 bmin, vec3 bmax) {
      vec3 c = (bmin + bmax) * 0.5;
      vec3 h = (bmax - bmin) * 0.5;
      vec3 q = abs(p - c) - h;
      return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0);
    }

    // Evaluate a prototype's region tree at a LOCAL point (already inverse-
    // translated into the prototype frame).
    float evalRegion(int protoIdx, vec3 pLocal) {
      int tokenCount = uProtoTokenCount[protoIdx];
      float stack[16];
      int sp = 0;
      for (int i = 0; i < MAX_TOKEN_ITERS; i++) {
        if (i >= tokenCount) break;
        vec2 tok = fetchToken(protoIdx, i);
        int op = int(tok.x + 0.5);
        int operand = int(tok.y + 0.5);
        if (op == 0) {
          stack[sp] = surfaceSDF(operand, pLocal); sp++;
        } else if (op == 1) {
          stack[sp] = -surfaceSDF(operand, pLocal); sp++;
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

    // BVH over instances. Leaf → instance → (offset, protoIdx) → eval in local frame.
    float sceneSDF(vec3 p, out int hitProto) {
      hitProto = -1;
      float best = 1.0e6;

      if (uBvhNodeCount <= 0) return best;

      int stack[MAX_BVH_STACK];
      int sp = 0;
      stack[sp] = 0; sp++;

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
        if (boxD >= best) continue;

        if (t0.w > 0.5) {
          int instIdx = int(t1.w + 0.5);
          if (instIdx < 0 || instIdx >= uInstanceCount) continue;
          vec4 inst = fetchInstance(instIdx);
          vec3 pLocal = p - inst.xyz;
          int protoIdx = int(inst.w + 0.5);
          if (protoIdx < 0 || protoIdx >= uProtoCount) continue;
          float exact = evalRegion(protoIdx, pLocal);
          if (exact < best) { best = exact; hitProto = protoIdx; }
        } else {
          vec4 t2 = fetchBvhTexel(nodeIdx, 2);
          int leftIdx = int(t2.x + 0.5);
          int rightIdx = int(t2.y + 0.5);
          if (sp < MAX_BVH_STACK) { stack[sp] = leftIdx; sp++; }
          if (sp < MAX_BVH_STACK) { stack[sp] = rightIdx; sp++; }
        }
      }

      return best;
    }

    vec3 calcNormal(vec3 pWorld, int protoIdx, vec3 offset) {
      // Finite differences in world space; for pure translation the local
      // gradient equals the world gradient.
      vec2 e = vec2(0.001, 0.0);
      vec3 pLocal = pWorld - offset;
      float dx = evalRegion(protoIdx, pLocal + e.xyy) - evalRegion(protoIdx, pLocal - e.xyy);
      float dy = evalRegion(protoIdx, pLocal + e.yxy) - evalRegion(protoIdx, pLocal - e.yxy);
      float dz = evalRegion(protoIdx, pLocal + e.yyx) - evalRegion(protoIdx, pLocal - e.yyx);
      return normalize(vec3(dx, dy, dz));
    }

    // Second sceneSDF pass to recover the winning instance offset for normals.
    // (Keeps the primary traversal free of extra out-params.)
    vec3 findHitOffset(vec3 p, int wantProto) {
      if (uBvhNodeCount <= 0) return vec3(0.0);
      int stack[MAX_BVH_STACK];
      int sp = 0;
      stack[sp] = 0; sp++;
      float best = 1.0e6;
      vec3 bestOff = vec3(0.0);
      for (int iter = 0; iter < MAX_BVH_ITERS; iter++) {
        if (sp <= 0) break;
        sp--;
        int nodeIdx = stack[sp];
        if (nodeIdx < 0 || nodeIdx >= uBvhNodeCount) continue;
        vec4 t0 = fetchBvhTexel(nodeIdx, 0);
        vec4 t1 = fetchBvhTexel(nodeIdx, 1);
        float boxD = boxSDF(p, t0.xyz, t1.xyz);
        if (boxD >= best) continue;
        if (t0.w > 0.5) {
          int instIdx = int(t1.w + 0.5);
          if (instIdx < 0 || instIdx >= uInstanceCount) continue;
          vec4 inst = fetchInstance(instIdx);
          int protoIdx = int(inst.w + 0.5);
          if (protoIdx != wantProto) continue;
          float exact = evalRegion(protoIdx, p - inst.xyz);
          if (exact < best) { best = exact; bestOff = inst.xyz; }
        } else {
          vec4 t2 = fetchBvhTexel(nodeIdx, 2);
          if (sp < MAX_BVH_STACK) { stack[sp] = int(t2.x + 0.5); sp++; }
          if (sp < MAX_BVH_STACK) { stack[sp] = int(t2.y + 0.5); sp++; }
        }
      }
      return bestOff;
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

      // Root AABB pre-test: if ray misses entire scene, skip march.
      float t = 0.0;
      int hitProto = -1;
      for (int i = 0; i < 160; i++) {
        vec3 p = ro + rd * t;
        int proto;
        float d = sceneSDF(p, proto);
        if (d < 0.002) { hitProto = proto; break; }
        t += max(d, 0.002);
        if (t > 800.0) break;
      }

      if (hitProto >= 0) {
        vec3 p = ro + rd * t;
        vec3 offset = findHitOffset(p, hitProto);
        vec3 n = calcNormal(p, hitProto, offset);
        float diff = max(dot(n, normalize(vec3(0.4, 0.6, 0.8))), 0.0);
        float ambient = 0.35;
        col = uProtoColor[hitProto] * (ambient + (1.0 - ambient) * diff);
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
      uProtoCount: { value: 0 },
      uProtoColor: { value: Array.from({ length: MAX_PROTOTYPES }, () => new THREE.Color(0, 0, 0)) },
      uProtoTokenCount: { value: new Array(MAX_PROTOTYPES).fill(0) },
      uTokenTex: { value: null as THREE.DataTexture | null },
      uInstanceTex: { value: null as THREE.DataTexture | null },
      uInstanceCount: { value: 0 },
      uBvhTex: { value: null as THREE.DataTexture | null },
      uBvhNodeCount: { value: 0 },
    };
  }

  const _camPos = new THREE.Vector3();
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
  }

  /** Names of every cell that belongs to a lattice expansion (all pins). */
  function latticeCellNameSet(lis: LatticeInstance[]): Set<string> {
    const set = new Set<string>();
    for (const li of lis) {
      // Prototype cells are named `{lattice_name}_0_layer{k}` (see templates.py).
      // Expanded pins use `{lattice_name}_{i}_layer{k}`.
      const layerSuffixes: string[] = [];
      for (const c of li.prototype_cells) {
        const name = c.name ?? '';
        const m = name.match(/_0_layer(\d+)$/);
        if (m) layerSuffixes.push(`_layer${m[1]}`);
        else if (name) layerSuffixes.push(''); // fallback: exact name only for pin0
      }
      for (let i = 0; i < li.instances.length; i++) {
        for (const suf of layerSuffixes) {
          if (suf) set.add(`${li.lattice_name}_${i}${suf}`);
          else {
            // no layer suffix parse — mark pin0 prototype names only
            for (const c of li.prototype_cells) if (c.name) set.add(c.name);
          }
        }
      }
    }
    return set;
  }

  function rebuildAndRender() {
    if (!shaderMaterial) return;
    const u = shaderMaterial.uniforms;

    if (!csg) {
      u.uProtoCount.value = 0;
      u.uInstanceCount.value = 0;
      u.uSurfCount.value = 0;
      u.uBvhNodeCount.value = 0;
      instanceCount = 0;
      prototypeCount = 0;
      materialLegend = [];
      usingInstancing = false;
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
    u.uSurfCount.value = surfacesUsed.length;

    // ---- Build prototypes + instances -------------------------------------
    interface ProtoEntry {
      region: RegionNode;
      material_id: string;
      aabb: AxisBounds; // in prototype (pin-0 / identity) frame
    }
    const protos: ProtoEntry[] = [];
    // instances: { dx,dy,dz, protoIdx }
    const instances: { dx: number; dy: number; dz: number; protoIdx: number }[] = [];

    const lis = csg.lattice_instances ?? [];
    usingInstancing = lis.length > 0;

    if (usingInstancing) {
      // One prototype entry per non-void prototype_cell of each lattice.
      for (const li of lis) {
        const baseProtoIdx = protos.length;
        const protoCells = li.prototype_cells.filter((c) => c.material_id != null);
        // Surfaces for AABB: prefer lattice prototype_surfaces, fall back to global map
        const protoSurfById = new Map(surfaceById);
        for (const s of li.prototype_surfaces) protoSurfById.set(s.id, s);

        for (const cell of protoCells) {
          if (protos.length >= MAX_PROTOTYPES) break;
          const aabb = cellAABB(cell.region, protoSurfById, sceneBounds);
          protos.push({ region: cell.region, material_id: cell.material_id!, aabb });
        }
        const numLayers = protos.length - baseProtoIdx;

        for (const off of li.instances) {
          for (let k = 0; k < numLayers; k++) {
            if (instances.length >= MAX_INSTANCES) break;
            instances.push({
              dx: off[0], dy: off[1], dz: off[2],
              protoIdx: baseProtoIdx + k,
            });
          }
        }
      }

      // Non-lattice cells (fill, single placements, Tier-1/2): single instance at origin.
      const latticeNames = latticeCellNameSet(lis);
      for (const cell of csg.cells) {
        if (cell.material_id == null) continue;
        if (cell.name && latticeNames.has(cell.name)) continue;
        if (protos.length >= MAX_PROTOTYPES) break;
        const aabb = cellAABB(cell.region, surfaceById, sceneBounds);
        const protoIdx = protos.length;
        protos.push({ region: cell.region, material_id: cell.material_id, aabb });
        if (instances.length < MAX_INSTANCES) {
          instances.push({ dx: 0, dy: 0, dz: 0, protoIdx });
        }
      }
    } else {
      // Flat path: each non-void cell is its own prototype with one identity instance.
      const nonVoid = csg.cells.filter((c) => c.material_id != null);
      for (const cell of nonVoid) {
        if (protos.length >= MAX_PROTOTYPES) break;
        const aabb = cellAABB(cell.region, surfaceById, sceneBounds);
        const protoIdx = protos.length;
        protos.push({ region: cell.region, material_id: cell.material_id!, aabb });
        if (instances.length < MAX_INSTANCES) {
          instances.push({ dx: 0, dy: 0, dz: 0, protoIdx });
        }
      }
    }

    truncatedProtos = protos.length >= MAX_PROTOTYPES;
    truncatedInstances = instances.length >= MAX_INSTANCES;

    // ---- Pack prototype token texture + colours ---------------------------
    const protoColor = u.uProtoColor.value as THREE.Color[];
    const protoTokenCount = u.uProtoTokenCount.value as number[];
    const texData = new Float32Array(MAX_TOKENS_PER_PROTO * MAX_PROTOTYPES * 4);
    let anyTokenTrunc = false;
    const legendSeen = new Map<string, string>();

    protos.forEach((proto, pi) => {
      const [r, g, b] = hashColor(proto.material_id);
      protoColor[pi].setRGB(r, g, b);
      if (!legendSeen.has(proto.material_id)) {
        legendSeen.set(proto.material_id, `#${protoColor[pi].getHexString()}`);
      }

      const tokens: number[] = [];
      flattenRegion(proto.region, surfIndex, tokens);
      const pairCount = tokens.length / 2;
      const used = Math.min(pairCount, MAX_TOKENS_PER_PROTO);
      if (pairCount > MAX_TOKENS_PER_PROTO) anyTokenTrunc = true;
      protoTokenCount[pi] = used;
      for (let p = 0; p < used; p++) {
        const texelIdx = (pi * MAX_TOKENS_PER_PROTO + p) * 4;
        texData[texelIdx] = tokens[p * 2];
        texData[texelIdx + 1] = tokens[p * 2 + 1];
      }
    });
    for (let pi = protos.length; pi < MAX_PROTOTYPES; pi++) {
      protoColor[pi].setRGB(0, 0, 0);
      protoTokenCount[pi] = 0;
    }

    truncatedTokens = anyTokenTrunc;
    prototypeCount = protos.length;
    instanceCount = instances.length;
    materialLegend = [...legendSeen.entries()].map(([id, color]) => ({ id, color }));

    u.uProtoCount.value = protos.length;

    tokenTexture?.dispose();
    tokenTexture = new THREE.DataTexture(texData, MAX_TOKENS_PER_PROTO, MAX_PROTOTYPES, THREE.RGBAFormat, THREE.FloatType);
    tokenTexture.magFilter = THREE.NearestFilter;
    tokenTexture.minFilter = THREE.NearestFilter;
    tokenTexture.generateMipmaps = false;
    tokenTexture.needsUpdate = true;
    u.uTokenTex.value = tokenTexture;

    // ---- Instance texture -------------------------------------------------
    const instData = new Float32Array(MAX_INSTANCES * 4);
    const instanceBoxes: AxisBounds[] = [];
    instances.forEach((inst, i) => {
      instData[i * 4] = inst.dx;
      instData[i * 4 + 1] = inst.dy;
      instData[i * 4 + 2] = inst.dz;
      instData[i * 4 + 3] = inst.protoIdx;
      const base = protos[inst.protoIdx].aabb;
      instanceBoxes.push(translateAABB(base, inst.dx, inst.dy, inst.dz));
    });

    instanceTexture?.dispose();
    instanceTexture = new THREE.DataTexture(instData, MAX_INSTANCES, 1, THREE.RGBAFormat, THREE.FloatType);
    instanceTexture.magFilter = THREE.NearestFilter;
    instanceTexture.minFilter = THREE.NearestFilter;
    instanceTexture.generateMipmaps = false;
    instanceTexture.needsUpdate = true;
    u.uInstanceTex.value = instanceTexture;
    u.uInstanceCount.value = instances.length;

    // ---- BVH over instance AABBs ------------------------------------------
    const tBvh0 = performance.now();
    const bvhTexData = new Float32Array(3 * MAX_BVH_NODES * 4);
    let bvhNodeCount = 0;
    if (instances.length > 0) {
      const root = buildBvh(instances.map((_, i) => i), instanceBoxes);
      bvhNodeCount = flattenBvh(root, bvhTexData);
    }
    u.uBvhNodeCount.value = bvhNodeCount;
    console.log(
      `[perf] BVH build: ${(performance.now() - tBvh0).toFixed(2)}ms, ` +
      `nodes=${bvhNodeCount}, protos=${protos.length}, instances=${instances.length}, ` +
      `instancing=${usingInstancing}`
    );

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

  let frameCount = 0;
  let frameTimeSum = 0;
  let frameTimeMax = 0;
  let pointerMoveCount = 0;

  function render() {
    if (!renderer) return;
    updateCameraUniforms();
    const t0 = performance.now();
    renderer.render(scene, camera);
    const dt = performance.now() - t0;

    frameCount++;
    frameTimeSum += dt;
    frameTimeMax = Math.max(frameTimeMax, dt);

    if (frameCount % 30 === 0) {
      const size = renderer.getSize(new THREE.Vector2());
      console.log(
        `[perf] avg=${(frameTimeSum / 30).toFixed(2)}ms max=${frameTimeMax.toFixed(2)}ms ` +
        `(${(1000 / (frameTimeSum / 30)).toFixed(0)} fps) ` +
        `canvas=${size.x}x${size.y} dpr=${renderer.getPixelRatio()} ` +
        `protos=${prototypeCount} instances=${instanceCount} ` +
        `bvhNodes=${shaderMaterial.uniforms.uBvhNodeCount.value} ` +
        `instancing=${usingInstancing} ` +
        `renders=${frameCount} pointermoves=${pointerMoveCount}`
      );
      frameTimeSum = 0;
      frameTimeMax = 0;
    }
  }

  let isDragging = false;
  let lastX = 0, lastY = 0;
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
    pointerMoveCount++;
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
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));

    const gl = renderer.getContext();
    try {
      const dbgInfo = gl.getExtension('WEBGL_debug_renderer_info');
      const rendererStr = dbgInfo
        ? gl.getParameter(dbgInfo.UNMASKED_RENDERER_WEBGL)
        : gl.getParameter(gl.RENDERER);
      console.log('[perf] GPU renderer:', rendererStr);
    } catch (e) {
      console.log('[perf] Could not query GPU renderer info:', e);
    }

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
    instanceTexture?.dispose();
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
      <span class="count-badge">
        {#if usingInstancing}
          {prototypeCount} proto · {instanceCount} inst
        {:else}
          {instanceCount} cell{instanceCount === 1 ? '' : 's'}
        {/if}
      </span>
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
  {#if truncatedSurfaces || truncatedProtos || truncatedInstances || truncatedTokens}
    <div class="badge warning">
      Geometry exceeds viewer capacity
      (max {MAX_PROTOTYPES} prototypes / {MAX_INSTANCES} instances / {MAX_SURFACES} surfaces) —
      showing a partial render.
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
