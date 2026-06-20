<script lang="ts">
  import { ui } from '$lib/stores/index.svelte';
  import GeometryEditor from '$lib/components/geometry/GeometryEditor.svelte';
  import JobScheduler from '$lib/components/orchestration/JobScheduler.svelte';
  import ResultsViewer from '$lib/components/results/ResultsViewer.svelte';
</script>

{#if ui.activeTab === 'geometry'}
  <GeometryEditor />
{:else if ui.activeTab === 'jobs'}
  <JobScheduler />
{:else if ui.activeTab === 'results'}
  <ResultsViewer />
{/if}