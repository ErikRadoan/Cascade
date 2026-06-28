<script lang="ts">
  import { jobsState, startJobsPolling, stopJobsPolling } from '$lib/stores/index.svelte';
  import { onMount } from 'svelte';
  import type { JobSummary } from '$lib/types';

  interface Props {
    onNewJob:         () => void;
    onManageBackends: () => void;
  }

  let { onNewJob, onManageBackends }: Props = $props();

  type Filter = 'all' | 'running' | 'completed' | 'failed';
  let filter = $state<Filter>('all');

  let filtered = $derived(
    filter === 'all'
      ? jobsState.list
      : jobsState.list.filter(j => j.status === filter)
  );

  let counts = $derived({
    all:       jobsState.list.length,
    running:   jobsState.list.filter(j => j.status === 'running' || j.status === 'queued').length,
    completed: jobsState.list.filter(j => j.status === 'completed').length,
    failed:    jobsState.list.filter(j => j.status === 'failed').length,
  });

  function select(job: JobSummary) {
    jobsState.selectedJobId = job.id;
  }

  function statusColor(s: string): string {
    switch (s) {
      case 'running':
      case 'queued':    return '#3b82f6';
      case 'completed': return '#22c55e';
      case 'failed':    return '#ef4444';
      case 'cancelled': return '#6b7280';
      default:          return '#f59e0b';
    }
  }

  function relativeTime(iso: string): string {
    const diff = Date.now() - new Date(iso).getTime();
    const m = Math.floor(diff / 60000);
    if (m < 1)  return 'just now';
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
  }

  function isSweep(job: JobSummary): boolean {
    return Object.keys(job.param_values ?? {}).length > 0;
  }

  onMount(() => {
    startJobsPolling();
    return () => stopJobsPolling();
  });
</script>

<div class="panel">

  <div class="panel-header">
    <span class="panel-title">Jobs</span>
    <div class="header-actions">
      <button class="icon-btn" title="Manage backend profiles" onclick={onManageBackends}>
        <svg viewBox="0 0 16 16" fill="currentColor">
          <path d="M1.75 2h12.5A1.75 1.75 0 0116 3.75v2.5A1.75 1.75 0 0114.25 8H1.75A1.75 1.75 0 010 6.25v-2.5A1.75 1.75 0 011.75 2zM1.5 3.75v2.5c0 .138.112.25.25.25h12.5a.25.25 0 00.25-.25v-2.5a.25.25 0 00-.25-.25H1.75a.25.25 0 00-.25.25zM1.75 9h12.5A1.75 1.75 0 0116 10.75v2.5A1.75 1.75 0 0114.25 15H1.75A1.75 1.75 0 010 13.25v-2.5A1.75 1.75 0 011.75 9zm-.25 1.75v2.5c0 .138.112.25.25.25h12.5a.25.25 0 00.25-.25v-2.5a.25.25 0 00-.25-.25H1.75a.25.25 0 00-.25.25z"/>
          <circle cx="12.5" cy="5" r="1"/>
          <circle cx="12.5" cy="12" r="1"/>
        </svg>
        Backends
      </button>
      <button class="new-btn" onclick={onNewJob}>
        <svg viewBox="0 0 16 16" fill="currentColor">
          <path d="M8 2a.75.75 0 01.75.75v4.5h4.5a.75.75 0 010 1.5h-4.5v4.5a.75.75 0 01-1.5 0v-4.5h-4.5a.75.75 0 010-1.5h4.5v-4.5A.75.75 0 018 2z"/>
        </svg>
        New job
      </button>
    </div>
  </div>

  <div class="filter-row">
    {#each (['all', 'running', 'completed', 'failed'] as Filter[]) as f}
      <button class="filter-btn" class:active={filter === f} onclick={() => filter = f}>
        {f}
        {#if counts[f] > 0}
          <span class="filter-count">{counts[f]}</span>
        {/if}
      </button>
    {/each}
  </div>

  <div class="list-body">
    {#if jobsState.isLoading && jobsState.list.length === 0}
      <p class="hint">Loading…</p>
    {:else if jobsState.error}
      <p class="hint error">{jobsState.error}</p>
    {:else if filtered.length === 0}
      <p class="hint">
        {filter === 'all' ? 'No jobs yet.' : `No ${filter} jobs.`}
      </p>
    {:else}
      {#each filtered as job (job.id)}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div
          class="job-card"
          class:selected={jobsState.selectedJobId === job.id}
          onclick={() => select(job)}
        >
          <div class="status-strip" style="background: {statusColor(job.status)}"></div>
          <div class="card-body">
            <div class="card-top">
              <span class="card-title">
                {job.notes ?? job.id.slice(0, 12) + '…'}
              </span>
              <div class="card-badges">
                {#if isSweep(job)}
                  <span class="badge sweep">sweep</span>
                {/if}
                <span class="badge status" style="color: {statusColor(job.status)}">
                  {job.status}
                </span>
              </div>
            </div>
            <div class="card-meta">
              <span class="meta-item">{job.backend}</span>
              {#if isSweep(job)}
                <span class="meta-item sweep-params">
                  {Object.entries(job.param_values).map(([k, v]) =>
                    `${k.split('.').pop()}=${v}`).join(', ')}
                </span>
              {/if}
              <span class="meta-item time">{relativeTime(job.created_at)}</span>
            </div>
          </div>
        </div>
      {/each}
    {/if}
  </div>

</div>

<style>
  .panel {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
    background: var(--color-bg-panel);
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    border-bottom: 1px solid var(--color-border);
    flex-shrink: 0;
  }

  .panel-title {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--color-subtext);
  }

  .header-actions { display: flex; align-items: center; gap: 6px; }

  .icon-btn {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 11px;
    font-weight: 500;
    color: var(--color-subtext);
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    padding: 4px 9px;
    border-radius: 6px;
    cursor: pointer;
    transition: border-color 0.1s, color 0.1s;
  }
  .icon-btn svg { width: 12px; height: 12px; flex-shrink: 0; }
  .icon-btn:hover { border-color: var(--color-accent); color: var(--color-accent-hi); }

  .new-btn {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 12px;
    font-weight: 500;
    color: var(--color-subtext);
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    padding: 4px 10px;
    border-radius: 6px;
    cursor: pointer;
    transition: border-color 0.1s, color 0.1s;
  }
  .new-btn svg { width: 13px; height: 13px; }
  .new-btn:hover { border-color: var(--color-accent); color: var(--color-accent-hi); }

  .filter-row {
    display: flex;
    border-bottom: 1px solid var(--color-border);
    flex-shrink: 0;
    padding: 0 8px;
  }

  .filter-btn {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: var(--color-subtext);
    padding: 6px 8px;
    cursor: pointer;
    margin-bottom: -1px;
    transition: color 0.1s;
  }
  .filter-btn:hover { color: var(--color-text); }
  .filter-btn.active { color: var(--color-accent-hi); border-bottom-color: var(--color-accent); }

  .filter-count {
    font-size: 9px;
    background: var(--color-bg-raised);
    color: var(--color-subtext);
    border-radius: 8px;
    padding: 0 5px;
    min-width: 16px;
    text-align: center;
  }
  .filter-btn.active .filter-count {
    background: rgba(6,182,212,0.15);
    color: var(--color-accent-hi);
  }

  .list-body {
    flex: 1;
    overflow-y: auto;
    padding: 6px;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  .hint {
    font-size: 11px;
    color: var(--color-subtext);
    text-align: center;
    padding: 24px 12px;
    opacity: 0.7;
  }
  .hint.error { color: #f87171; opacity: 1; }

  .job-card {
    display: flex;
    border-radius: 6px;
    border: 1px solid transparent;
    background: var(--color-bg-raised);
    cursor: pointer;
    overflow: hidden;
    transition: border-color 0.1s;
  }
  .job-card:hover { border-color: var(--color-border); }
  .job-card.selected { border-color: var(--color-accent); background: rgba(6,182,212,0.05); }

  .status-strip { width: 3px; flex-shrink: 0; opacity: 0.85; }

  .card-body {
    flex: 1;
    padding: 8px 10px;
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }

  .card-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
  }

  .card-title {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
  }

  .card-badges { display: flex; gap: 4px; flex-shrink: 0; }

  .badge {
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    padding: 1px 5px;
    border-radius: 4px;
  }
  .badge.sweep { background: rgba(139,92,246,0.15); color: #a78bfa; }
  .badge.status { background: transparent; }

  .card-meta {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 10px;
    color: var(--color-subtext);
    overflow: hidden;
  }

  .meta-item { flex-shrink: 0; }
  .meta-item.sweep-params {
    flex: 1;
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-accent);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .meta-item.time { margin-left: auto; flex-shrink: 0; }
</style>