<script lang="ts">
  import { ui } from '$lib/stores/index.svelte';
  import { onMount } from 'svelte';
  import * as api from '$lib/api';

  // ── State ─────────────────────────────────────────────────────────────────

  let jobId    = $state<string | null>(null);
  let loading  = $state(false);
  let error    = $state<string | null>(null);

  let summary  = $state<unknown>(null);
  let tallies  = $state<unknown>(null);
  let mesh     = $state<unknown>(null);
  let spectra  = $state<unknown>(null);

  // ── Load whenever the store points us at a job ────────────────────────────

  $effect(() => {
    const id = ui.resultsJobId;
    jobId = id ?? null;
    if (id) load(id);
    else clearData();
  });

  function clearData() {
    summary = tallies = mesh = spectra = null;
    error   = null;
  }

  async function load(id: string) {
    loading = true;
    error   = null;
    clearData();

    const settled = await Promise.allSettled([
      api.results.summary(id),
      api.results.tallies(id),
      api.results.mesh(id),
      api.results.spectra(id),
    ]);

    const [s, t, m, sp] = settled;
    if (s.status  === 'fulfilled') summary = s.value;
    if (t.status  === 'fulfilled') tallies = t.value;
    if (m.status  === 'fulfilled') mesh    = m.value;
    if (sp.status === 'fulfilled') spectra = sp.value;

    // Surface errors only if everything failed
    const allFailed = settled.every(r => r.status === 'rejected');
    if (allFailed) {
      const first = settled[0] as PromiseRejectedResult;
      error = first.reason instanceof Error ? first.reason.message : String(first.reason);
    }

    loading = false;
  }

  function pretty(v: unknown): string {
    return JSON.stringify(v, null, 2);
  }
</script>

<div class="rv">

  <!-- Header -->
  <div class="rv-header">
    <span class="rv-title">Results Viewer</span>
    {#if jobId}
      <span class="rv-job-id">{jobId}</span>
    {/if}
    {#if jobId}
      <button class="reload-btn" onclick={() => jobId && load(jobId)} disabled={loading}>
        ↻ Reload
      </button>
    {/if}
  </div>

  <!-- Body -->
  <div class="rv-body">

    {#if !jobId}
      <p class="hint">No job selected — click "View results →" on a completed job.</p>

    {:else if loading}
      <p class="hint">Loading results for {jobId.slice(0, 8)}…</p>

    {:else if error}
      <p class="hint error">{error}</p>

    {:else}

      <!-- Summary block -->
      <details class="block" open>
        <summary class="block-title">
          summary
          {#if summary}
            {@const s = summary as any}
            <span class="keff-inline">
              k<sub>eff</sub> = {s?.k_effective?.combined?.mean?.toFixed(5) ?? '—'}
              ± {s?.k_effective?.combined?.std_dev?.toFixed(5) ?? '—'}
            </span>
          {:else}
            <span class="not-available">not available</span>
          {/if}
        </summary>
        <pre class="dump">{summary ? pretty(summary) : 'No data returned.'}</pre>
      </details>

      <!-- Tallies block -->
      <details class="block">
        <summary class="block-title">
          scalar tallies
          {#if tallies}
            <span class="count">{(tallies as any)?.tallies?.length ?? 0} cells</span>
          {:else}
            <span class="not-available">not available</span>
          {/if}
        </summary>
        <pre class="dump">{tallies ? pretty(tallies) : 'Not requested or not available.'}</pre>
      </details>

      <!-- Mesh block -->
      <details class="block">
        <summary class="block-title">
          mesh tally
          {#if mesh}
            {@const m = mesh as any}
            <span class="count">{m?.data?.length ?? 0} voxels · {m?.scores?.join(', ')}</span>
          {:else}
            <span class="not-available">not available</span>
          {/if}
        </summary>
        <pre class="dump">{mesh ? pretty(mesh) : 'Not requested or not available.'}</pre>
      </details>

      <!-- Spectra block -->
      <details class="block">
        <summary class="block-title">
          energy spectra
          {#if spectra}
            {@const sp = spectra as any}
            <span class="count">{sp?.spectra?.length ?? 0} spectra · {sp?.group_structure}-group</span>
          {:else}
            <span class="not-available">not available</span>
          {/if}
        </summary>
        <pre class="dump">{spectra ? pretty(spectra) : 'Not requested or not available.'}</pre>
      </details>

    {/if}
  </div>
</div>

<style>
  .rv {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }

  .rv-header {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 12px;
    border-bottom: 1px solid var(--color-border);
    flex-shrink: 0;
    background: var(--color-bg-panel);
  }

  .rv-title {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--color-accent-hi);
    flex-shrink: 0;
  }

  .rv-job-id {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-subtext);
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .reload-btn {
    font-size: 10px;
    font-family: var(--font-mono);
    padding: 2px 8px;
    border-radius: 4px;
    border: 1px solid var(--color-border);
    background: var(--color-bg-raised);
    color: var(--color-subtext);
    cursor: pointer;
    flex-shrink: 0;
  }
  .reload-btn:hover:not(:disabled) { color: var(--color-text); border-color: var(--color-accent); }
  .reload-btn:disabled { opacity: 0.35; cursor: default; }

  .rv-body {
    flex: 1;
    overflow-y: auto;
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .hint {
    font-size: 12px;
    color: var(--color-subtext);
    text-align: center;
    padding: 40px 16px;
    opacity: 0.7;
  }
  .hint.error { color: #f87171; opacity: 1; }

  /* Collapsible blocks */
  .block {
    border: 1px solid var(--color-border);
    border-radius: 4px;
    overflow: hidden;
  }

  .block-title {
    display: flex;
    align-items: baseline;
    gap: 10px;
    padding: 6px 10px;
    background: var(--color-bg-raised);
    font-size: 10px;
    font-weight: 600;
    font-family: var(--font-mono);
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: var(--color-subtext);
    cursor: pointer;
    user-select: none;
    list-style: none;
  }
  .block-title::-webkit-details-marker { display: none; }
  .block[open] .block-title { color: var(--color-text); border-bottom: 1px solid var(--color-border); }

  .keff-inline {
    font-size: 11px;
    color: var(--color-accent-hi);
    font-weight: 700;
    text-transform: none;
    letter-spacing: 0;
  }

  .count {
    font-size: 9px;
    color: var(--color-subtext);
    font-weight: 400;
    text-transform: none;
    letter-spacing: 0;
    opacity: 0.7;
  }

  .not-available {
    font-size: 9px;
    color: var(--color-subtext);
    font-weight: 400;
    text-transform: none;
    letter-spacing: 0;
    opacity: 0.45;
  }

  .dump {
    margin: 0;
    padding: 10px 12px;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-subtext);
    white-space: pre;
    overflow-x: auto;
    line-height: 1.55;
    background: var(--color-bg-deep);
    max-height: 400px;
    overflow-y: auto;
  }
</style>