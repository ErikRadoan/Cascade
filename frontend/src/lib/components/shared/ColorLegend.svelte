<script lang="ts">
  // Shared legend for every scalar overlay (3D heatmap, mesh slice, bar
  // chart axes). One component so "0.2, log, blue-red" reads identically
  // everywhere — see results-dashboard-spec.md §3.

  import { rampColor, type ColorScale } from './ColorMap.ts';

  let { scale, unit = '' }: { scale: ColorScale; unit?: string } = $props();

  const STOP_COUNT = 24;
  const stops = $derived(
    Array.from({ length: STOP_COUNT }, (_, i) => rampColor(i / (STOP_COUNT - 1))),
  );

  function fmt(v: number): string {
    if (v === 0) return '0';
    const abs = Math.abs(v);
    if (abs >= 1e4 || abs < 1e-3) return v.toExponential(1);
    return v.toPrecision(3);
  }
</script>

<div class="legend">
  <div class="legend-bar" style="background: linear-gradient(to right, {stops.join(', ')})"></div>
  <div class="legend-labels">
    <span>{fmt(scale.min)}{unit}</span>
    <span class="legend-scale-type">{scale.type}</span>
    <span>{fmt(scale.max)}{unit}</span>
  </div>
  <div class="legend-flag">
    <span class="flag-swatch"></span>
    rel. err. &gt; 10%
  </div>
</div>

<style>
  .legend {
    display: flex;
    flex-direction: column;
    gap: 3px;
    min-width: 160px;
  }

  .legend-bar {
    height: 8px;
    border-radius: 2px;
    border: 1px solid var(--color-border);
  }

  .legend-labels {
    display: flex;
    justify-content: space-between;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-subtext);
  }

  .legend-scale-type {
    text-transform: uppercase;
    letter-spacing: 0.05em;
    opacity: 0.7;
  }

  .legend-flag {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 10px;
    color: var(--color-subtext);
  }

  .flag-swatch {
    width: 10px;
    height: 10px;
    border-radius: 2px;
    background: repeating-linear-gradient(
      45deg,
      #f87171,
      #f87171 2px,
      transparent 2px,
      transparent 4px
    );
    border: 1px solid #ef4444;
  }
</style>