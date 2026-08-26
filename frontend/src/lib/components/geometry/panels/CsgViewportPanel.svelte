<script lang="ts">
  // CsgViewportPanel — CSG ray viewer with Phase A/B/C/D scaling work.
  // Phase D: nested lattices (inner_offsets) + lattice-aware fill (plane-only).
  // Phase C: lattice instancing. Phase B: analytic ray intervals for primary hit.
  // Phase A: BVH, root AABB, tetrahedron normals, adaptive DPR, perf logs.

  import { onMount, onDestroy } from 'svelte';
  import * as THREE from 'three';
  import * as api from '$lib/api';
  import { activeProject } from '../stores/projects.svelte.js';
  // Same store ObjectPanel writes — keys are `${projectId}:${placementName}`
  // inside isVisible/toggleVisibility. Must match ObjectPanel's import path.
  import { isVisible, visibility } from '../stores/visibility.svelte.js';
  import { baseGroupName } from '../csgCellGrouping';
  import type { CsgGeometry, CsgSurface, RegionNode, LatticeInstance } from '$lib/types';

  /** Placement key for visibility — same grouping as ObjectPanel rows. */
  function visibilityKey(name: string | null | undefined): string | null {
    if (!name) return null;
    return baseGroupName(name);
  }

  const MAX_SURFACES = 256;
  const MAX_PROTOTYPES = 64;
  const MAX_INSTANCES = 4096;
  // Fill cell = 6 box planes + Outside(each pin outer). A 10×10 lattice needs
  // ~200 RPN pairs; 17×17 needs ~600. Cap of 96 was truncating the AND chain
  // so the water solid lost constraints and pins appeared to punch through sides.
  const MAX_TOKENS_PER_PROTO = 256;
  const MAX_BVH_NODES = 8192;
  const INTERACT_DPR = 0.65;
  const IDLE_DPR = Math.min(typeof window !== 'undefined' ? window.devicePixelRatio : 1.5, 1.5);

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
        const items: RegionNode[] = [];
        for (const it of node.items) {
          const k = planeOnlyRegion(it, surfaceById);
          if (k) items.push(k);
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

  function packSurface(s: CsgSurface): { type: number; v: THREE.Vector4 } {
    const p = s.params;
    switch (s.type) {
      case 'plane_x': return { type: 0, v: new THREE.Vector4(num(p, 'x0', 'x'), 0, 0, 0) };
      case 'plane_y': return { type: 1, v: new THREE.Vector4(0, num(p, 'y0', 'y'), 0, 0) };
      case 'plane_z': return { type: 2, v: new THREE.Vector4(0, 0, num(p, 'z0', 'z'), 0) };
      case 'cylinder_x': return { type: 3, v: new THREE.Vector4(0, num(p, 'y0', 'y'), num(p, 'z0', 'z'), num(p, 'r', 'r', 1)) };
      case 'cylinder_y': return { type: 4, v: new THREE.Vector4(num(p, 'x0', 'x'), 0, num(p, 'z0', 'z'), num(p, 'r', 'r', 1)) };
      case 'cylinder_z': return { type: 5, v: new THREE.Vector4(num(p, 'x0', 'x'), num(p, 'y0', 'y'), 0, num(p, 'r', 'r', 1)) };
      case 'sphere': return { type: 6, v: new THREE.Vector4(num(p, 'x0', 'x'), num(p, 'y0', 'y'), num(p, 'z0', 'z'), num(p, 'r', 'r', 1)) };
      default: return { type: -1, v: new THREE.Vector4(0, 0, 0, 0) };
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
    return b;
  }

  function computeBounds(csg: CsgGeometry): AxisBounds {
    let b: AxisBounds = { xMin: Infinity, xMax: -Infinity, yMin: Infinity, yMax: -Infinity, zMin: Infinity, zMax: -Infinity };
    for (const s of csg.surfaces) b = accumulateSurfaceBounds(b, s);
    // Per-axis fallback — a missing plane_y must not leave yMin/yMax at ±Infinity
    // (that breaks cameraDistance and the shader root AABB).
    if (!isFinite(b.xMin) || !isFinite(b.xMax)) { b.xMin = -5; b.xMax = 5; }
    if (!isFinite(b.yMin) || !isFinite(b.yMax)) { b.yMin = -5; b.yMax = 5; }
    if (!isFinite(b.zMin) || !isFinite(b.zMax)) { b.zMin = -5; b.zMax = 5; }
    return b;
  }

  function cellAABB(region: RegionNode, surfaceById: Map<string, CsgSurface>, fallback: AxisBounds): AxisBounds {
    // Always accumulate bounds from surfaces referenced by the region (cylinders
    // give tight xy even when the cell also has Outside(...)). Only fall back
    // per-axis when that axis is missing — do NOT replace the whole box with
    // sceneBounds solely because the region contains an Outside node.
    const names: string[] = [];
    surfaceIdsInRegion(region, names);
    let b: AxisBounds = { xMin: Infinity, xMax: -Infinity, yMin: Infinity, yMax: -Infinity, zMin: Infinity, zMax: -Infinity };
    for (const n of names) { const s = surfaceById.get(n); if (s) b = accumulateSurfaceBounds(b, s); }
    if (!isFinite(b.xMin) || !isFinite(b.xMax)) { b.xMin = fallback.xMin; b.xMax = fallback.xMax; }
    if (!isFinite(b.yMin) || !isFinite(b.yMax)) { b.yMin = fallback.yMin; b.yMax = fallback.yMax; }
    if (!isFinite(b.zMin) || !isFinite(b.zMax)) { b.zMin = fallback.zMin; b.zMax = fallback.zMax; }
    const pad = 0.02 * Math.max(b.xMax - b.xMin, b.yMax - b.yMin, b.zMax - b.zMin, 1);
    return { xMin: b.xMin - pad, xMax: b.xMax + pad, yMin: b.yMin - pad, yMax: b.yMax + pad, zMin: b.zMin - pad, zMax: b.zMax + pad };
  }

  function translateAABB(b: AxisBounds, dx: number, dy: number, dz: number): AxisBounds {
    return { xMin: b.xMin + dx, xMax: b.xMax + dx, yMin: b.yMin + dy, yMax: b.yMax + dy, zMin: b.zMin + dz, zMax: b.zMax + dz };
  }

  function unionAABB(a: AxisBounds, b: AxisBounds): AxisBounds {
    return { xMin: Math.min(a.xMin, b.xMin), xMax: Math.max(a.xMax, b.xMax), yMin: Math.min(a.yMin, b.yMin), yMax: Math.max(a.yMax, b.yMax), zMin: Math.min(a.zMin, b.zMin), zMax: Math.max(a.zMax, b.zMax) };
  }

  interface BvhBuildNode { bounds: AxisBounds; isLeaf: boolean; instanceIndex: number; left: BvhBuildNode | null; right: BvhBuildNode | null; }

  function buildBvh(indices: number[], boxes: AxisBounds[]): BvhBuildNode {
    if (indices.length === 1) {
      const i = indices[0];
      return { bounds: boxes[i], isLeaf: true, instanceIndex: i, left: null, right: null };
    }
    let combined = boxes[indices[0]];
    for (let k = 1; k < indices.length; k++) combined = unionAABB(combined, boxes[indices[k]]);
    const spanX = combined.xMax - combined.xMin, spanY = combined.yMax - combined.yMin, spanZ = combined.zMax - combined.zMin;
    const axis: 'x' | 'y' | 'z' = spanX >= spanY && spanX >= spanZ ? 'x' : spanY >= spanZ ? 'y' : 'z';
    const centroid = (i: number) => { const b = boxes[i]; return axis === 'x' ? (b.xMin + b.xMax) / 2 : axis === 'y' ? (b.yMin + b.yMax) / 2 : (b.zMin + b.zMax) / 2; };
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

  function flattenBvh(root: BvhBuildNode, data: Float32Array): { count: number; truncated: boolean } {
    let counter = 0;
    function assign(node: BvhBuildNode): number {
      const idx = counter++;
      if (idx >= MAX_BVH_NODES) return idx;
      const b = node.bounds;
      writeBvhTexel(data, idx, 0, b.xMin, b.yMin, b.zMin, node.isLeaf ? 1 : 0);
      if (node.isLeaf) {
        writeBvhTexel(data, idx, 1, b.xMax, b.yMax, b.zMax, node.instanceIndex);
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
  let loading = $state(false);
  let loadError = $state<string | null>(null);
  let truncatedSurfaces = $state(false);
  let truncatedProtos = $state(false);
  let truncatedInstances = $state(false);
  let truncatedTokens = $state(false);
  let truncatedBvh = $state(false);
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

  // Re-pack protos/instances when an Objects-panel eye toggle flips.
  // Touch every key so deep $state mutations invalidate this effect
  // (reading only the object ref can miss property writes in some runes setups).
  $effect(() => {
    for (const k of Object.keys(visibility)) void visibility[k];
    // Also re-run when the map gains its first hide (keys go from 0 → 1).
    void Object.keys(visibility).length;
    if (shaderMaterial && csg) rebuildAndRender();
  });

  async function load(text: string) {
    const t0 = performance.now();
    loading = true; loadError = null;
    try {
      csg = await api.geometry.csg(text);
      console.log(`[perf] /geometry/csg fetch: ${(performance.now() - t0).toFixed(1)}ms`);
    } catch (e) {
      loadError = e instanceof Error ? e.message : String(e);
      csg = null;
    } finally {
      loading = false;
      const t1 = performance.now();
      rebuildAndRender();
      console.log(`[perf] rebuildAndRender: ${(performance.now() - t1).toFixed(1)}ms`);
    }
  }

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
  let interactTimer: ReturnType<typeof setTimeout> | undefined;

  const VERTEX_SHADER = `varying vec2 vUv; void main(){ vUv=uv; gl_Position=vec4(position.xy,0.0,1.0); }`;

  // Phase B: analytic intervals for primary hit; SDF tetrahedron normals; Phase C instancing.
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
    // Large lattices (e.g. 17x17 x layers) need a high iter budget; 256 was
    // aborting traversal mid-tree and caused angle-dependent missing pins.
    #define MAX_BVH_STACK 64
    #define MAX_BVH_ITERS 4096
    #define INF 1.0e6
    #define NEG_INF -1.0e6
    #define LEAF_MARCH_STEPS 96

    uniform vec3 uCamPos, uCamForward, uCamRight, uCamUp;
    uniform float uTanHalfFov, uAspect;
    uniform int uSurfCount;
    uniform int uSurfType[MAX_SURFACES];
    uniform vec4 uSurfParams[MAX_SURFACES];
    uniform int uProtoCount;
    uniform vec3 uProtoColor[MAX_PROTOTYPES];
    uniform int uProtoTokenCount[MAX_PROTOTYPES];
    uniform int uProtoIsFill[MAX_PROTOTYPES];
    uniform sampler2D uTokenTex, uInstanceTex, uBvhTex;
    uniform int uInstanceCount, uBvhNodeCount;
    uniform vec3 uSceneBMin, uSceneBMax;
    varying vec2 vUv;

    float surfaceSDF(int idx, vec3 p) {
      if (idx < 0) return INF;
      int t = uSurfType[idx]; vec4 pr = uSurfParams[idx];
      if (t == 0) return p.x - pr.x;
      if (t == 1) return p.y - pr.y;
      if (t == 2) return p.z - pr.z;
      if (t == 3) return length(vec2(p.y - pr.y, p.z - pr.z)) - pr.w;
      if (t == 4) return length(vec2(p.x - pr.x, p.z - pr.z)) - pr.w;
      if (t == 5) return length(vec2(p.x - pr.x, p.y - pr.y)) - pr.w;
      if (t == 6) return length(p - pr.xyz) - pr.w;
      return INF;
    }

    // Analytic ray ∩ inside-of-surface → (tEnter, tExit). Empty if miss.
    vec2 surfaceInterval(int idx, vec3 ro, vec3 rd) {
      if (idx < 0) return vec2(INF, NEG_INF);
      int t = uSurfType[idx]; vec4 pr = uSurfParams[idx];
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

    vec2 fetchToken(int protoIdx, int pairIdx) {
      return texture2D(uTokenTex, vec2((float(pairIdx)+0.5)/TOK_TEX_W, (float(protoIdx)+0.5)/TOK_TEX_H)).rg;
    }
    vec4 fetchInstance(int instIdx) {
      return texture2D(uInstanceTex, vec2((float(instIdx)+0.5)/float(MAX_INSTANCES), 0.5));
    }
    vec4 fetchBvhTexel(int nodeIdx, int col) {
      return texture2D(uBvhTex, vec2((float(col)+0.5)/3.0, (float(nodeIdx)+0.5)/BVH_TEX_H));
    }
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

    float evalRegion(int protoIdx, vec3 pLocal) {
      int tokenCount = uProtoTokenCount[protoIdx];
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

    // Phase B: interval CSG along ray in local frame → entry t (or INF)
    float evalIntervalEntry(int protoIdx, vec3 ro, vec3 rd) {
      int tokenCount = uProtoTokenCount[protoIdx];
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
          // Complement in t: (-inf, tEnter) U (tExit, +inf) — take the nearer forward piece
          // Represent as single interval is lossy; use two-slot approximation:
          // For solid modeling CSG with bounded cells, complement of a bounded solid
          // is handled via De Morgan at higher tree levels. For a leaf Outside(S),
          // interval is complement of surfaceInterval.
          if (inn.x > inn.y) { stack[sp++] = vec2(NEG_INF, INF); }
          else {
            // Prefer the front complement segment that can be hit from outside
            stack[sp++] = vec2(inn.y, INF); // outside beyond exit (common for cladding Outside(inner))
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
          vec2 A = stack[sp-1];
          if (A.x > A.y) stack[sp-1] = vec2(NEG_INF, INF);
          else stack[sp-1] = vec2(A.y, INF);
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

    // Traverse BVH; for each leaf hybrid-hit in local frame:
    //   1) analytic interval as a seed (fast when CSG is simple Inside-heavy)
    //   2) SDF sphere-trace clamped to the leaf AABB interval (robust for
    //      Outside/Complement, which single-interval CSG cannot represent)
    // Returns best t; outs proto + instance offset for shading.
    float sceneHit(vec3 ro, vec3 rd, out int hitProto, out vec3 hitOff) {
      // Track pin vs fill hits separately. Plane-only fill is a solid box (no
      // pin holes); without priority, the shared top/bot plane makes fill win
      // every ray from above and pins disappear past ~7 pins (when plane-only
      // activates). Pin wins on near-ties and whenever it is clearly nearer.
      hitProto = -1; hitOff = vec3(0.0);
      float bestPin = INF; int pinProto = -1; vec3 pinOff = vec3(0.0);
      float bestFill = INF; int fillProto = -1; vec3 fillOff = vec3(0.0);
      if (uBvhNodeCount <= 0) return INF;
      int stack[MAX_BVH_STACK]; int sp = 0; stack[sp++] = 0;
      for (int iter = 0; iter < MAX_BVH_ITERS; iter++) {
        if (sp <= 0) break;
        int nodeIdx = stack[--sp];
        if (nodeIdx < 0 || nodeIdx >= uBvhNodeCount) continue;
        vec4 t0 = fetchBvhTexel(nodeIdx, 0);
        vec4 t1 = fetchBvhTexel(nodeIdx, 1);
        float bestAny = min(bestPin, bestFill);
        vec2 bi = boxInterval(ro, rd, t0.xyz, t1.xyz);
        if (bi.x > bi.y || bi.x >= bestAny) continue;
        if (t0.w > 0.5) {
          int instIdx = int(t1.w + 0.5);
          if (instIdx < 0 || instIdx >= uInstanceCount) continue;
          vec4 inst = fetchInstance(instIdx);
          int protoIdx = int(inst.w + 0.5);
          if (protoIdx < 0 || protoIdx >= uProtoCount) continue;
          bool isFill = uProtoIsFill[protoIdx] > 0;
          vec3 roL = ro - inst.xyz;
          float tStart = max(bi.x, 0.0);
          float tEnd = min(bi.y, isFill ? bestFill : bestPin);
          if (tStart >= tEnd) continue;

          float tHit = evalIntervalEntry(protoIdx, roL, rd);
          if (tHit < tStart || tHit >= tEnd) tHit = tStart;

          for (int k = 0; k < LEAF_MARCH_STEPS; k++) {
            if (tHit >= tEnd) break;
            float d = evalRegion(protoIdx, roL + rd * tHit);
            if (d < 0.002) {
              if (tHit >= 0.0) {
                if (isFill) {
                  if (tHit < bestFill) { bestFill = tHit; fillProto = protoIdx; fillOff = inst.xyz; }
                } else {
                  if (tHit < bestPin) { bestPin = tHit; pinProto = protoIdx; pinOff = inst.xyz; }
                }
              }
              break;
            }
            tHit += max(d, 0.002);
          }
        } else {
          vec4 t2 = fetchBvhTexel(nodeIdx, 2);
          int leftIdx = int(t2.x + 0.5);
          int rightIdx = int(t2.y + 0.5);
          vec4 l0 = fetchBvhTexel(leftIdx, 0);
          vec4 l1 = fetchBvhTexel(leftIdx, 1);
          vec4 r0 = fetchBvhTexel(rightIdx, 0);
          vec4 r1 = fetchBvhTexel(rightIdx, 1);
          float tL = boxInterval(ro, rd, l0.xyz, l1.xyz).x;
          float tR = boxInterval(ro, rd, r0.xyz, r1.xyz).x;
          if (tL > tR) {
            if (sp < MAX_BVH_STACK) stack[sp++] = leftIdx;
            if (sp < MAX_BVH_STACK) stack[sp++] = rightIdx;
          } else {
            if (sp < MAX_BVH_STACK) stack[sp++] = rightIdx;
            if (sp < MAX_BVH_STACK) stack[sp++] = leftIdx;
          }
        }
      }
      // Pin-vs-fill priority:
      // - Strictly nearer hit always wins.
      // - Near-ties: prefer pin only when looking steeply down (top-face map
      //   view). Side/oblique views (even with mild downward tilt) prefer fill
      //   so vertical box walls stay solid instead of showing pin material.
      if (pinProto >= 0 && fillProto >= 0) {
        if (bestPin < bestFill - 0.002) {
          hitProto = pinProto; hitOff = pinOff; return bestPin;
        }
        if (bestFill < bestPin - 0.002) {
          hitProto = fillProto; hitOff = fillOff; return bestFill;
        }
        // Near-tie — require steep downward look (rd.z ≈ -1)
        if (rd.z < -0.65) {
          hitProto = pinProto; hitOff = pinOff; return bestPin;
        }
        hitProto = fillProto; hitOff = fillOff; return bestFill;
      }
      if (fillProto >= 0) {
        hitProto = fillProto; hitOff = fillOff; return bestFill;
      }
      if (pinProto >= 0) {
        hitProto = pinProto; hitOff = pinOff; return bestPin;
      }
      return INF;
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
      vec2 rootHit = boxInterval(ro, rd, uSceneBMin, uSceneBMax);
      if (rootHit.x <= rootHit.y && rootHit.y >= 0.0) {
        int hitProto; vec3 hitOff;
        float t = sceneHit(ro, rd, hitProto, hitOff);
        if (hitProto >= 0 && t < INF) {
          vec3 p = ro + rd * t;
          vec3 n = calcNormal(p - hitOff, hitProto);
          float diff = max(dot(n, normalize(vec3(0.4, 0.6, 0.8))), 0.0);
          col = uProtoColor[hitProto] * (0.35 + 0.65 * diff);
        }
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
      uProtoIsFill: { value: new Array(MAX_PROTOTYPES).fill(0) },
      uTokenTex: { value: null as THREE.DataTexture | null },
      uInstanceTex: { value: null as THREE.DataTexture | null },
      uInstanceCount: { value: 0 },
      uBvhTex: { value: null as THREE.DataTexture | null },
      uBvhNodeCount: { value: 0 },
      uSceneBMin: { value: new THREE.Vector3(-1e3, -1e3, -1e3) },
      uSceneBMax: { value: new THREE.Vector3(1e3, 1e3, 1e3) },
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

  function beginInteraction() {
    if (!renderer) return;
    renderer.setPixelRatio(INTERACT_DPR);
    clearTimeout(interactTimer);
    interactTimer = setTimeout(() => {
      renderer.setPixelRatio(IDLE_DPR);
      requestRender();
    }, 150);
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
      u.uTokenTex.value = null; u.uInstanceTex.value = null; u.uBvhTex.value = null;
      u.uProtoCount.value = 0; u.uInstanceCount.value = 0; u.uSurfCount.value = 0; u.uBvhNodeCount.value = 0;
      instanceCount = 0; prototypeCount = 0; materialLegend = []; usingInstancing = false;
      truncatedSurfaces = truncatedProtos = truncatedInstances = truncatedTokens = truncatedBvh = false;
      render(); return;
    }
    const surfacesUsed = csg.surfaces.slice(0, MAX_SURFACES);
    truncatedSurfaces = csg.surfaces.length > MAX_SURFACES;
    const surfIndex = new Map(surfacesUsed.map((s, i) => [s.id, i]));
    const surfaceById = new Map(surfacesUsed.map((s) => [s.id, s]));
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
    }

    const surfType = u.uSurfType.value as number[];
    const surfParams = u.uSurfParams.value as THREE.Vector4[];
    surfacesUsed.forEach((s, i) => { const packed = packSurface(s); surfType[i] = packed.type; surfParams[i].copy(packed.v); });
    for (let i = surfacesUsed.length; i < MAX_SURFACES; i++) { surfType[i] = -1; surfParams[i].set(0, 0, 0, 0); }
    u.uSurfCount.value = surfacesUsed.length;

    interface ProtoEntry { region: RegionNode; material_id: string; aabb: AxisBounds; isFill?: boolean; }
    const protos: ProtoEntry[] = [];
    const instances: { dx: number; dy: number; dz: number; protoIdx: number }[] = [];
    const lis = csg.lattice_instances ?? [];
    usingInstancing = lis.length > 0;

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
        }
        const numLayers = protos.length - baseProtoIdx;
        // Phase D: inner_offsets non-empty → nested lattice
        // world = assembly_offset + pin_offset; prototype stays one pin.
        const innerOffs = (li as LatticeInstance & { inner_offsets?: [number, number, number][] }).inner_offsets ?? [];
        const outerOffs = li.instances;
        if (innerOffs.length > 0) {
          for (const a of outerOffs) {
            for (const pin of innerOffs) {
              const dx = a[0] + pin[0], dy = a[1] + pin[1], dz = a[2] + pin[2];
              for (let k = 0; k < numLayers; k++) {
                if (instances.length >= MAX_INSTANCES) break;
                instances.push({ dx, dy, dz, protoIdx: baseProtoIdx + k });
              }
            }
          }
        } else {
          for (const off of outerOffs) {
            for (let k = 0; k < numLayers; k++) {
              if (instances.length >= MAX_INSTANCES) break;
              instances.push({ dx: off[0], dy: off[1], dz: off[2], protoIdx: baseProtoIdx + k });
            }
          }
        }
      }
      const latticeNames = latticeCellNameSet(lis);
      for (const cell of csg.cells) {
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
        const simplified = planeOnlyRegion(region, surfaceById);
        if (simplified) region = simplified;
        const protoIdx = protos.length;
        protos.push({
          region,
          material_id: cell.material_id,
          aabb: cellAABB(region, surfaceById, sceneBounds),
          isFill: true,
        });
        if (instances.length < MAX_INSTANCES) instances.push({ dx: 0, dy: 0, dz: 0, protoIdx });
      }
    } else {
      for (const cell of csg.cells.filter((c) => c.material_id != null)) {
        const key = visibilityKey(cell.name);
        if (key && !isVisible(key)) continue;
        if (protos.length >= MAX_PROTOTYPES) break;
        const protoIdx = protos.length;
        protos.push({ region: cell.region, material_id: cell.material_id!, aabb: cellAABB(cell.region, surfaceById, sceneBounds), isFill: false });
        if (instances.length < MAX_INSTANCES) instances.push({ dx: 0, dy: 0, dz: 0, protoIdx });
      }
    }

    truncatedProtos = protos.length >= MAX_PROTOTYPES;
    truncatedInstances = instances.length >= MAX_INSTANCES;

    const protoColor = u.uProtoColor.value as THREE.Color[];
    const protoTokenCount = u.uProtoTokenCount.value as number[];
    const protoIsFill = u.uProtoIsFill.value as number[];
    const texData = new Float32Array(MAX_TOKENS_PER_PROTO * MAX_PROTOTYPES * 4);
    let anyTokenTrunc = false;
    const legendSeen = new Map<string, string>();
    protos.forEach((proto, pi) => {
      const [r, g, b] = resolveMaterialColor(proto.material_id);
      protoColor[pi].setRGB(r, g, b);
      if (!legendSeen.has(proto.material_id)) legendSeen.set(proto.material_id, `#${protoColor[pi].getHexString()}`);
      protoIsFill[pi] = proto.isFill ? 1 : 0;
      const tokens: number[] = [];
      flattenRegion(proto.region, surfIndex, tokens);
      const pairCount = tokens.length / 2;
      const used = Math.min(pairCount, MAX_TOKENS_PER_PROTO);
      if (pairCount > MAX_TOKENS_PER_PROTO) anyTokenTrunc = true;
      protoTokenCount[pi] = used;
      for (let p = 0; p < used; p++) {
        const texelIdx = (pi * MAX_TOKENS_PER_PROTO + p) * 4;
        texData[texelIdx] = tokens[p * 2]; texData[texelIdx + 1] = tokens[p * 2 + 1];
      }
    });
    for (let pi = protos.length; pi < MAX_PROTOTYPES; pi++) { protoColor[pi].setRGB(0, 0, 0); protoTokenCount[pi] = 0; protoIsFill[pi] = 0; }
    truncatedTokens = anyTokenTrunc;
    prototypeCount = protos.length; instanceCount = instances.length;
    materialLegend = [...legendSeen.entries()].map(([id, color]) => ({ id, color }));
    u.uProtoCount.value = protos.length;

    tokenTexture?.dispose();
    tokenTexture = new THREE.DataTexture(texData, MAX_TOKENS_PER_PROTO, MAX_PROTOTYPES, THREE.RGBAFormat, THREE.FloatType);
    tokenTexture.magFilter = THREE.NearestFilter; tokenTexture.minFilter = THREE.NearestFilter;
    tokenTexture.generateMipmaps = false; tokenTexture.needsUpdate = true;
    u.uTokenTex.value = tokenTexture;

    const instData = new Float32Array(MAX_INSTANCES * 4);
    const instanceBoxes: AxisBounds[] = [];
    instances.forEach((inst, i) => {
      instData[i * 4] = inst.dx; instData[i * 4 + 1] = inst.dy; instData[i * 4 + 2] = inst.dz; instData[i * 4 + 3] = inst.protoIdx;
      instanceBoxes.push(translateAABB(protos[inst.protoIdx].aabb, inst.dx, inst.dy, inst.dz));
    });
    instanceTexture?.dispose();
    instanceTexture = new THREE.DataTexture(instData, MAX_INSTANCES, 1, THREE.RGBAFormat, THREE.FloatType);
    instanceTexture.magFilter = THREE.NearestFilter; instanceTexture.minFilter = THREE.NearestFilter;
    instanceTexture.generateMipmaps = false; instanceTexture.needsUpdate = true;
    u.uInstanceTex.value = instanceTexture; u.uInstanceCount.value = instances.length;

    const tBvh0 = performance.now();
    const bvhTexData = new Float32Array(3 * MAX_BVH_NODES * 4);
    let bvhNodeCount = 0;
    truncatedBvh = false;
    if (instances.length > 0) {
      const flat = flattenBvh(buildBvh(instances.map((_, i) => i), instanceBoxes), bvhTexData);
      bvhNodeCount = flat.count;
      truncatedBvh = flat.truncated;
    }
    u.uBvhNodeCount.value = bvhNodeCount;
    console.log(`[perf] BVH build: ${(performance.now() - tBvh0).toFixed(2)}ms, nodes=${bvhNodeCount}, protos=${protos.length}, instances=${instances.length}, instancing=${usingInstancing}`);

    bvhTexture?.dispose();
    bvhTexture = new THREE.DataTexture(bvhTexData, 3, MAX_BVH_NODES, THREE.RGBAFormat, THREE.FloatType);
    bvhTexture.magFilter = THREE.NearestFilter; bvhTexture.minFilter = THREE.NearestFilter;
    bvhTexture.generateMipmaps = false; bvhTexture.needsUpdate = true;
    u.uBvhTex.value = bvhTexture;

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

  let frameCount = 0, frameTimeSum = 0, frameTimeMax = 0, pointerMoveCount = 0;
  function render() {
    if (!renderer) return;
    updateCameraUniforms();
    const t0 = performance.now();
    renderer.render(scene, camera);
    const dt = performance.now() - t0;
    frameCount++; frameTimeSum += dt; frameTimeMax = Math.max(frameTimeMax, dt);
    if (frameCount % 30 === 0) {
      console.log(`[perf] avg=${(frameTimeSum/30).toFixed(2)}ms max=${frameTimeMax.toFixed(2)}ms protos=${prototypeCount} instances=${instanceCount} instancing=${usingInstancing}`);
      frameTimeSum = 0; frameTimeMax = 0;
    }
  }

  let isDragging = false, lastX = 0, lastY = 0, renderQueued = false, pendingRenderFrame: number | null = null;
  function requestRender() {
    if (renderQueued) return;
    renderQueued = true;
    pendingRenderFrame = requestAnimationFrame(() => { renderQueued = false; pendingRenderFrame = null; render(); });
  }
  function onPointerDown(e: PointerEvent) { isDragging = true; lastX = e.clientX; lastY = e.clientY; canvasEl.setPointerCapture(e.pointerId); beginInteraction(); }
  function onPointerMove(e: PointerEvent) {
    if (!isDragging) return;
    pointerMoveCount++;
    const dx = e.clientX - lastX, dy = e.clientY - lastY;
    lastX = e.clientX; lastY = e.clientY;
    cameraTheta -= dx * 0.005;
    cameraPhi = Math.max(0.05, Math.min(Math.PI - 0.05, cameraPhi - dy * 0.005));
    beginInteraction(); requestRender();
  }
  function onPointerUp(e: PointerEvent) { isDragging = false; canvasEl.releasePointerCapture(e.pointerId); }
  function onWheel(e: WheelEvent) {
    e.preventDefault();
    cameraDistance = Math.max(0.5, Math.min(500, cameraDistance * (1 + e.deltaY * 0.001)));
    beginInteraction(); requestRender();
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
    shaderMaterial = new THREE.ShaderMaterial({ uniforms: makeUniforms(), vertexShader: VERTEX_SHADER, fragmentShader: FRAGMENT_SHADER });
    scene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), shaderMaterial));
    renderer = new THREE.WebGLRenderer({ canvas: canvasEl, antialias: false });
    renderer.setPixelRatio(IDLE_DPR);
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
    tokenTexture?.dispose(); bvhTexture?.dispose(); instanceTexture?.dispose(); renderer?.dispose();
  });
</script>

<div class="csg-viewport" bind:this={containerEl}>
  <canvas bind:this={canvasEl} onpointerdown={onPointerDown} onpointermove={onPointerMove} onpointerup={onPointerUp} onwheel={onWheel}></canvas>
  <div class="overlay">
    <button class="viewport-btn" onclick={resetCamera} title="Reset camera">Reset view</button>
    {#if !loading && !loadError}
      <span class="count-badge">
        {#if usingInstancing}{prototypeCount} proto · {instanceCount} inst{:else}{instanceCount} cell{instanceCount === 1 ? '' : 's'}{/if}
      </span>
    {/if}
  </div>
  {#if materialLegend.length > 0}
    <div class="legend">{#each materialLegend as m}<span class="legend-item"><i style="background:{m.color}"></i>{m.id}</span>{/each}</div>
  {/if}
  {#if loading}<div class="badge">Loading CSG…</div>{/if}
  {#if loadError}<div class="badge error">{loadError}</div>{/if}
  {#if truncatedSurfaces || truncatedProtos || truncatedInstances || truncatedTokens || truncatedBvh}
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
  .count-badge { font-size: 10px; font-family: var(--font-mono); color: var(--color-subtext); }
  .legend { position: absolute; top: 12px; left: 12px; display: flex; flex-direction: column; gap: 3px; background: var(--color-bg-panel); border: 1px solid var(--color-border); border-radius: 6px; padding: 6px 8px; max-height: 40%; overflow-y: auto; }
  .legend-item { display: flex; align-items: center; gap: 5px; font-size: 10px; font-family: var(--font-mono); color: var(--color-subtext); }
  .legend-item i { width: 9px; height: 9px; border-radius: 2px; display: inline-block; flex-shrink: 0; }
  .badge { position: absolute; top: 12px; right: 12px; max-width: 320px; font-size: 11px; padding: 6px 10px; border-radius: 6px; background: rgba(6, 182, 212, 0.15); color: var(--color-accent-hi); border: 1px solid var(--color-accent); }
  .badge.error { background: rgba(239, 68, 68, 0.15); color: #f87171; border-color: #ef4444; }
  .badge.warning { background: rgba(245, 158, 11, 0.15); color: #f59e0b; border-color: #f59e0b; top: auto; bottom: 12px; }
</style>
