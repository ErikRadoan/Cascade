<script lang="ts">
  // ResultsViewer — thin wrapper around JobResultsPage.
  //
  // jobsState.selectedResultJobId is canonical: JobDetails sets it when the
  // user clicks "View results →" on a completed job. This component's only
  // job is to watch that field and mount JobResultsPage with it — all the
  // actual results UI (summary strip, tabs, panels) lives there now.
  //
  // Confirmed: this file never reads `resultsUi.resultsJobId` (the other
  // field JobDetails sets on that same click). That field has no reader
  // anywhere in the results domain — it's dead. Worth deleting the
  // `resultsUi.resultsJobId = jobId;` line in JobDetails and the
  // resultsUi.svelte.ts store entirely, unless something outside these
  // two components turns up reading it.
  import { jobsState } from '../orchestration/stores/jobs.svelte';
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