<script lang="ts">
  // Panel A — Summary. Fixed layout, no display-mode switcher (spec §4).
  // Hides k-eff/convergence content entirely for fixed-source legs where
  // k_effective is empty — shows a note instead of erroring.

  import { hasKEff, type ImportSummaryResponse, type KEffEstimate } from '../shared/ResultsTypes';

  let { summary }: { summary: ImportSummaryResponse } = $props();

  const showKEff = $derived(hasKEff(summary));
  let secondaryOpen = $state(false);

  // Built as an object array (not a [label, est] tuple literal) — a tuple
  // literal here infers as (string | KEffEstimate)[][], which widens `est`
  // to string | KEffEstimate in the template and breaks `.mean`/`.std_dev`.
  const secondaryEstimators = $derived.by(() => {
    if (!showKEff) return [] as { label: string; est: KEffEstimate }[];
    const keff = summary.k_effective as {
      combined: KEffEstimate; col_abs: KEffEstimate; abs_tra: KEffEstimate; col_tra: KEffEstimate;
    };
    return [
      { label: 'col-abs', est: keff.col_abs },
      { label: 'abs-tra', est: keff.abs_tra },
      { label: 'col-tra', est: keff.col_tra },
    ];
  });

  function pct(std: number, mean: number): string {
    if (mean === 0) return '—';
    return `${((std / mean) * 100).toFixed(3)}%`;
  }

  // ---- Convergence chart (hand-rolled SVG, dual y-axis) ------------------
  const CHART_W = 560;
  const CHART_H = 200;
  const PAD = { top: 12, right: 44, bottom: 24, left: 48 };

  function buildPath(values: number[], yMin: number, yMax: number, w: number, h: number): string {
    if (values.length < 2) return '';
    const span = yMax - yMin || 1;
    return values
      .map((v, i) => {
        const x = PAD.left + (i / (values.length - 1)) * w;
        const y = PAD.top + h - ((v - yMin) / span) * h;
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(' ');
  }

  const innerW = $derived(CHART_W - PAD.left - PAD.right);
  const innerH = $derived(CHART_H - PAD.top - PAD.bottom);

  const keffRange = $derived.by(() => {
    const vals = summary.keff_history.filter(Number.isFinite);
    if (vals.length === 0) return { min: 0, max: 1 };
    const min = Math.min(...vals), max = Math.max(...vals);
    const pad = (max - min) * 0.1 || 0.01;
    return { min: min - pad, max: max + pad };
  });

  const entropyRange = $derived.by(() => {
    const vals = summary.entropy_history.filter(Number.isFinite);
    if (vals.length === 0) return { min: 0, max: 1 };
    const min = Math.min(...vals), max = Math.max(...vals);
    const pad = (max - min) * 0.1 || 0.01;
    return { min: min - pad, max: max + pad };
  });

  const keffPath = $derived(buildPath(summary.keff_history, keffRange.min, keffRange.max, innerW, innerH));
  const entropyPath = $derived(buildPath(summary.entropy_history, entropyRange.min, entropyRange.max, innerW, innerH));

  // Vertical marker at the inactive -> active batch boundary
  const markerX = $derived.by(() => {
    const total = summary.keff_history.length;
    if (total < 2) return null;
    const frac = summary.inactive / (summary.batches || total);
    return PAD.left + frac * innerW;
  });

  function tick(v: number): string {
    return Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(3);
  }

  const timingEntries = $derived(Object.entries(summary.timing ?? {}));
</script>

<div class="summary-panel">
  {#if showKEff}
    {@const keff = summary.k_effective as { combined: KEffEstimate; col_abs: KEffEstimate; abs_tra: KEffEstimate; col_tra: KEffEstimate }}
    <div class="row">
      <div class="keff-primary">
        <span class="keff-label">k-effective (combined)</span>
        <span class="keff-value">{keff.combined.mean.toFixed(5)}</span>
        <span class="keff-std">± {keff.combined.std_dev.toFixed(5)} ({pct(keff.combined.std_dev, keff.combined.mean)})</span>
      </div>

      <button class="secondary-toggle" onclick={() => (secondaryOpen = !secondaryOpen)}>
        {secondaryOpen ? 'Hide' : 'Show'} other estimators
      </button>
    </div>

    {#if secondaryOpen}
      <div class="secondary-row">
        {#each secondaryEstimators as { label, est }}
          <div class="keff-secondary">
            <span class="keff-label-sm">{label}</span>
            <span class="keff-value-sm">{est.mean.toFixed(5)}</span>
            <span class="keff-std-sm">± {est.std_dev.toFixed(5)}</span>
          </div>
        {/each}
      </div>
    {/if}

    <div class="card">
      <div class="card-title">Convergence — k-eff &amp; Shannon entropy</div>
      <svg viewBox="0 0 {CHART_W} {CHART_H}" class="convergence-chart">
        <!-- axes -->
        <line x1={PAD.left} y1={PAD.top} x2={PAD.left} y2={PAD.top + innerH} class="axis-line" />
        <line x1={PAD.left} y1={PAD.top + innerH} x2={PAD.left + innerW} y2={PAD.top + innerH} class="axis-line" />
        <line x1={PAD.left + innerW} y1={PAD.top} x2={PAD.left + innerW} y2={PAD.top + innerH} class="axis-line" />

        {#if markerX !== null}
          <line x1={markerX} y1={PAD.top} x2={markerX} y2={PAD.top + innerH} class="marker-line" />
          <text x={markerX + 4} y={PAD.top + 10} class="marker-label">active batches →</text>
        {/if}

        <path d={keffPath} class="keff-path" />
        <path d={entropyPath} class="entropy-path" />

        <text x={PAD.left - 6} y={PAD.top + 4} text-anchor="end" class="axis-tick">{tick(keffRange.max)}</text>
        <text x={PAD.left - 6} y={PAD.top + innerH} text-anchor="end" class="axis-tick">{tick(keffRange.min)}</text>

        <text x={PAD.left + innerW + 6} y={PAD.top + 4} class="axis-tick entropy-tick">{tick(entropyRange.max)}</text>
        <text x={PAD.left + innerW + 6} y={PAD.top + innerH} class="axis-tick entropy-tick">{tick(entropyRange.min)}</text>
      </svg>
      <div class="chart-legend">
        <span><i class="swatch keff"></i> k-eff (left axis)</span>
        <span><i class="swatch entropy"></i> entropy (right axis)</span>
      </div>
    </div>
  {:else}
    <div class="card note">Fixed-source run — no k-effective / convergence data.</div>
  {/if}

  <div class="card">
    <div class="card-title">Timing</div>
    {#if timingEntries.length === 0}
      <div class="note-inline">No timing data for this run.</div>
    {:else}
      <table class="timing-table">
        <tbody>
          {#each timingEntries as [label, value]}
            <tr><td>{label}</td><td>{value}</td></tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>

  <div class="meta-strip">
    <span>{summary.batches} batches</span>
    <span>{summary.inactive} inactive</span>
    <span>{summary.particles_per_batch.toLocaleString()} particles/batch</span>
    <span>{summary.n_realizations} realizations</span>
  </div>
</div>

<style>
  .summary-panel {
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 14px;
    overflow-y: auto;
    height: 100%;
  }

  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px;
  }

  .keff-primary {
    display: flex;
    align-items: baseline;
    gap: 10px;
  }

  .keff-label {
    font-size: 11px;
    color: var(--color-subtext);
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }

  .keff-value {
    font-family: var(--font-mono);
    font-size: 26px;
    font-weight: 600;
    color: var(--color-accent-hi);
  }

  .keff-std {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--color-subtext);
  }

  .secondary-toggle {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text);
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    border-radius: 2px;
    padding: 5px 9px;
    cursor: pointer;
  }

  .secondary-toggle:hover {
    color: var(--color-accent-hi);
    border-color: var(--color-accent);
  }

  .secondary-row {
    display: flex;
    gap: 16px;
    padding: 8px 0;
    border-top: 1px dashed var(--color-border);
  }

  .keff-secondary {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .keff-label-sm {
    font-size: 10px;
    color: var(--color-subtext);
    text-transform: uppercase;
  }

  .keff-value-sm {
    font-family: var(--font-mono);
    font-size: 14px;
    color: var(--color-text);
  }

  .keff-std-sm {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-subtext);
  }

  .card {
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    border-radius: 2px;
    padding: 12px;
  }

  .card.note,
  .note-inline {
    font-size: 12px;
    color: var(--color-subtext);
    font-style: italic;
  }

  .card-title {
    font-size: 11px;
    color: var(--color-subtext);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 8px;
  }

  .convergence-chart {
    width: 100%;
    height: auto;
  }

  .axis-line {
    stroke: var(--color-border);
    stroke-width: 1;
  }

  .marker-line {
    stroke: var(--color-subtext);
    stroke-dasharray: 3 3;
    stroke-width: 1;
  }

  .marker-label {
    font-size: 9px;
    fill: var(--color-subtext);
  }

  .keff-path {
    fill: none;
    stroke: var(--color-accent-hi);
    stroke-width: 1.6;
  }

  .entropy-path {
    fill: none;
    stroke: #eab308;
    stroke-width: 1.6;
    stroke-dasharray: 2 2;
  }

  .axis-tick {
    font-family: var(--font-mono);
    font-size: 9px;
    fill: var(--color-subtext);
  }

  .entropy-tick {
    fill: #eab308;
  }

  .chart-legend {
    display: flex;
    gap: 14px;
    font-size: 10px;
    color: var(--color-subtext);
    margin-top: 4px;
  }

  .swatch {
    display: inline-block;
    width: 10px;
    height: 2px;
    margin-right: 4px;
    vertical-align: middle;
  }

  .swatch.keff { background: var(--color-accent-hi); }
  .swatch.entropy { background: #eab308; }

  .timing-table {
    width: 100%;
    font-family: var(--font-mono);
    font-size: 11px;
    border-collapse: collapse;
  }

  .timing-table td {
    padding: 3px 6px;
    color: var(--color-text);
    border-bottom: 1px solid var(--color-border);
  }

  .timing-table td:first-child {
    color: var(--color-subtext);
  }

  .meta-strip {
    display: flex;
    gap: 14px;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-subtext);
    padding-top: 4px;
  }
</style>