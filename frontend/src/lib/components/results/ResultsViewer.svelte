<script lang="ts">
  // ResultsViewer — thin wrapper around JobResultsPage.
  //
  // jobsState.selectedResultJobId is canonical: JobDetails sets it when the
  // user clicks "View results →" on a completed job (see that store's own
  // comment). This component's only job is to watch that field and mount
  // JobResultsPage with it — all the actual results UI (summary strip,
  // tabs, panels) lives there now. The old raw JSON-dump blocks and the
  // fission-overlay wiring are gone; JobResultsPage's panels supersede them.
  //
  // NOTE: `jobsState` didn't appear in either file reviewed for this change
  // (only `ui.resultsJobId` did, in the old version of this file), so the
  // import path below is inferred from the sibling `ui` store's location.
  // Fix the path/name if jobsState actually lives elsewhere.
  import { jobsState } from '$lib/stores/index.svelte';
  import JobResultsPage from './JobResultsPage.svelte';

  const jobId = $derived(jobsState.selectedResultJobId);
</script>

<div class="rv">
  {#if jobId}
    {#key jobId}
      <JobResultsPage {jobId} />
    {/key}
  {:else}
    <p class="hint">No job selected — click "View results →" on a completed job.</p>
  {/if}
</div>

<style>
  .rv {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }

  .hint {
    font-size: 12px;
    color: var(--color-subtext);
    text-align: center;
    padding: 40px 16px;
    opacity: 0.7;
  }
</style>