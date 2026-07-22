<script lang="ts">
  // GeometryPlotPanel — material- or tally-colored geometry slices.
  //
  // Rasterization now happens server-side (GET /jobs/{id}/raster,
  // numpy-vectorized — see geometry_raster_service.py). This component's
  // only job is: ask for a slice, then color each returned cell index —
  // either by its material (via material_colors from the scene response)
  // or by a tally score (via /results/{id}/tallies, same cell_name join
  // key ResultsViewport3D uses). No CSG math lives in the frontend anymore.

  import * as api from '$lib/api';
  import PanelToolBar from '../shared/PanelToolBar.svelte';
  import ColorLegend from '../shared/ColorLegend.svelte';
  import {
    buildScale, staticScale, valueToColor, isFlagged,
    type ScaleType, type ScaleMode, type ColorScale,
  } from '../shared/ColorMap';
  import type { ImportTalliesResponse } from '../shared/ResultsTypes';
  import type { RasterResponse, SceneResponse } from '$lib/types';

  let { jobId }: { jobId: string } = $props();

  const VOID_COLOR = '#101020';
  const UNKNOWN_MATERIAL_COLOR = '#A0A0A0';
  const NO_DATA_COLOR = '#454b57';

  // ---- Fetch: scene (bounds + material colors) + tallies (optional) ------
  let bounds = $state<SceneResponse['bounds'] | null>(null);
  let materialColors = $state<Record<string, string>>({});
  let tallies = $state<ImportTalliesResponse | null>(null);
  let loading = $state(false);
  let loadError = $state<string | null>(null);

  async function loadContext(id: string) {
    loading = true;
    loadError = null;
    bounds = null;
    tallies = null;
    try {
      const scene = await api.jobs.scene(id);
      bounds = scene.bounds;
      materialColors = scene.material_colors;
    } catch (e: unknown) {
      loadError = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
    // Tally data is optional (job may not have scalar tallies enabled,
    // or may not be completed) — fail silently into "Material mode only".
    try {
      tallies = (await api.results.tallies(id)) as unknown as ImportTalliesResponse;
    } catch {
      tallies = null;
    }
  }

  $effect(() => { if (jobId) loadContext(jobId); });

  // ---- Color mode ----------------------------------------------------------
  type ColorMode = 'material' | 'tally';
  let colorMode = $state<ColorMode>('material');

  const scoreKeys = $derived.by(() => {
    if (!tallies) return [] as string[];
    const seen = new Set<string>();
    for (const t of tallies.tallies) for (const k of Object.keys(t.scores)) seen.add(k);
    return [...seen].sort();
  });

  let selectedScore = $state('');
  $effect(() => {
    if (!selectedScore && scoreKeys.length > 0) selectedScore = scoreKeys[0];
  });
  // If tallies never load, stay in material mode regardless of user intent.
  $effect(() => {
    if (scoreKeys.length === 0) colorMode = 'material';
  });

  let scaleType = $state<ScaleType>('log');
  let scaleMode = $state<ScaleMode>('dynamic');
  let staticMin = $state(0);
  let staticMax = $state(1);
  let staticSeeded = false;

  interface Row { name: string; mean: number; rel_err: number; }
  const rows = $derived.by((): Row[] => {
    if (!tallies || !selectedScore) return [];
    const out: Row[] = [];
    for (const t of tallies.tallies) {
      const s = t.scores[selectedScore];
      if (s) out.push({ name: t.name, mean: s.mean, rel_err: s.rel_err });
    }
    return out;
  });

  const dynamicScale = $derived(buildScale(rows.map((r) => r.mean), scaleType));
  $effect(() => {
    if (!staticSeeded && rows.length > 0) {
      staticMin = dynamicScale.min;
      staticMax = dynamicScale.max;
      staticSeeded = true;
    }
  });
  const scale = $derived<ColorScale>(
    scaleMode === 'static' ? staticScale(staticMin, staticMax, scaleType) : dynamicScale,
  );
  function useCurrentRange() {
    staticMin = dynamicScale.min;
    staticMax = dynamicScale.max;
  }

  const tallyByName = $derived.by(() => {
    const m = new Map<string, Row>();
    for (const r of rows) m.set(r.name, r);
    return m;
  });

  function colorForMaterial(materialId: string | null): string {
    if (materialId === null) return VOID_COLOR;
    if (materialColors[materialId]) return materialColors[materialId];
    for (const [k, v] of Object.entries(materialColors)) {
      if (k.toLowerCase() === materialId.toLowerCase()) return v;
    }
    return UNKNOWN_MATERIAL_COLOR;
  }

  // ---- Controls: axis / slice / resolution --------------------------------
  type Axis = 'x' | 'y' | 'z';
  const AXIS_LABELS: Record<Axis, string> = { x: 'YZ plane (fix X)', y: 'XZ plane (fix Y)', z: 'XY plane (fix Z)' };

  let axis = $state<Axis>('z');
  let sliceFrac = $state(0.5);
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
  function planeAxes(a: Axis): [Axis, Axis] {
    if (a === 'z') return ['x', 'y'];
    if (a === 'y') return ['x', 'z'];
    return ['y', 'z'];
  }

  // ---- Raster fetch + render ------------------------------------------------
  let canvasEl: HTMLCanvasElement;
  let raster = $state<RasterResponse | null>(null);
  let rendering = $state(false);
  let renderError = $state<string | null>(null);
  let hoverInfo = $state<{ material: string | null; cellName: string | null; value: number | null } | null>(null);

  const colorCache = new Map<string, [number, number, number]>();
  function hexToRgb(hex: string): [number, number, number] {
    let c = colorCache.get(hex);
    if (!c) {
      const v = parseInt(hex.replace('#', ''), 16) || 0;
      c = [(v >> 16) & 255, (v >> 8) & 255, v & 255];
      colorCache.set(hex, c);
    }
    return c;
  }

  async function fetchAndDraw(targetResolution: number) {
    if (!bounds || !jobId) return;
    const [hAxis, vAxis] = planeAxes(axis);
    const [hLoRaw, hHiRaw] = axisBounds(hAxis);
    const [vLoRaw, vHiRaw] = axisBounds(vAxis);
    const hLo = Number.isFinite(hLoRaw) ? hLoRaw : -1;
    const hHi = Number.isFinite(hHiRaw) && hHiRaw > hLo ? hHiRaw : hLo + 1;
    const vLo = Number.isFinite(vLoRaw) ? vLoRaw : -1;
    const vHi = Number.isFinite(vHiRaw) && vHiRaw > vLo ? vHiRaw : vLo + 1;

    rendering = true;
    renderError = null;
    try {
      const result = await api.jobs.raster(jobId, {
        axis, coord: sliceCoord,
        h_min: hLo, h_max: hHi, v_min: vLo, v_max: vHi,
        resolution: targetResolution,
      });
      raster = result;
      draw(result);
    } catch (e: unknown) {
      renderError = e instanceof Error ? e.message : String(e);
    } finally {
      rendering = false;
    }
  }

  function lutColorFor(entry: RasterResponse['legend'][number]): string {
    if (colorMode === 'tally') {
      const row = entry.cell_name ? tallyByName.get(entry.cell_name) : undefined;
      if (!row) return NO_DATA_COLOR;
      return valueToColor(row.mean, scale);
    }
    return colorForMaterial(entry.material_id);
  }

  function draw(r: RasterResponse) {
    if (!canvasEl) return;
    canvasEl.width = r.width;
    canvasEl.height = r.height;
    const ctx = canvasEl.getContext('2d');
    if (!ctx) return;

    // Build a small LUT (one entry per cell in the legend) instead of
    // resolving a color per pixel — the expensive part (which cell owns
    // this pixel) already happened server-side; this is just a lookup.
    const lut: [number, number, number][] = r.legend.map((e) => hexToRgb(lutColorFor(e)));
    const voidRgb = hexToRgb(VOID_COLOR);

    const img = ctx.createImageData(r.width, r.height);
    for (let i = 0; i < r.cell_index.length; i++) {
      const idx = r.cell_index[i];
      const rgb = idx === -1 ? voidRgb : (lut[idx] ?? voidRgb);
      const p = i * 4;
      img.data[p] = rgb[0]; img.data[p + 1] = rgb[1]; img.data[p + 2] = rgb[2]; img.data[p + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
  }

  // Re-draw locally (no new fetch) when only the color mapping changes.
  function redrawOnly() {
    if (raster) draw(raster);
  }

  const PREVIEW_RESOLUTION = 96;
  let debounceHandle: ReturnType<typeof setTimeout> | undefined;

  $effect(() => {
    axis; sliceFrac; resolution; jobId; bounds;
    if (!bounds) return;
    fetchAndDraw(PREVIEW_RESOLUTION);
    clearTimeout(debounceHandle);
    debounceHandle = setTimeout(() => fetchAndDraw(resolution), 150);
  });

  $effect(() => {
    colorMode; selectedScore; scale;
    redrawOnly();
  });

  function onCanvasMove(e: MouseEvent) {
    if (!raster) return;
    const rect = canvasEl.getBoundingClientRect();
    const col = Math.floor(((e.clientX - rect.left) / rect.width) * raster.width);
    const row = Math.floor(((e.clientY - rect.top) / rect.height) * raster.height);
    if (row < 0 || row >= raster.height || col < 0 || col >= raster.width) { hoverInfo = null; return; }
    const idx = raster.cell_index[row * raster.width + col];
    if (idx === -1) { hoverInfo = { material: null, cellName: null, value: null }; return; }
    const entry = raster.legend[idx];
    const row2 = entry.cell_name ? tallyByName.get(entry.cell_name) : undefined;
    hoverInfo = {
      material: entry.material_id,
      cellName: entry.cell_name,
      value: colorMode === 'tally' ? (row2?.mean ?? null) : null,
    };
  }
  function onCanvasLeave() { hoverInfo = null; }

  function fmt(v: number): string {
    if (!Number.isFinite(v)) return '—';
    const abs = Math.abs(v);
    if (abs >= 1e4 || (abs > 0 && abs < 1e-3)) return v.toExponential(3);
    return v.toPrecision(4);
  }
</script>

<div class="geom-plot-panel">
  <div class="panel-body">
    {#if loading}
      <div class="empty-note">Loading geometry…</div>
    {:else if loadError}
      <div class="empty-note error">{loadError}</div>
    {:else if bounds}
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

        {#if scoreKeys.length > 0}
          <div class="axis-picker" role="group" aria-label="Color by">
            <button class="axis-btn" class:active={colorMode === 'material'} onclick={() => (colorMode = 'material')}>Material</button>
            <button class="axis-btn" class:active={colorMode === 'tally'} onclick={() => (colorMode = 'tally')}>Tally</button>
          </div>
        {/if}

        {#if rendering}<span class="rendering-note">rendering…</span>{/if}
      </div>

      {#if colorMode === 'tally'}
        <PanelToolBar scores={scoreKeys} bind:selectedScore bind:scaleType />
        <div class="controls-row">
          <div class="axis-picker" role="group" aria-label="Color scale mode">
            <button class="axis-btn" class:active={scaleMode === 'dynamic'} onclick={() => (scaleMode = 'dynamic')}>Dynamic</button>
            <button class="axis-btn" class:active={scaleMode === 'static'} onclick={() => (scaleMode = 'static')}>Static</button>
          </div>
          {#if scaleMode === 'static'}
            <label class="range-field">min <input type="number" step="any" bind:value={staticMin} /></label>
            <label class="range-field">max <input type="number" step="any" bind:value={staticMax} /></label>
            <button class="use-range-btn" onclick={useCurrentRange}>Use current range</button>
          {/if}
        </div>
      {/if}

      <div class="slice-row">
        <span class="slice-label">Slice position</span>
        <input type="range" min="0" max="1" step="0.002" bind:value={sliceFrac} class="slice-slider" />
        <span class="slice-index mono">{sliceCoord.toPrecision(4)} cm</span>
      </div>

      <div class="canvas-wrap">
        <canvas bind:this={canvasEl} onmousemove={onCanvasMove} onmouseleave={onCanvasLeave}></canvas>
        {#if hoverInfo}
          <div class="hover-readout mono">
            {hoverInfo.material ?? 'void'}{#if hoverInfo.cellName} — {hoverInfo.cellName}{/if}
            {#if hoverInfo.value != null} — {selectedScore}: {fmt(hoverInfo.value)}{/if}
          </div>
        {/if}
      </div>
      {#if renderError}
        <div class="empty-note error">Render failed: {renderError}</div>
      {/if}

      {#if colorMode === 'tally' && rows.length > 0}
        <ColorLegend {scale} />
      {:else}
        <div class="legend-row">
          {#each Object.entries(materialColors) as [mat, color]}
            <span class="legend-chip"><i style="background:{color}"></i>{mat}</span>
          {/each}
          <span class="legend-chip"><i style="background:{VOID_COLOR}"></i>void</span>
        </div>
      {/if}
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

  .range-field {
    display: flex; align-items: center; gap: 5px; font-size: 11px;
    color: var(--color-subtext); font-family: var(--font-mono);
  }
  .range-field input {
    width: 84px; background: var(--color-bg-raised); border: 1px solid var(--color-border);
    border-radius: 2px; color: var(--color-text); font-family: var(--font-mono);
    font-size: 12px; padding: 5px 8px;
  }
  .use-range-btn {
    font-family: var(--font-mono); background: var(--color-bg-raised);
    border: 1px solid var(--color-border); color: var(--color-text);
    font-size: 10px; padding: 5px 8px; border-radius: 2px; cursor: pointer;
  }
  .use-range-btn:hover { border-color: var(--color-accent); color: var(--color-accent-hi); }

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
    white-space: nowrap;
  }

  .legend-row { display: flex; flex-wrap: wrap; gap: 10px; }
  .legend-chip { display: flex; align-items: center; gap: 5px; font-size: 10px; color: var(--color-subtext); font-family: var(--font-mono); }
  .legend-chip i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }

  .mono { font-family: var(--font-mono); }
</style>