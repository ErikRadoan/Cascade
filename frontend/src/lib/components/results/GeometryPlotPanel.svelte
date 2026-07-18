<script lang="ts">
  // GeometryPlotPanel — material-colored geometry slices, computed
  // entirely client-side by point-classifying the CSG tree (same math
  // OpenMC's plotter does), never invoking OpenMC. Fetches the geometry
  // once per job; every axis/slice/resolution change afterwards is a
  // local recompute — no backend round trip, so scrubbing is instant
  // and jitter-free.
  //
  // Surface support: plane_x/y/z, cylinder_x/y/z, sphere — covers every
  // surface type the expander currently produces (FuelPin/Box/lattices).
  // cone_z/torus aren't emitted by anything today; if a future component
  // adds them, surfaceF() below needs a matching branch or they'll
  // silently render as "outside everywhere" rather than crash.

  import * as api from '$lib/api';
  import type { CsgSurface, CsgCell, RegionNode, CsgGeometry, CsgSurfaceType } from '$lib/types';

  let { jobId }: { jobId: string } = $props();

  // ---- Types mirroring the new /csg response ----------------------------
  type SurfaceType =
    | 'plane_x' | 'plane_y' | 'plane_z'
    | 'cylinder_x' | 'cylinder_y' | 'cylinder_z'
    | 'sphere' | 'cone_z' | 'torus';

  // ---- Fetch --------------------------------------------------------------
  let csg = $state<CsgGeometry | null>(null);
  let materialColors = $state<Record<string, string>>({});
  let bounds = $state<{ x_min: number; x_max: number; y_min: number; y_max: number; z_min: number; z_max: number } | null>(null);
  let loading = $state(false);
  let loadError = $state<string | null>(null);

  async function load(id: string) {
    loading = true;
    loadError = null;
    csg = null;
    try {
      const [geo, scene] = await Promise.all([api.jobs.csg(id), api.jobs.scene(id)]);
      csg = geo;
      materialColors = scene.material_colors;
      bounds = scene.bounds;
    } catch (e: unknown) {
      loadError = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  $effect(() => { if (jobId) load(jobId); });

  // ---- CSG point classification -------------------------------------------
  const VOID_COLOR = '#101020';
  const UNKNOWN_MATERIAL_COLOR = '#A0A0A0';

  function resolveParam(s: CsgSurface, canonical: string, alias: string): number {
    if (s.params[canonical] != null) return s.params[canonical];
    if (s.params[alias] != null) return s.params[alias];
    return 0;
  }

  // Signed implicit function: negative = "inside" (matches OpenMC's
  // -surface_id convention), non-negative = "outside".
  function surfaceF(s: CsgSurface, x: number, y: number, z: number): number {
    switch (s.type) {
      case 'plane_x': return x - resolveParam(s, 'x0', 'x');
      case 'plane_y': return y - resolveParam(s, 'y0', 'y');
      case 'plane_z': return z - resolveParam(s, 'z0', 'z');
      case 'cylinder_z': {
        const x0 = resolveParam(s, 'x0', 'x'), y0 = resolveParam(s, 'y0', 'y'), r = s.params.r ?? 1;
        const dx = x - x0, dy = y - y0;
        return dx * dx + dy * dy - r * r;
      }
      case 'cylinder_x': {
        const y0 = resolveParam(s, 'y0', 'y'), z0 = resolveParam(s, 'z0', 'z'), r = s.params.r ?? 1;
        const dy = y - y0, dz = z - z0;
        return dy * dy + dz * dz - r * r;
      }
      case 'cylinder_y': {
        const x0 = resolveParam(s, 'x0', 'x'), z0 = resolveParam(s, 'z0', 'z'), r = s.params.r ?? 1;
        const dx = x - x0, dz = z - z0;
        return dx * dx + dz * dz - r * r;
      }
      case 'sphere': {
        const x0 = resolveParam(s, 'x0', 'x'), y0 = resolveParam(s, 'y0', 'y'), z0 = resolveParam(s, 'z0', 'z');
        const r = s.params.r ?? 1;
        const dx = x - x0, dy = y - y0, dz = z - z0;
        return dx * dx + dy * dy + dz * dz - r * r;
      }
      default:
        // cone_z / torus: not emitted by the expander today. Render as
        // "always outside" rather than throw, so an unsupported surface
        // degrades to "this region never matches" instead of breaking
        // the whole slice.
        return 1;
    }
  }

  function evalRegion(node: RegionNode, surfaces: Map<string, CsgSurface>, x: number, y: number, z: number): boolean {
    switch (node.op) {
      case 'inside': {
        const s = surfaces.get(node.surface);
        return s ? surfaceF(s, x, y, z) < 0 : false;
      }
      case 'outside': {
        const s = surfaces.get(node.surface);
        return s ? surfaceF(s, x, y, z) >= 0 : false;
      }
      case 'and':
        return node.items.every((it) => evalRegion(it, surfaces, x, y, z));
      case 'or':
        return node.items.some((it) => evalRegion(it, surfaces, x, y, z));
      case 'not':
        return !evalRegion(node.item, surfaces, x, y, z);
    }
  }

  function classify(cells: CsgCell[], candidates: number[], surfaces: Map<string, CsgSurface>, x: number, y: number, z: number): CsgCell | null {
    for (const i of candidates) {
      const cell = cells[i];
      if (evalRegion(cell.region, surfaces, x, y, z)) return cell;
    }
    return null;
  }

  function colorForMaterial(materialId: string | null): string {
    if (materialId === null) return VOID_COLOR;
    return materialColors[materialId] ?? UNKNOWN_MATERIAL_COLOR;
  }

  // ---- Spatial index --------------------------------------------------------
  // classify() previously walked the FULL cell list for EVERY pixel, and for
  // each cell evaluated its whole boolean region tree — for a flattened
  // lattice/core with thousands of cells that's pixels × cells × tree-depth
  // boolean tests, which is where the multi-minute render time comes from.
  //
  // Fix: derive an axis-aligned bounding box for each cell's region (from
  // its surfaces — a plane bounds one side, a cylinder/sphere bounds all
  // sides it touches), then bucket cell indices into a uniform 3D grid over
  // the geometry bounds. Per-pixel classification only tests the handful of
  // cells whose bbox overlaps that pixel's bucket. Bucket arrays are built
  // by iterating cells in original order, so "first cell containing the
  // point wins" semantics (same as evalRegion's linear scan) are preserved.
  //
  // The bbox derivation is conservative, never wrong: anything it can't
  // bound (e.g. `not`, or "outside a cylinder") is left unbounded and that
  // cell simply gets tested in every bucket, exactly like before — the
  // index can only reduce work, never produce a different classification.
  interface AABB { xlo: number; xhi: number; ylo: number; yhi: number; zlo: number; zhi: number }
  const UNBOUNDED_BOX: AABB = { xlo: -Infinity, xhi: Infinity, ylo: -Infinity, yhi: Infinity, zlo: -Infinity, zhi: Infinity };

  function intersectBox(a: AABB, b: AABB): AABB {
    return {
      xlo: Math.max(a.xlo, b.xlo), xhi: Math.min(a.xhi, b.xhi),
      ylo: Math.max(a.ylo, b.ylo), yhi: Math.min(a.yhi, b.yhi),
      zlo: Math.max(a.zlo, b.zlo), zhi: Math.min(a.zhi, b.zhi),
    };
  }
  function unionBox(a: AABB, b: AABB): AABB {
    return {
      xlo: Math.min(a.xlo, b.xlo), xhi: Math.max(a.xhi, b.xhi),
      ylo: Math.min(a.ylo, b.ylo), yhi: Math.max(a.yhi, b.yhi),
      zlo: Math.min(a.zlo, b.zlo), zhi: Math.max(a.zhi, b.zhi),
    };
  }

  // Bound for surfaceF(s, ...) < 0, i.e. an 'inside' node on this surface.
  function insideBBox(s: CsgSurface): AABB {
    switch (s.type) {
      case 'plane_x': return { ...UNBOUNDED_BOX, xhi: resolveParam(s, 'x0', 'x') };
      case 'plane_y': return { ...UNBOUNDED_BOX, yhi: resolveParam(s, 'y0', 'y') };
      case 'plane_z': return { ...UNBOUNDED_BOX, zhi: resolveParam(s, 'z0', 'z') };
      case 'cylinder_z': {
        const x0 = resolveParam(s, 'x0', 'x'), y0 = resolveParam(s, 'y0', 'y'), r = s.params.r ?? 1;
        return { xlo: x0 - r, xhi: x0 + r, ylo: y0 - r, yhi: y0 + r, zlo: -Infinity, zhi: Infinity };
      }
      case 'cylinder_x': {
        const y0 = resolveParam(s, 'y0', 'y'), z0 = resolveParam(s, 'z0', 'z'), r = s.params.r ?? 1;
        return { xlo: -Infinity, xhi: Infinity, ylo: y0 - r, yhi: y0 + r, zlo: z0 - r, zhi: z0 + r };
      }
      case 'cylinder_y': {
        const x0 = resolveParam(s, 'x0', 'x'), z0 = resolveParam(s, 'z0', 'z'), r = s.params.r ?? 1;
        return { xlo: x0 - r, xhi: x0 + r, ylo: -Infinity, yhi: Infinity, zlo: z0 - r, zhi: z0 + r };
      }
      case 'sphere': {
        const x0 = resolveParam(s, 'x0', 'x'), y0 = resolveParam(s, 'y0', 'y'), z0 = resolveParam(s, 'z0', 'z'), r = s.params.r ?? 1;
        return { xlo: x0 - r, xhi: x0 + r, ylo: y0 - r, yhi: y0 + r, zlo: z0 - r, zhi: z0 + r };
      }
      default:
        return UNBOUNDED_BOX;
    }
  }

  // Bound for surfaceF(s, ...) >= 0, i.e. an 'outside' node. Only planes
  // give a useful finite bound here — "outside a cylinder/sphere" extends
  // to infinity, so that stays unbounded.
  function outsideBBox(s: CsgSurface): AABB {
    switch (s.type) {
      case 'plane_x': return { ...UNBOUNDED_BOX, xlo: resolveParam(s, 'x0', 'x') };
      case 'plane_y': return { ...UNBOUNDED_BOX, ylo: resolveParam(s, 'y0', 'y') };
      case 'plane_z': return { ...UNBOUNDED_BOX, zlo: resolveParam(s, 'z0', 'z') };
      default:
        return UNBOUNDED_BOX;
    }
  }

  function regionBBox(node: RegionNode, surfaces: Map<string, CsgSurface>): AABB {
    switch (node.op) {
      case 'inside': {
        const s = surfaces.get(node.surface);
        return s ? insideBBox(s) : UNBOUNDED_BOX;
      }
      case 'outside': {
        const s = surfaces.get(node.surface);
        return s ? outsideBBox(s) : UNBOUNDED_BOX;
      }
      case 'and':
        return node.items.reduce((acc, it) => intersectBox(acc, regionBBox(it, surfaces)), UNBOUNDED_BOX);
      case 'or': {
        if (node.items.length === 0) return UNBOUNDED_BOX;
        let box = regionBBox(node.items[0], surfaces);
        for (let i = 1; i < node.items.length; i++) box = unionBox(box, regionBBox(node.items[i], surfaces));
        return box;
      }
      case 'not':
        // The complement of an arbitrary region isn't boundable in
        // general — stay conservative (correct, just uncullable) rather
        // than risk excluding a cell that should have matched.
        return UNBOUNDED_BOX;
    }
  }

  interface SpatialIndex {
    gx: number; gy: number; gz: number;
    xlo: number; xhi: number; ylo: number; yhi: number; zlo: number; zhi: number;
    buckets: number[][]; // flattened [iz*gy+iy]*gx+ix -> ordered cell indices
    surfaceMap: Map<string, CsgSurface>;
  }

  function buildSpatialIndex(geo: CsgGeometry, b: NonNullable<typeof bounds>): SpatialIndex {
    const surfaceMap = new Map(geo.surfaces.map((s) => [s.id, s]));
    const n = geo.cells.length;
    // Aim for a modest cell count per bucket on average; cap grid
    // resolution so memory stays bounded even for huge cell counts.
    const dim = Math.max(1, Math.min(24, Math.round(Math.cbrt(n))));
    const gx = dim, gy = dim, gz = dim;
    const xlo = b.x_min, xhi = b.x_max, ylo = b.y_min, yhi = b.y_max, zlo = b.z_min, zhi = b.z_max;
    const xw = Math.max(1e-9, xhi - xlo), yw = Math.max(1e-9, yhi - ylo), zw = Math.max(1e-9, zhi - zlo);
    const buckets: number[][] = Array.from({ length: gx * gy * gz }, () => []);
    const clamp = (v: number, max: number) => Math.max(0, Math.min(max - 1, v));

    geo.cells.forEach((cell, idx) => {
      const box = regionBBox(cell.region, surfaceMap);
      const ixLo = Number.isFinite(box.xlo) ? clamp(Math.floor(((box.xlo - xlo) / xw) * gx), gx) : 0;
      const ixHi = Number.isFinite(box.xhi) ? clamp(Math.floor(((box.xhi - xlo) / xw) * gx), gx) : gx - 1;
      const iyLo = Number.isFinite(box.ylo) ? clamp(Math.floor(((box.ylo - ylo) / yw) * gy), gy) : 0;
      const iyHi = Number.isFinite(box.yhi) ? clamp(Math.floor(((box.yhi - ylo) / yw) * gy), gy) : gy - 1;
      const izLo = Number.isFinite(box.zlo) ? clamp(Math.floor(((box.zlo - zlo) / zw) * gz), gz) : 0;
      const izHi = Number.isFinite(box.zhi) ? clamp(Math.floor(((box.zhi - zlo) / zw) * gz), gz) : gz - 1;
      for (let iz = izLo; iz <= izHi; iz++) {
        for (let iy = iyLo; iy <= iyHi; iy++) {
          for (let ix = ixLo; ix <= ixHi; ix++) {
            buckets[(iz * gy + iy) * gx + ix].push(idx);
          }
        }
      }
    });

    return { gx, gy, gz, xlo, xhi, ylo, yhi, zlo, zhi, buckets, surfaceMap };
  }

  function bucketAt(idx: SpatialIndex, x: number, y: number, z: number): number[] {
    const ix = Math.max(0, Math.min(idx.gx - 1, Math.floor(((x - idx.xlo) / Math.max(1e-9, idx.xhi - idx.xlo)) * idx.gx)));
    const iy = Math.max(0, Math.min(idx.gy - 1, Math.floor(((y - idx.ylo) / Math.max(1e-9, idx.yhi - idx.ylo)) * idx.gy)));
    const iz = Math.max(0, Math.min(idx.gz - 1, Math.floor(((z - idx.zlo) / Math.max(1e-9, idx.zhi - idx.zlo)) * idx.gz)));
    return idx.buckets[(iz * idx.gy + iy) * idx.gx + ix];
  }

  // Built once per geometry load, not per pixel or per render() call —
  // the previous code rebuilt a surfaceMap on every single render() AND
  // on every mousemove over the canvas.
  const spatialIndex = $derived.by((): SpatialIndex | null => {
    if (!csg || !bounds) return null;
    return buildSpatialIndex(csg, bounds);
  });

  // ---- Controls -------------------------------------------------------------
  type Axis = 'x' | 'y' | 'z';
  const AXIS_LABELS: Record<Axis, string> = { x: 'YZ plane (fix X)', y: 'XZ plane (fix Y)', z: 'XY plane (fix Z)' };

  let axis = $state<Axis>('z');
  let sliceFrac = $state(0.5); // 0..1 through the bounding box on the chosen axis
  const RESOLUTIONS = [150, 250, 400] as const;
  let resolution = $state<(typeof RESOLUTIONS)[number]>(250);

  function axisBounds(a: Axis): [number, number] {
    if (!bounds) return [-1, 1];
    if (a === 'x') return [bounds.x_min, bounds.x_max];
    if (a === 'y') return [bounds.y_min, bounds.y_max];
    return [bounds.z_min, bounds.z_max];
  }

  const sliceCoord = $derived.by(() => {
    const [lo, hi] = axisBounds(axis);
    return lo + sliceFrac * (hi - lo);
  });

  // In-plane axes for the current slice, in (horizontal, vertical) order —
  // vertical flipped so +axis points up on screen, matching the 3D viewport.
  function planeAxes(a: Axis): [Axis, Axis] {
    if (a === 'z') return ['x', 'y'];
    if (a === 'y') return ['x', 'z'];
    return ['y', 'z'];
  }

  function pointFor(a: Axis, h: number, v: number): [number, number, number] {
    if (a === 'z') return [h, v, sliceCoord];
    if (a === 'y') return [h, sliceCoord, v];
    return [sliceCoord, h, v];
  }

  // ---- Render ---------------------------------------------------------------
  let canvasEl: HTMLCanvasElement;
  let rendering = $state(false);
  let hoverInfo = $state<{ material: string | null; cellName: string | null } | null>(null);
  let renderError = $state<string | null>(null);

  function render(targetResolution: number = resolution) {
    if (!canvasEl || !csg || !bounds || !spatialIndex) return;
    renderError = null;
    try {
      rendering = true;
      const idx = spatialIndex;
      const [hAxis, vAxis] = planeAxes(axis);
      const [hLoRaw, hHiRaw] = axisBounds(hAxis);
      const [vLoRaw, vHiRaw] = axisBounds(vAxis);

      // Degenerate bounds (zero-extent axis, or non-finite from an empty
      // geometry) would otherwise divide by zero into NaN pixel colors —
      // not a crash, but a blank/garbage canvas. Fall back to a small
      // symmetric window instead.
      const hLo = Number.isFinite(hLoRaw) ? hLoRaw : -1;
      const hHi = Number.isFinite(hHiRaw) && hHiRaw > hLo ? hHiRaw : hLo + 1;
      const vLo = Number.isFinite(vLoRaw) ? vLoRaw : -1;
      const vHi = Number.isFinite(vHiRaw) && vHiRaw > vLo ? vHiRaw : vLo + 1;

      const n = targetResolution;
      canvasEl.width = n;
      canvasEl.height = n;
      const ctx = canvasEl.getContext('2d');
      if (!ctx) return;

      const img = ctx.createImageData(n, n);
      const hexToRgb = (hex: string): [number, number, number] => {
        const v = parseInt(hex.replace('#', ''), 16) || 0;
        return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
      };
      const colorCache = new Map<string, [number, number, number]>();

      for (let row = 0; row < n; row++) {
        const v = vHi - ((row + 0.5) / n) * (vHi - vLo);
        for (let col = 0; col < n; col++) {
          const h = hLo + ((col + 0.5) / n) * (hHi - hLo);
          const [x, y, z] = pointFor(axis, h, v);
          const candidates = bucketAt(idx, x, y, z);
          const cell = classify(csg.cells, candidates, idx.surfaceMap, x, y, z);
          const hex = colorForMaterial(cell?.material_id ?? null);
          let rgb = colorCache.get(hex);
          if (!rgb) { rgb = hexToRgb(hex); colorCache.set(hex, rgb); }
          const pxIdx = (row * n + col) * 4;
          img.data[pxIdx] = rgb[0];
          img.data[pxIdx + 1] = rgb[1];
          img.data[pxIdx + 2] = rgb[2];
          img.data[pxIdx + 3] = 255;
        }
      }
      ctx.putImageData(img, 0, 0);
    } catch (e) {
      renderError = e instanceof Error ? e.message : String(e);
    } finally {
      rendering = false;
    }
  }

  function onCanvasMove(e: MouseEvent) {
    if (!csg || !bounds || !spatialIndex) return;
    const [hAxis, vAxis] = planeAxes(axis);
    const [hLo, hHi] = axisBounds(hAxis);
    const [vLo, vHi] = axisBounds(vAxis);
    const rect = canvasEl.getBoundingClientRect();
    const h = hLo + ((e.clientX - rect.left) / rect.width) * (hHi - hLo);
    const v = vHi - ((e.clientY - rect.top) / rect.height) * (vHi - vLo);
    const [x, y, z] = pointFor(axis, h, v);
    const candidates = bucketAt(spatialIndex, x, y, z);
    const cell = classify(csg.cells, candidates, spatialIndex.surfaceMap, x, y, z);
    hoverInfo = { material: cell?.material_id ?? null, cellName: cell?.name ?? null };
  }
  function onCanvasLeave() { hoverInfo = null; }

  // render() previously had nothing calling it at all, so the canvas
  // stayed blank no matter what. Now that it's wired up, this also does
  // the render in two passes: an instant low-res pass so the slice
  // slider feels immediate while scrubbing, then the user's chosen
  // resolution ~120ms later once things settle. Each render() call is
  // synchronous and runs to completion before the next one starts (no
  // worker/async involved), so a later call always simply overwrites an
  // earlier one — no stale-frame race to guard against.
  const PREVIEW_RESOLUTION = 64;
  let debounceHandle: ReturnType<typeof setTimeout> | undefined;

  $effect(() => {
    axis; sliceFrac; resolution; spatialIndex;
    if (!csg || !bounds || !spatialIndex) return;
    render(PREVIEW_RESOLUTION);
    clearTimeout(debounceHandle);
    debounceHandle = setTimeout(() => render(resolution), 120);
  });

</script>

<div class="geom-plot-panel">
  <div class="panel-body">
    {#if loading}
      <div class="empty-note">Loading geometry…</div>
    {:else if loadError}
      <div class="empty-note error">{loadError}</div>
    {:else if csg && bounds}
      <div class="controls-row">
        <div class="axis-picker" role="group" aria-label="Slice plane">
          {#each (['x', 'y', 'z'] as Axis[]) as a}
            <button class="axis-btn" class:active={axis === a} onclick={() => (axis = a)}>{AXIS_LABELS[a]}</button>
          {/each}
        </div>
        <div class="res-picker" role="group" aria-label="Resolution">
          {#each RESOLUTIONS as r}
            <button class="axis-btn" class:active={resolution === r} onclick={() => (resolution = r)}>{r}px</button>
          {/each}
        </div>
        {#if rendering}<span class="rendering-note">rendering…</span>{/if}
      </div>

      <div class="slice-row">
        <span class="slice-label">Slice position</span>
        <input type="range" min="0" max="1" step="0.002" bind:value={sliceFrac} class="slice-slider" />
        <span class="slice-index mono">{sliceCoord.toPrecision(4)} cm</span>
      </div>

      <div class="canvas-wrap">
        <canvas
          bind:this={canvasEl}
          onmousemove={onCanvasMove}
          onmouseleave={onCanvasLeave}
        ></canvas>
        {#if hoverInfo}
          <div class="hover-readout mono">
            {hoverInfo.material ?? 'void'}{#if hoverInfo.cellName} — {hoverInfo.cellName}{/if}
          </div>
        {/if}
      </div>
      {#if renderError}
          <div class="empty-note error">Render failed: {renderError}</div>
        {/if}
      <div class="legend-row">
        {#each Object.entries(materialColors) as [mat, color]}
          <span class="legend-chip"><i style="background:{color}"></i>{mat}</span>
        {/each}
        <span class="legend-chip"><i style="background:{VOID_COLOR}"></i>void</span>
      </div>
    {/if}
  </div>
</div>

<style>
  .geom-plot-panel { display: flex; flex-direction: column; height: 100%; }
  .panel-body { flex: 1; overflow: auto; padding: 12px; display: flex; flex-direction: column; gap: 12px; }
  .empty-note { font-size: 12px; color: var(--color-subtext); font-style: italic; padding: 20px; }
  .empty-note.error { color: #f87171; font-style: normal; }

  .controls-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .axis-picker, .res-picker {
    display: flex; border: 1px solid var(--color-border); border-radius: 2px; overflow: hidden;
  }
  .axis-btn {
    background: var(--color-bg-raised); color: var(--color-subtext); border: none;
    font-family: var(--font-mono); font-size: 11px; padding: 5px 10px; cursor: pointer;
    transition: background-color 0.1s, color 0.1s;
  }
  .axis-btn + .axis-btn { border-left: 1px solid var(--color-border); }
  .axis-btn:hover { color: var(--color-text); }
  .axis-btn.active { background: rgba(6, 182, 212, 0.12); color: var(--color-accent-hi); }
  .rendering-note { font-size: 10px; color: var(--color-subtext); font-style: italic; }

  .slice-row { display: flex; align-items: center; gap: 10px; }
  .slice-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--color-subtext); flex-shrink: 0; }
  .slice-slider { flex: 1; accent-color: var(--color-accent); }
  .slice-index { font-size: 11px; color: var(--color-text); flex-shrink: 0; white-space: nowrap; }

  .canvas-wrap {
    position: relative; display: flex; justify-content: center;
    background: var(--color-bg-deep, #0f172a); border: 1px solid var(--color-border);
    border-radius: 2px; padding: 8px;
  }
  canvas { width: min(100%, 480px); height: auto; aspect-ratio: 1; image-rendering: pixelated; display: block; }

  .hover-readout {
    position: absolute; top: 14px; right: 14px; font-size: 11px;
    background: var(--color-bg-panel); border: 1px solid var(--color-border);
    border-radius: 4px; padding: 4px 8px; color: var(--color-accent-hi); pointer-events: none;
  }

  .legend-row { display: flex; flex-wrap: wrap; gap: 10px; }
  .legend-chip { display: flex; align-items: center; gap: 5px; font-size: 10px; color: var(--color-subtext); font-family: var(--font-mono); }
  .legend-chip i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }

  .mono { font-family: var(--font-mono); }
</style>