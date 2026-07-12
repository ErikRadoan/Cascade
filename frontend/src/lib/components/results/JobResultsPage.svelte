<script lang="ts">
  // JobResultsPage — shell for a completed job's results.
  // Structure per results-dashboard-spec.md §1:
  //   SummaryStrip (always) / StepSelector (multi-step only) / Tabs (B/C/D/E)
  //
  // Panel A (Summary) and Panel B (Scalars) are wired up for real below;
  // Panels C/D/E are still stubs. See spec §5 for the ownership split.

  import { results, jobs } from '$lib/api';
  import SummaryPanel from './SummaryPanel.svelte';
  import ScalarsPanel from './ScalarPanel.svelte';
  import type { ImportSummaryResponse, ImportTalliesResponse } from '../shared/ResultsTypes';
  import type { SceneResponse } from '$lib/types';

  let { jobId }: { jobId: string } = $props();

  type TabId = 'scalars' | 'mesh' | 'spectra' | 'depletion';
  const tabs: { id: TabId; label: string }[] = [
    { id: 'scalars', label: 'Scalars' },
    { id: 'mesh', label: 'Mesh' },
    { id: 'spectra', label: 'Spectra' },
    { id: 'depletion', label: 'Depletion / R2S' },
  ];

  let activeTab = $state<TabId>('scalars');

  let summary = $state<ImportSummaryResponse | null>(null);
  let tallies = $state<ImportTalliesResponse | null>(null);
  let scene = $state<SceneResponse | null>(null);
  let loadError = $state<string | null>(null);
  let loading = $state(true);

  $effect(() => {
    loading = true;
    loadError = null;
    summary = null;
    tallies = null;
    scene = null;

    Promise.allSettled([
      results.summary(jobId),
      results.tallies(jobId),
      jobs.scene(jobId),
    ]).then(([s, t, sc]) => {
      if (s.status === 'fulfilled') summary = s.value as ImportSummaryResponse;
      if (t.status === 'fulfilled') tallies = t.value as ImportTalliesResponse;
      // scene 404s for jobs predating geometry_text persistence — treat as
      // "no 3D preview", not a load failure (see api/index.ts jobs.scene doc).
      if (sc.status === 'fulfilled') scene = sc.value as SceneResponse;

      if (s.status === 'rejected') {
        loadError = s.reason instanceof Error ? s.reason.message : String(s.reason);
      }
    }).finally(() => { loading = false; });
  });
</script>

<div class="results-page">
  <div class="results-body">
    <aside class="summary-aside">
      <div class="aside-header">
        <svg class="aside-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M2 13V7M6 13V3M10 13V9M14 13V5" />
        </svg>
        <span class="aside-title">Summary</span>
      </div>
      <div class="aside-body">
        {#if loading}
          <div class="strip-note">Loading summary…</div>
        {:else if loadError}
          <div class="strip-note error">Failed to load summary: {loadError}</div>
        {:else if summary}
          <SummaryPanel {summary} />
        {/if}
      </div>
    </aside>

    <section class="main-area">
      <div class="tabs-bar" role="tablist">
        {#each tabs as tab}
          <button
            class="tab-btn"
            class:active={activeTab === tab.id}
            role="tab"
            aria-selected={activeTab === tab.id}
            onclick={() => (activeTab = tab.id)}
          >
            {tab.label}
          </button>
        {/each}
      </div>

      <div class="tab-body">
        {#if activeTab === 'scalars'}
          {#if loading}
            <div class="stub">Loading…</div>
          {:else if tallies}
            <ScalarsPanel {tallies} {scene} />
          {:else}
            <div class="stub">No scalar tally data available for this job.</div>
          {/if}
        {:else if activeTab === 'mesh'}
          <div class="stub">Panel C — Mesh tally (slice / volumetric / profile) — not yet built.</div>
        {:else if activeTab === 'spectra'}
          <div class="stub">Panel D — Energy spectra (log-log step plot / table) — not yet built.</div>
        {:else if activeTab === 'depletion'}
          <div class="stub">Depletion / R2S results — blocked on backend schema (spec §2.5). Not yet available.</div>
        {/if}
      </div>
    </section>
  </div>
</div>

<style>
  .results-page {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }

  .results-body {
    display: grid;
    grid-template-columns: 340px 1fr;
    flex: 1;
    overflow: hidden;
  }

  .summary-aside {
    display: flex;
    flex-direction: column;
    border-right: 1px solid var(--color-border);
    background: var(--color-bg-panel);
    overflow: hidden;
  }

  .aside-header {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 34px;
    padding: 0 10px;
    background: var(--color-bg-raised);
    border-bottom: 1px solid var(--color-border);
    flex-shrink: 0;
  }

  .aside-icon {
    width: 13px;
    height: 13px;
    color: var(--color-subtext);
    opacity: 0.7;
    flex-shrink: 0;
  }

  .aside-title {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--color-subtext);
  }

  .aside-body {
    flex: 1;
    overflow-y: auto;
    min-height: 0;
  }

  .strip-note {
    padding: 14px;
    font-size: 12px;
    color: var(--color-subtext);
  }

  .strip-note.error {
    color: #f87171;
  }

  .main-area {
    display: flex;
    flex-direction: column;
    overflow: hidden;
    min-width: 0;
  }

  .tabs-bar {
    display: flex;
    align-items: flex-end;
    gap: 0;
    padding: 0 10px;
    background: var(--color-bg-deep);
    border-bottom: 1px solid var(--color-border);
    flex-shrink: 0;
    height: 34px;
  }

  .tab-btn {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--color-subtext);
    background: transparent;
    border: none;
    border-right: 1px solid var(--color-border);
    height: calc(100% - 1px);
    padding: 0 14px;
    cursor: pointer;
    /* Same chamfered top-right corner as GeometryTabBar's project tabs —
       one tab vocabulary across the whole app, not just the editor. */
    clip-path: polygon(0 0, calc(100% - 8px) 0, 100% 8px, 100% 100%, 0 100%);
    transition: background-color 0.1s ease, color 0.1s ease;
  }

  .tab-btn:hover {
    background: var(--color-bg-panel);
    color: var(--color-text);
  }

  .tab-btn:focus-visible {
    outline: 1px solid var(--color-accent);
    outline-offset: -1px;
  }

  .tab-btn.active {
    background: var(--color-bg-panel);
    color: var(--color-accent-hi);
    box-shadow: inset 0 -2px 0 var(--color-accent);
  }

  .tab-body {
    flex: 1;
    overflow: auto;
  }

  .stub {
    padding: 24px;
    font-size: 12px;
    color: var(--color-subtext);
    font-style: italic;
  }
</style>