<script lang="ts">
  import JobPanel from './JobPanel.svelte';
  import JobDetails from './JobDetails.svelte';
</script>

<div class="job-scheduler">
  <aside class="left-panel">
    <JobPanel />
  </aside>

  <section class="details-area">
    <JobDetails />
  </section>
</div>

<style>
  .job-scheduler {
    display: grid;
    grid-template-columns: 300px 1fr;
    height: 100%;
    overflow: hidden;
  }

  .left-panel {
    border-right: 1px solid var(--color-border);
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  .details-area {
    overflow: hidden;
    background: var(--color-bg-deep);
  }
</style>