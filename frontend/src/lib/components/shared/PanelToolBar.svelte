<script lang="ts">
  // Shared toolbar layout for every results panel: score selector + display-
  // mode segmented control + log/linear toggle, always top-right, same
  // position in every panel — results-dashboard-spec.md §3.
  //
  // A panel with no modes (Panel A/Summary) or no scale toggle (Panel D has
  // log-log fixed) just omits those props; the toolbar collapses to
  // whatever's left rather than rendering empty controls.

  import type { ScaleType } from './ColorMap.ts';

  let {
    scores = [],
    selectedScore = $bindable(''),
    modes = [],
    activeMode = $bindable(''),
    scaleType = $bindable<ScaleType>('log'),
    showScaleToggle = true,
  }: {
    scores?: string[];
    selectedScore?: string;
    modes?: { id: string; label: string }[];
    activeMode?: string;
    scaleType?: ScaleType;
    showScaleToggle?: boolean;
  } = $props();
</script>

<div class="toolbar">
  {#if scores.length > 0}
    <select class="tb-select" bind:value={selectedScore} aria-label="Score">
      {#each scores as s}
        <option value={s}>{s}</option>
      {/each}
    </select>
  {/if}

  {#if modes.length > 0}
    <div class="tb-segmented" role="tablist">
      {#each modes as m}
        <button
          class="tb-seg-btn"
          class:active={activeMode === m.id}
          role="tab"
          aria-selected={activeMode === m.id}
          onclick={() => (activeMode = m.id)}
        >
          {m.label}
        </button>
      {/each}
    </div>
  {/if}

  {#if showScaleToggle}
    <div class="tb-segmented">
      <button
        class="tb-seg-btn"
        class:active={scaleType === 'log'}
        onclick={() => (scaleType = 'log')}
      >log</button>
      <button
        class="tb-seg-btn"
        class:active={scaleType === 'linear'}
        onclick={() => (scaleType = 'linear')}
      >linear</button>
    </div>
  {/if}
</div>

<style>
  .toolbar {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    padding: 6px 10px;
    border-bottom: 1px solid var(--color-border);
    flex-shrink: 0;
  }

  .tb-select {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text);
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    border-radius: 5px;
    padding: 4px 8px;
  }

  .tb-segmented {
    display: flex;
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    border-radius: 6px;
    padding: 2px;
    gap: 2px;
  }

  .tb-seg-btn {
    font-family: var(--font-sans);
    font-size: 11px;
    color: var(--color-subtext);
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 3px 9px;
    cursor: pointer;
    transition: color 0.1s, background 0.1s;
  }

  .tb-seg-btn:hover {
    color: var(--color-text);
  }

  .tb-seg-btn.active {
    color: var(--color-accent-hi);
    background: rgba(6, 182, 212, 0.15);
  }
</style>