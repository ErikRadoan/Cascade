<script lang="ts">
  // CsgViewportPanel — Phase D of geometry-restructuring-plan.md: this is
  // now the editor's ONLY 3D viewport (see panelRegistry.ts / dockStore's
  // defaultLayout — the old FuelPin/Box-only Viewport3D + ViewportPanel
  // are retired). Renders the FULLY EXPANDED CSG (surfaces + region
  // trees), not a shape-specific scene description, via a raymarched
  // signed-distance-field shader — see the original file header (kept
  // below) for why raymarching, and why this already renders arbitrary
  // Union/Subtraction/Intersection region trees with zero new shader code.
  //
  // Phase D changes:
  //   - CSG data now comes from the shared stores/csg.svelte.ts store
  //     (one request backs both this panel and ObjectPanel) instead of
  //     each fetching /geometry/csg independently.
  //   - New `cellColors` prop: an optional cell_name -> hex color map,
  //     the same shape ResultsViewport3D/GeometryPlotPanel already use for
  //     tally overlays. When set, it overrides this panel's default
  //     per-material hash color for that cell — this is what would let a
  //     results-style overlay reuse this viewport instead of
  //     ResultsViewport3D, should that consolidation happen later (not
  //     part of this phase — see plan §9 open questions).
  //   - Cells belonging to a hidden ObjectPanel group (the eye toggle) are
  //     now excluded before the MAX_CELLS cap is applied, via the same
  //     csgCellGrouping.baseGroupName() ObjectPanel groups by — so hiding
  //     a lattice in the object list actually hides it here now, matching
  //     the old Viewport3D/ObjectPanel behavior this panel replaces.
  //
  // Original header, unchanged:
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
  // Known scaling limit (documented, not silently swallowed): every cell
  // is brute-force tested at every raymarch step, capped at MAX_CELLS/
  // MAX_SURFACES below. A single pin cell or a small lattice is fine; a
  // large lattice (dozens+ of pins) will get truncated with a banner
  // rather than silently rendering wrong or hanging the GPU. Fixing that
  // for real needs a spatial accelerator, the same problem
  // GeometryPlotPanel.svelte solves for its 2D slice with a bucket-grid
  // index (see that file's block comment) — a 3D analogue (or a BVH baked
  // into a texture) is the natural next step once lattice-scale CSG
  // viewing matters, not part of this pass.

  import { onMount, onDestroy } from 'svelte';
  import * as THREE from 'three';
  import { activeProject } from '../stores/projects.svelte.js';
  import { csgState, requestCsgRefresh } from '../stores/csg.svelte.js';
  import { isVisible, visibility } from '../stores/visibility.svelte.js';
  import { baseGroupName } from '../csgCellGrouping';
  import type { CsgGeometry, CsgSurface, RegionNode } from '$lib/types';

  let { cellColors = null }: { cellColors?: Record<string, string> | null } = $props();

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

  // Parses a "#rrggbb" cellColors override into the same [0,1] RGB triple
  // shape hashColor() returns, so both feed uCellColor identically.
  function hexToRgb01(hex: string): [number, number, number] {
    const v = parseInt(hex.replace('#', ''), 16) || 0;
    return [((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255];
  }

  function computeBounds(csg: CsgGeometry) {
    let xMin = Infinity, xMax = -Infinity, yMin = Infinity, yMax = -Infinity, zMin = Infinity, zMax = -Infinity;
    for (const s of csg.surfaces) {
      const p = s.params;
      if (s.type === 'plane_x') { const v = num(p, 'x0', 'x'); xMin = Math.min(xMin, v); xMax = Math.max(xMax, v); }
      else if (s.type === 'plane_y') { const v = num(p, 'y0', 'y'); yMin = Math.min(yMin, v); yMax = Math.max(yMax, v); }
      else if (s.type === 'plane_z') { const v = num(p, 'z0', 'z'); zMin = Math.min(zMin, v); zMax = Math.max(zMax, v); }
      else if (s.type === 'cylinder_z') {
        const x0 = num(p, 'x0', 'x'), y0 = num(p, 'y0', 'y'), r = num(p, 'r', 'r', 1);
        xMin = Math.min(xMin, x0 - r); xMax = Math.max(xMax, x0 + r);
        yMin = Math.min(yMin, y0 - r); yMax = Math.max(yMax, y0 + r);
      } else if (s.type === 'sphere') {
        const x0 = num(p, 'x0', 'x'), y0 = num(p, 'y0', 'y'), z0 = num(p, 'z0', 'z'), r = num(p, 'r', 'r', 1);
        xMin = Math.min(xMin, x0 - r); xMax = Math.max(xMax, x0 + r);
        yMin = Math.min(yMin, y0 - r); yMax = Math.max(yMax, y0 + r);
        zMin = Math.min(zMin, z0 - r); zMax = Math.max(zMax, z0 + r);
      }
    }
    if (!isFinite(xMin)) return { xMin: -5, xMax: 5, yMin: -5, yMax: 5, zMin: -5, zMax: 5 };
    if (!isFinite(zMin)) { zMin = -5; zMax = 5; }
    return { xMin, xMax, yMin, yMax, zMin, zMax };
  }

  // ---- Component state ------------------------------------------------------
  let truncatedSurfaces = $state(false);
  let truncatedCells = $state(false);
  let truncatedTokens = $state(false);
  let cellCount = $state(0);
  let materialLegend = $state<{ id: string; color: string }[]>([]);

  $effect(() => {
    requestCsgRefresh(activeProject().text);
  });

  // ---- Three.js scaffolding: one full-screen quad, no meshes -----------------
  let canvasEl: HTMLCanvasElement;
  let containerEl: HTMLDivElement;
  let renderer: THREE.WebGLRenderer;
  let scene: THREE.Scene;
  let camera: THREE.OrthographicCamera;
  let shaderMaterial: THREE.ShaderMaterial;
  let tokenTexture: THREE.DataTexture | null = null;

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

    // Region -> signed distance. Inside/Outside read a surface's own exact
    // SDF (negated for Outside); Intersection = max (AND), Union = min
    // (OR), Complement = negate (NOT) — the standard SDF-CSG combinators.
    // This is what makes union/subtraction "just work" once the DSL grows
    // component types that produce those Region shapes.
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

    float sceneSDF(vec3 p, out int hitCell) {
      float best = 1.0e6;
      hitCell = -1;
      for (int i = 0; i < MAX_CELLS; i++) {
        if (i >= uCellCount) break;
        float d = evalRegion(i, p);
        if (d < best) { best = d; hitCell = i; }
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
    const csg = csgState.data;

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

    const surfType = u.uSurfType.value as number[];
    const surfParams = u.uSurfParams.value as THREE.Vector4[];
    surfacesUsed.forEach((s, i) => {
      const packed = packSurface(s);
      surfType[i] = packed.type;
      surfParams[i].copy(packed.v);
    });
    for (let i = surfacesUsed.length; i < MAX_SURFACES; i++) { surfType[i] = -1; surfParams[i].set(0, 0, 0, 0); }

    // Skip void cells AND cells whose owning ObjectPanel group is
    // currently hidden (the eye toggle) — grouped via the same
    // baseGroupName() ObjectPanel uses, so "hide the lattice" here means
    // exactly what it means there.
    const nonVoid = csg.cells.filter(
      (c) => c.material_id != null && isVisible(baseGroupName(c.name ?? c.id)),
    );
    const cellsUsed = nonVoid.slice(0, MAX_CELLS);
    truncatedCells = nonVoid.length > MAX_CELLS;

    const cellColor = u.uCellColor.value as THREE.Color[];
    const cellTokenCount = u.uCellTokenCount.value as number[];
    const texData = new Float32Array(MAX_TOKENS_PER_CELL * MAX_CELLS * 4);
    let anyCellTruncated = false;
    const legendSeen = new Map<string, string>();

    cellsUsed.forEach((cell, ci) => {
      const matId = cell.material_id!;
      const override = cellColors?.[cell.name ?? ''];
      const [r, g, b] = override ? hexToRgb01(override) : hashColor(matId);
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
    });
    for (let ci = cellsUsed.length; ci < MAX_CELLS; ci++) { cellColor[ci].setRGB(0, 0, 0); cellTokenCount[ci] = 0; }

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

    if (!hasFramedOnce) { resetCamera(); hasFramedOnce = true; }
    else { render(); }
  }

  function resetCamera() {
    cameraTheta = Math.PI / 4;
    cameraPhi = Math.PI / 3;
    const csg = csgState.data;
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
    render();
  }
  function onPointerUp(e: PointerEvent) {
    isDragging = false;
    canvasEl.releasePointerCapture(e.pointerId);
  }
  function onWheel(e: WheelEvent) {
    e.preventDefault();
    cameraDistance = Math.max(0.5, Math.min(500, cameraDistance * (1 + e.deltaY * 0.001)));
    render();
  }

  function resize() {
    if (!containerEl || !renderer || !shaderMaterial) return;
    const w = containerEl.clientWidth, h = containerEl.clientHeight;
    renderer.setSize(w, h, false);
    shaderMaterial.uniforms.uAspect.value = w / Math.max(1, h);
    render();
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
    tokenTexture?.dispose();
    renderer?.dispose();
  });

  // Re-run whenever the shared CSG data, color overrides, or visibility
  // toggles change. Reading `visibility` here (the raw reactive store
  // object, not just isVisible()) registers it as a dependency — same
  // pattern Viewport3D used pre-Phase-D.
  $effect(() => {
    csgState.data;
    cellColors;
    visibility;
    if (shaderMaterial) rebuildAndRender();
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
    {#if !csgState.loading && !csgState.error}
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

  {#if csgState.loading}
    <div class="badge">Loading CSG…</div>
  {/if}
  {#if csgState.error}
    <div class="badge error">{csgState.error}</div>
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
