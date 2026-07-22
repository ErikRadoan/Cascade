<script lang="ts">
  // Panel C — 3-D mesh tally viewer. Slices the voxel grid along one axis
  // and renders it as a 2D heatmap (canvas, since a 500x500 mesh as SVG
  // would be thousands of DOM nodes). Reuses the same ColorMap/legend
  // conventions as Panel B (results-dashboard-spec.md §3) so "flux, log,
  // blue-red" means the same thing in both panels.

  import { onMount } from 'svelte';
  import * as api from '$lib/api';
  import PanelToolBar from '../shared/PanelToolBar.svelte';
  import ColorLegend from '../shared/ColorLegend.svelte';
  import {
    buildScale, staticScale, valueToColor,
    type ScaleType, type ScaleMode, type ColorScale,
  } from '../shared/ColorMap';
  import type { ImportMeshResponse } from '../shared/ResultsTypes';

  let { jobId }: { jobId: string } = $props();

  // ---- Fetch ----------------------------------------------------------
  let mesh = $state<ImportMeshResponse | null>(null);
  let loading = $state(false);
  let loadError = $state<string | null>(null);
  let notRequested = $state(false);

  async function load(id: string) {
    loading = true;
    loadError = null;
    notRequested = false;
    mesh = null;
    try {
      mesh = (await api.results.mesh(id)) as unknown as ImportMeshResponse;
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      // Backend 404s with "not requested" when results_config.mesh.enabled
      // was false at submit time — that's a config state, not a failure.
      if (msg.startsWith('API 404')) {
        notRequested = true;
      } else {
        loadError = msg;
      }
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    if (jobId) load(jobId);
  });

  // ---- Controls ---------------------------------------------------------
  type Axis = 'x' | 'y' | 'z';
  const AXIS_LABELS: Record<Axis, string> = { x: 'YZ (fix X)', y: 'XZ (fix Y)', z: 'XY (fix Z)' };

  let selectedScore = $state('');
  let axis = $state<Axis>('z');
  let sliceIndex = $state(0);
  let scaleType = $state<ScaleType>('log');
  let scaleMode = $state<ScaleMode>('dynamic');
  let staticMin = $state(0);
  let staticMax = $state(1);
  let staticSeeded = false;

  $effect(() => {
    if (mesh && !selectedScore && mesh.scores.length > 0) selectedScore = mesh.scores[0];
  });

  // Reset slice index to the middle whenever axis or mesh changes, so
  // switching planes doesn't leave the slider pointing off the new range.
  $effect(() => {
    const n = dimForAxis(axis);
    sliceIndex = Math.floor(n / 2);
  });

  const nx = $derived(mesh?.mesh.shape?.[0] ?? 1);
  const ny = $derived(mesh?.mesh.shape?.[1] ?? 1);
  const nz = $derived(mesh?.mesh.shape?.[2] ?? 1);

  function dimForAxis(a: Axis): number {
    if (a === 'x') return nx;
    if (a === 'y') return ny;
    return nz;
  }

  // ---- Flatten selected score into a lookup by (ix, iy, iz) --------------
  // Matches the backend's row-major loop order (ix outer, then iy, then iz)
  // — see openmc_adapter.py's import_mesh().
  const flatMean = $derived.by((): Float64Array => {
    if (!mesh || !selectedScore) return new Float64Array(0);
    const key = `${selectedScore}_mean`;
    const arr = new Float64Array(mesh.data.length);
    for (let i = 0; i < mesh.data.length; i++) {
      const v = mesh.data[i][key];
      arr[i] = typeof v === 'number' ? v : NaN;
    }
    return arr;
  });

  function valueAt(ix: number, iy: number, iz: number): number {
    return flatMean[ix * ny * nz + iy * nz + iz] ?? NaN;
  }

  // Global scale over the WHOLE volume's selected score (not just the
  // current slice) — so paging through slices doesn't rescale colors
  // under you and make two slices look artificially similar/different.
  const dynamicScale = $derived(buildScale(Array.from(flatMean), scaleType));

  $effect(() => {
    if (!staticSeeded && flatMean.length > 0) {
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

  // ---- Slice geometry: (rows, cols) dims for the current axis -----------
  const sliceDims = $derived.by((): { rows: number; cols: number; rowsLabel: string; colsLabel: string } => {
    if (axis === 'z') return { rows: ny, cols: nx, rowsLabel: 'y', colsLabel: 'x' };
    if (axis === 'y') return { rows: nz, cols: nx, rowsLabel: 'z', colsLabel: 'x' };
    return { rows: nz, cols: ny, rowsLabel: 'z', colsLabel: 'y' };
  });

  function sliceValue(row: number, col: number): number {
    if (axis === 'z') return valueAt(col, row, sliceIndex);   // (ix=col, iy=row, iz=fixed)
    if (axis === 'y') return valueAt(col, sliceIndex, row);   // (ix=col, iy=fixed, iz=row)
    return valueAt(sliceIndex, row, col);                     // (ix=fixed, iy=col? ...) see below
  }
  // NB for axis 'x': rows=z, cols=y -> valueAt(fixed_x, col=y, row=z)
  function sliceValueX(row: number, col: number): number {
    return valueAt(sliceIndex, col, row);
  }

  // ---- Canvas render ------------------------------------------------------
  let canvasEl: HTMLCanvasElement;
  const MAX_CANVAS = 480;

  function draw() {
    if (!canvasEl || !mesh || flatMean.length === 0) return;
    const { rows, cols } = sliceDims;
    if (rows < 1 || cols < 1) return;

    const cell = Math.max(1, Math.floor(MAX_CANVAS / Math.max(rows, cols)));
    const w = cols * cell;
    const h = rows * cell;
    canvasEl.width = w;
    canvasEl.height = h;

    const ctx = canvasEl.getContext('2d');
    if (!ctx) return;
    ctx.imageSmoothingEnabled = false;

    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const v = axis === 'x' ? sliceValueX(r, c) : sliceValue(r, c);
        ctx.fillStyle = Number.isFinite(v) ? valueToColor(v, scale) : '#1e293b';
        ctx.fillRect(c * cell, (rows - 1 - r) * cell, cell, cell); // flip row so +y/+z is "up"
      }
    }
  }

  $effect(() => {
    mesh; selectedScore; axis; sliceIndex; scale;
    draw();
  });

  onMount(() => draw());

  // World-coordinate label for the current slice position, from mesh bounds.
  const sliceWorldCoord = $derived.by((): string => {
    if (!mesh?.mesh.lower_left || !mesh?.mesh.upper_right) return '';
    const axisIdx = axis === 'x' ? 0 : axis === 'y' ? 1 : 2;
    const n = dimForAxis(axis);
    const lo = mesh.mesh.lower_left[axisIdx];
    const hi = mesh.mesh.upper_right[axisIdx];
    if (lo == null || hi == null || n < 1) return '';
    const frac = (sliceIndex + 0.5) / n;
    const coord = lo + frac * (hi - lo);
    return `${axis} ≈ ${coord.toPrecision(4)} cm`;
  });

  function fmt(v: number): string {
    if (!Number.isFinite(v)) return '—';
    const abs = Math.abs(v);
    if (abs >= 1e4 || (abs > 0 && abs < 1e-3)) return v.toExponential(3);
    return v.toPrecision(4);
  }

  // Hover readout
  let hoverValue = $state<number | null>(null);
  function onCanvasMove(e: MouseEvent) {
    if (!mesh) return;
    const { rows, cols } = sliceDims;
    const rect = canvasEl.getBoundingClientRect();
    const c = Math.floor(((e.clientX - rect.left) / rect.width) * cols);
    const r = rows - 1 - Math.floor(((e.clientY - rect.top) / rect.height) * rows);
    if (r < 0 || r >= rows || c < 0 || c >= cols) { hoverValue = null; return; }
    hoverValue = axis === 'x' ? sliceValueX(r, c) : sliceValue(r, c);
  }
  function onCanvasLeave() { hoverValue = null; }
</script>

<div class="mesh-panel">
  {#if mesh}
    <PanelToolBar
      scores={mesh.scores}
      bind:selectedScore
      bind:scaleType
    />
  {/if}

  <div class="panel-body">
    {#if loading}
      <div class="empty-note">Loading mesh…</div>

    {:else if notRequested}
      <div class="empty-note">Mesh tally was not requested for this job — enable it under "3-D mesh tally" when submitting.</div>

    {:else if loadError}
      <div class="empty-note error">{loadError}</div>

    {:else if mesh && flatMean.length === 0}
      <div class="empty-note">No mesh data available yet.</div>

    {:else if mesh}
      <div class="controls-row">
        <div class="axis-picker" role="group" aria-label="Slice axis">
          {#each (['x', 'y', 'z'] as Axis[]) as a}
            <button class="axis-btn" class:active={axis === a} onclick={() => (axis = a)}>
              {AXIS_LABELS[a]}
            </button>
          {/each}
        </div>

        <div class="scale-mode-toggle" role="group" aria-label="Color scale mode">
          <button class="scale-mode-btn" class:active={scaleMode === 'dynamic'} onclick={() => (scaleMode = 'dynamic')}>Dynamic</button>
          <button class="scale-mode-btn" class:active={scaleMode === 'static'} onclick={() => (scaleMode = 'static')}>Static</button>
        </div>

        {#if scaleMode === 'static'}
          <div class="static-range-inputs">
            <label>min <input type="number" bind:value={staticMin} step="any" /></label>
            <label>max <input type="number" bind:value={staticMax} step="any" /></label>
            <button class="use-range-btn" onclick={useCurrentRange}>Use current range</button>
          </div>
        {/if}
      </div>

      <div class="slice-row">
        <span class="slice-label">Slice ({sliceDims.rowsLabel} × {sliceDims.colsLabel})</span>
        <input
          type="range"
          min="0"
          max={Math.max(0, dimForAxis(axis) - 1)}
          bind:value={sliceIndex}
          class="slice-slider"
        />
        <span class="slice-index mono">
          {sliceIndex + 1} / {dimForAxis(axis)}
          {#if sliceWorldCoord}<span class="slice-coord">({sliceWorldCoord})</span>{/if}
        </span>
      </div>

      <div class="canvas-wrap">
        <div class="canvas-frame">
          <canvas
            bind:this={canvasEl}
            onmousemove={onCanvasMove}
            onmouseleave={onCanvasLeave}
          ></canvas>
          {#if hoverValue !== null}
            <div class="hover-readout mono">{selectedScore}: {fmt(hoverValue)}</div>
          {/if}
        </div>
        <ColorLegend {scale} />
      </div>
    {/if}
  </div>
</div>

<style>
  .mesh-panel {
    display: flex;
    flex-direction: column;
    height: 100%;
  }

  .panel-body {
    flex: 1;
    overflow: auto;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .empty-note {
    font-size: 12px;
    color: var(--color-subtext);
    font-style: italic;
    padding: 20px;
  }
  .empty-note.error { color: #f87171; font-style: normal; }

  .controls-row {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }

  .axis-picker,
  .scale-mode-toggle {
    display: flex;
    border: 1px solid var(--color-border);
    border-radius: 2px;
    overflow: hidden;
  }

  .axis-btn,
  .scale-mode-btn {
    background: var(--color-bg-raised);
    color: var(--color-subtext);
    border: none;
    font-family: var(--font-mono);
    font-size: 11px;
    padding: 5px 10px;
    cursor: pointer;
    transition: background-color 0.1s, color 0.1s;
  }
  .axis-btn + .axis-btn,
  .scale-mode-btn + .scale-mode-btn { border-left: 1px solid var(--color-border); }
  .axis-btn:hover,
  .scale-mode-btn:hover { color: var(--color-text); }
  .axis-btn.active,
  .scale-mode-btn.active { background: rgba(6, 182, 212, 0.12); color: var(--color-accent-hi); }

  .static-range-inputs {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .static-range-inputs label {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 11px;
    color: var(--color-subtext);
    font-family: var(--font-mono);
  }
  .static-range-inputs input {
    width: 84px;
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    border-radius: 2px;
    color: var(--color-text);
    font-family: var(--font-mono);
    font-size: 12px;
    padding: 5px 8px;
  }
  .use-range-btn {
    font-family: var(--font-mono);
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    color: var(--color-text);
    font-size: 10px;
    padding: 5px 8px;
    border-radius: 2px;
    cursor: pointer;
  }
  .use-range-btn:hover { border-color: var(--color-accent); color: var(--color-accent-hi); }

  .slice-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .slice-label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--color-subtext);
    flex-shrink: 0;
  }
  .slice-slider { flex: 1; accent-color: var(--color-accent); }
  .slice-index {
    font-size: 11px;
    color: var(--color-text);
    flex-shrink: 0;
    white-space: nowrap;
  }
  .slice-coord { color: var(--color-subtext); margin-left: 4px; }

  .canvas-wrap {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    flex: 1;
  }

  .canvas-frame {
    position: relative;
    background: var(--color-bg-deep, #0f172a);
    border: 1px solid var(--color-border);
    border-radius: 2px;
    padding: 8px;
    display: inline-flex;
  }

  canvas {
    image-rendering: pixelated;
    display: block;
  }

  .hover-readout {
    position: absolute;
    top: 10px;
    right: 10px;
    font-size: 11px;
    background: var(--color-bg-panel);
    border: 1px solid var(--color-border);
    border-radius: 4px;
    padding: 4px 8px;
    color: var(--color-accent-hi);
    pointer-events: none;
  }

  .mono { font-family: var(--font-mono); }
</style>