<script lang="ts">
  // Panel B — Scalar cell tallies (spec §4, Panel B).
  // Four display modes behind one segmented control; the score dropdown
  // drives all four identically. Mode 1 reuses ResultsViewport3D as-is —
  // no fork, per spec §5 — everything else is this file.

  import ResultsViewport3D from '../shared/ResultsViewport3D.svelte';
  import ColorLegend from '../shared/ColorLegend.svelte';
  import PanelToolBar from '../shared/PanelToolBar.svelte';
  import {
    buildScale, staticScale, valueToColor, valueToT, isFlagged,
    type ScaleType, type ScaleMode,
  } from '../shared/ColorMap';
  import type { ImportTalliesResponse } from '../shared/ResultsTypes';
  import type { SceneResponse } from '$lib/types';

  let {
    tallies,
    scene,
  }: {
    tallies: ImportTalliesResponse;
    scene: SceneResponse | null;
  } = $props();

  type ModeId = '3d' | 'bar' | 'table' | 'material';
  const modes: { id: ModeId; label: string }[] = [
    { id: '3d', label: '3D heatmap' },
    { id: 'bar', label: 'Bar' },
    { id: 'table', label: 'Table' },
    { id: 'material', label: 'By material' },
  ];

  // Union of score keys across all tallies — never hardcode a score list.
  const scoreKeys = $derived.by(() => {
    const seen = new Set<string>();
    for (const t of tallies.tallies) for (const k of Object.keys(t.scores)) seen.add(k);
    return [...seen].sort();
  });

  let selectedScore = $state('');
  let activeMode = $state<ModeId>('3d');
  let scaleType = $state<ScaleType>('log');

  // Static vs. dynamic color domain (independent of scaleType's log/linear
  // choice). Dynamic = domain derived from whatever result is on screen
  // right now (existing buildScale behavior). Static = a fixed domain the
  // user sets, so the same raw value always maps to the same color across
  // different jobs/runs — e.g. "0 to 1000" for temperature every time.
  let scaleMode = $state<ScaleMode>('dynamic');
  let staticMin = $state(0);
  let staticMax = $state(1000);
  let staticBoundsSeeded = false;

  // Default the score once options are known, without clobbering a choice
  // the user already made if tallies reload with the same scores.
  $effect(() => {
    if (!selectedScore && scoreKeys.length > 0) selectedScore = scoreKeys[0];
  });

  interface Row { name: string; mean: number; std_dev: number; rel_err: number; }

  const rows = $derived.by((): Row[] => {
    if (!selectedScore) return [];
    const out: Row[] = [];
    for (const t of tallies.tallies) {
      const s = t.scores[selectedScore];
      if (s) out.push({ name: t.name, mean: s.mean, std_dev: s.std_dev, rel_err: s.rel_err });
    }
    return out;
  });

  const dynamicScale = $derived(buildScale(rows.map((r) => r.mean), scaleType));

  // Seed the static bounds from the first dynamic range we see, so
  // switching to "Static" for the first time isn't a blank 0–1000 default.
  // After that, the user's own edits (or the "Use current range" button)
  // are what drive it — this effect only fires once.
  $effect(() => {
    if (!staticBoundsSeeded && rows.length > 0) {
      staticMin = dynamicScale.min;
      staticMax = dynamicScale.max;
      staticBoundsSeeded = true;
    }
  });

  const scale = $derived(
    scaleMode === 'static' ? staticScale(staticMin, staticMax, scaleType) : dynamicScale,
  );

  function useCurrentRange() {
    staticMin = dynamicScale.min;
    staticMax = dynamicScale.max;
  }

  const cellColors = $derived.by((): Record<string, string> => {
    const m: Record<string, string> = {};
    for (const r of rows) m[r.name] = valueToColor(r.mean, scale);
    return m;
  });

  const cellFlags = $derived.by((): Record<string, boolean> => {
    const m: Record<string, boolean> = {};
    for (const r of rows) m[r.name] = isFlagged(r.rel_err);
    return m;
  });

  const sortedRows = $derived([...rows].sort((a, b) => b.mean - a.mean));

  // ---- Table sort state ----------------------------------------------------
  type SortKey = 'name' | 'mean' | 'std_dev' | 'rel_err';
  let sortKey = $state<SortKey>('mean');
  let sortDesc = $state(true);

  function setSort(key: SortKey) {
    if (sortKey === key) { sortDesc = !sortDesc; }
    else { sortKey = key; sortDesc = true; }
  }

  const tableRows = $derived.by(() => {
    const out = [...rows];
    out.sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      const cmp = typeof av === 'string' ? av.localeCompare(bv as string) : (av as number) - (bv as number);
      return sortDesc ? -cmp : cmp;
    });
    return out;
  });

  // ---- By-material aggregate ------------------------------------------------
  // cell_name -> material_id, pulled from scene geometry per spec §6 (not
  // carried by import_tallies itself).
  const materialByCell = $derived.by(() => {
    const m: Record<string, string> = {};
    if (!scene) return m;
    for (const comp of scene.components) {
      for (const layer of comp.layers) m[layer.cell_name] = layer.material_id;
      if (comp.box) m[comp.box.cell_name] = comp.box.fill_material_id;
    }
    return m;
  });

  interface MaterialRow { material_id: string; mean: number; std_dev: number; rel_err: number; }

  // Inverse-variance weighted combination — the standard way to combine
  // several independent MC estimates of the same quantity into one. Not
  // specified further in the spec; flagging the assumption here rather
  // than inventing an unweighted average silently.
  const materialRows = $derived.by((): MaterialRow[] => {
    const groups = new Map<string, Row[]>();
    for (const r of rows) {
      const mat = materialByCell[r.name];
      if (!mat) continue;
      if (!groups.has(mat)) groups.set(mat, []);
      groups.get(mat)!.push(r);
    }
    const out: MaterialRow[] = [];
    for (const [material_id, group] of groups) {
      let wSum = 0, wMeanSum = 0;
      for (const g of group) {
        const w = g.std_dev > 0 ? 1 / (g.std_dev * g.std_dev) : 0;
        wSum += w;
        wMeanSum += w * g.mean;
      }
      const mean = wSum > 0 ? wMeanSum / wSum : group.reduce((s, g) => s + g.mean, 0) / group.length;
      const std_dev = wSum > 0 ? Math.sqrt(1 / wSum) : NaN;
      const rel_err = mean !== 0 ? Math.abs(std_dev / mean) : NaN;
      out.push({ material_id, mean, std_dev, rel_err });
    }
    return out.sort((a, b) => b.mean - a.mean);
  });

  const noMaterialLookup = $derived(scene != null && Object.keys(materialByCell).length === 0);

  function fmt(v: number): string {
    if (!Number.isFinite(v)) return '—';
    const abs = Math.abs(v);
    if (abs >= 1e4 || (abs > 0 && abs < 1e-3)) return v.toExponential(3);
    return v.toPrecision(4);
  }
</script>

<div class="scalars-panel">
  <PanelToolBar
    scores={scoreKeys}
    bind:selectedScore
    {modes}
    bind:activeMode
    bind:scaleType
    showScaleToggle={activeMode !== 'table'}
  />

  <div class="panel-body">
    {#if rows.length > 0 && activeMode !== 'table'}
      <div class="scale-mode-row">
        <span class="scale-mode-label">Color scale</span>

        <div class="scale-mode-toggle" role="group" aria-label="Color scale mode">
          <button
            type="button"
            class="scale-mode-btn"
            class:active={scaleMode === 'dynamic'}
            onclick={() => (scaleMode = 'dynamic')}
          >Dynamic</button>
          <button
            type="button"
            class="scale-mode-btn"
            class:active={scaleMode === 'static'}
            onclick={() => (scaleMode = 'static')}
          >Static</button>
        </div>

        {#if scaleMode === 'dynamic'}
          <span class="scale-mode-hint">spans this result's own min/max</span>
        {:else}
          <div class="static-range-inputs">
            <label>
              min
              <input type="number" bind:value={staticMin} step="any" />
            </label>
            <label>
              max
              <input type="number" bind:value={staticMax} step="any" />
            </label>
            <button
              type="button"
              class="use-range-btn"
              onclick={useCurrentRange}
              title="Copy this result's current min/max into these fields"
            >Use current range</button>
          </div>
        {/if}
      </div>
    {/if}

    {#if rows.length === 0}
      <div class="empty-note">No tally data for score "{selectedScore}".</div>

    {:else if activeMode === '3d'}
      <div class="viewport-wrap">
        <div class="viewport-slot">
          <ResultsViewport3D {scene} {cellColors} {cellFlags} />
        </div>
        <div class="legend-slot">
          <ColorLegend {scale} />
        </div>
      </div>

    {:else if activeMode === 'bar'}
      <div class="bar-list">
        {#each sortedRows as r (r.name)}
          <div class="bar-row" class:flagged={isFlagged(r.rel_err)}>
            <span class="bar-name">{r.name}</span>
            <div class="bar-track">
              <div class="bar-fill" style="width: {valueToT(r.mean, scale) * 100}%; background: {valueToColor(r.mean, scale)}"></div>
            </div>
            <span class="bar-value">{fmt(r.mean)}</span>
          </div>
        {/each}
      </div>
      <ColorLegend {scale} />

    {:else if activeMode === 'table'}
      <table class="data-table">
        <thead>
          <tr>
            <th onclick={() => setSort('name')}>Cell {sortKey === 'name' ? (sortDesc ? '▼' : '▲') : ''}</th>
            <th onclick={() => setSort('mean')}>Mean {sortKey === 'mean' ? (sortDesc ? '▼' : '▲') : ''}</th>
            <th onclick={() => setSort('std_dev')}>Std. dev. {sortKey === 'std_dev' ? (sortDesc ? '▼' : '▲') : ''}</th>
            <th onclick={() => setSort('rel_err')}>Rel. err. {sortKey === 'rel_err' ? (sortDesc ? '▼' : '▲') : ''}</th>
          </tr>
        </thead>
        <tbody>
          {#each tableRows as r (r.name)}
            <tr class:flagged={isFlagged(r.rel_err)}>
              <td>{r.name}</td>
              <td>{fmt(r.mean)}</td>
              <td>{fmt(r.std_dev)}</td>
              <td>{(r.rel_err * 100).toFixed(2)}%</td>
            </tr>
          {/each}
        </tbody>
      </table>

    {:else if activeMode === 'material'}
      {#if noMaterialLookup}
        <div class="empty-note">Geometry not available — can't resolve cell → material lookup.</div>
      {:else}
        <div class="bar-list">
          {#each materialRows as r (r.material_id)}
            <div class="bar-row" class:flagged={isFlagged(r.rel_err)}>
              <span class="bar-name">{r.material_id}</span>
              <div class="bar-track">
                <div class="bar-fill" style="width: {valueToT(r.mean, scale) * 100}%; background: {valueToColor(r.mean, scale)}"></div>
              </div>
              <span class="bar-value">{fmt(r.mean)}</span>
            </div>
          {/each}
        </div>
        <ColorLegend {scale} />
      {/if}
    {/if}
  </div>
</div>

<style>
  .scalars-panel {
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
    gap: 10px;
  }

  .empty-note {
    font-size: 12px;
    color: var(--color-subtext);
    font-style: italic;
    padding: 20px;
  }

  .scale-mode-row {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    padding-bottom: 2px;
  }

  .scale-mode-label {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--color-subtext);
    flex-shrink: 0;
  }

  .scale-mode-toggle {
    display: flex;
    border: 1px solid var(--color-border);
    border-radius: 2px;
    overflow: hidden;
    flex-shrink: 0;
  }

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

  .scale-mode-btn + .scale-mode-btn {
    border-left: 1px solid var(--color-border);
  }

  .scale-mode-btn:hover {
    color: var(--color-text);
  }

  .scale-mode-btn.active {
    background: rgba(6, 182, 212, 0.12);
    color: var(--color-accent-hi);
  }

  .scale-mode-hint {
    font-size: 11px;
    color: var(--color-subtext);
    opacity: 0.7;
    font-style: italic;
  }

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

  .static-range-inputs input:focus {
    outline: none;
    border-color: var(--color-accent);
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

  .use-range-btn:hover {
    border-color: var(--color-accent);
    color: var(--color-accent-hi);
  }

  .viewport-wrap {
    position: relative;
    flex: 1;
    min-height: 360px;
  }

  .viewport-slot {
    height: 100%;
    background: var(--color-bg-deep, #0f172a);
    border: 1px solid var(--color-border);
    border-radius: 2px;
    overflow: hidden;
  }

  .legend-slot {
    position: absolute;
    top: 12px;
    left: 12px;
    background: var(--color-bg-panel);
    border: 1px solid var(--color-border);
    border-radius: 2px;
    padding: 8px 10px;
  }

  .bar-list {
    display: flex;
    flex-direction: column;
    gap: 5px;
  }

  .bar-row {
    display: grid;
    grid-template-columns: 140px 1fr 90px;
    align-items: center;
    gap: 8px;
  }

  .bar-row.flagged .bar-name {
    color: #f87171;
  }

  .bar-name {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-subtext);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .bar-track {
    height: 10px;
    background: var(--color-bg-raised);
    border-radius: 2px;
    overflow: hidden;
  }

  .bar-fill {
    height: 100%;
    border-radius: 2px;
  }

  .bar-row.flagged .bar-fill {
    opacity: 0.55;
    background-image: repeating-linear-gradient(
      45deg, rgba(0,0,0,0.25), rgba(0,0,0,0.25) 3px, transparent 3px, transparent 6px
    );
  }

  .bar-value {
    font-family: var(--font-mono);
    font-size: 11px;
    text-align: right;
    color: var(--color-text);
  }

  .data-table {
    width: 100%;
    border-collapse: collapse;
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .data-table th {
    text-align: left;
    color: var(--color-subtext);
    font-weight: 600;
    padding: 6px 8px;
    border-bottom: 1px solid var(--color-border);
    cursor: pointer;
    user-select: none;
    white-space: nowrap;
  }

  .data-table th:hover {
    color: var(--color-text);
  }

  .data-table td {
    padding: 5px 8px;
    color: var(--color-text);
    border-bottom: 1px solid var(--color-border);
  }

  .data-table tr.flagged td {
    color: #f87171;
  }
</style>