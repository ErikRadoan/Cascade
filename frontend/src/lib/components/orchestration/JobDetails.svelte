<script lang="ts">
  import { jobsState, refreshJobs } from '$lib/stores/index.svelte';
  import { onMount, onDestroy } from 'svelte';
  import * as api from '$lib/api';
  import type { JobDetail } from '$lib/types';

  let detail     = $state<JobDetail | null>(null);
  let log        = $state<string>('');
  let loading    = $state(false);
  let cancelling = $state(false);
  let deleting   = $state(false);
  let error      = $state<string | null>(null);
  let logInterval: ReturnType<typeof setInterval> | null = null;

  $effect(() => {
    const id = jobsState.selectedJobId;
    if (id) {
      loadDetail(id);
    } else {
      detail = null;
      log = '';
    }
    return () => stopLogPolling();
  });

  async function loadDetail(id: string) {
    loading = true;
    error   = null;
    log     = '';
    try {
      detail = await api.jobs.get(id);
      // Always do an initial log fetch regardless of status
      await fetchLog(id);
      if (detail.status === 'running' || detail.status === 'queued') {
        startLogPolling(id);
      }
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to load job.';
    } finally {
      loading = false;
    }
  }

  // Fetch the full raw stdout for a job and replace log state.
  // Primary: api.jobs.log (raw stdout string).
  // Fallback: api.results.get — show all fields unfiltered.
  async function fetchLog(id: string) {
  try {
    const res = await api.jobs.stdout(id);
    log = res.available ? res.lines : '';
  } catch {
    try {
      const res = await api.results.get(id);
      log = Object.entries(res)
        .map(([k, v]) => `${k} = ${v}`)
        .join('\n');
    } catch {
      // results not yet available
    }
  }
}

  function startLogPolling(id: string) {
    stopLogPolling();
    logInterval = setInterval(async () => {
      try {
        const updated = await api.jobs.get(id);

        if (detail) {
          detail.status      = updated.status;
          detail.finished_at = updated.finished_at;
          detail.error       = updated.error;
        }

        const listEntry = jobsState.list.find(j => j.id === id);
        if (listEntry) listEntry.status = updated.status;

        // Re-fetch full log every tick so new lines appear
        await fetchLog(id);

        if (updated.status !== 'running' && updated.status !== 'queued') {
          stopLogPolling();
        }
      } catch { /* ignore transient errors */ }
    }, 3000);
  }

  function stopLogPolling() {
    if (logInterval) { clearInterval(logInterval); logInterval = null; }
  }

  async function cancelJob() {
    if (!detail) return;
    cancelling = true;
    try {
      await api.jobs.cancel(detail.id);
      detail.status = 'cancelled';
      stopLogPolling();
      await refreshJobs();
    } catch (e) {
      error = e instanceof Error ? e.message : 'Cancel failed.';
    } finally {
      cancelling = false;
    }
  }

  async function deleteJob() {
    if (!detail) return;
    if (!confirm(`Delete job ${detail.id.slice(0, 8)}…? This cannot be undone.`)) return;
    deleting = true;
    try {
      await api.jobs.delete(detail.id);
      jobsState.selectedJobId = null;
      await refreshJobs();
    } catch (e) {
      error = e instanceof Error ? e.message : 'Delete failed.';
    } finally {
      deleting = false;
    }
  }

  function statusColor(s: string): string {
    switch (s) {
      case 'running':   return '#3b82f6';
      case 'completed': return '#22c55e';
      case 'failed':    return '#ef4444';
      case 'cancelled': return '#6b7280';
      default:          return '#f59e0b';
    }
  }

  function formatDate(iso: string | null): string {
    if (!iso) return '—';
    return new Date(iso).toLocaleString();
  }

  function elapsedSeconds(start: string | null, end: string | null): string {
    if (!start) return '—';
    const ms = new Date(end ?? Date.now()).getTime() - new Date(start).getTime();
    const s  = Math.floor(ms / 1000);
    if (s < 60) return `${s}s`;
    return `${Math.floor(s / 60)}m ${s % 60}s`;
  }

  onDestroy(() => stopLogPolling());
</script>

<div class="panel">

  <!-- Panel header -->
  <div class="panel-header">
    <span class="panel-title">Job Details</span>
    {#if detail}
      <div class="panel-actions">
        {#if detail.status === 'running' || detail.status === 'queued'}
          <button class="icon-text-btn warning" disabled={cancelling} onclick={cancelJob}>
            {cancelling ? 'Cancelling…' : 'Cancel'}
          </button>
        {/if}
        {#if detail.status !== 'running' && detail.status !== 'queued'}
          <button class="icon-text-btn danger" disabled={deleting} onclick={deleteJob}>
            {deleting ? 'Deleting…' : 'Delete'}
          </button>
        {/if}
        {#if detail.status === 'completed'}
          <a class="icon-text-btn" href={api.results.downloadUrl(detail.id)} download="statepoint.h5">
            ↓ Statepoint
          </a>
        {/if}
      </div>
    {/if}
  </div>

  <!-- Body -->
  <div class="panel-body">

    {#if !jobsState.selectedJobId}
      <p class="empty-hint">Select a job to view its details.</p>

    {:else if loading}
      <p class="empty-hint">Loading…</p>

    {:else if error}
      <p class="empty-hint error">{error}</p>

    {:else if detail}
      <div class="columns">

        <!-- Left column: identity + meta -->
        <div class="col">

          <!-- Info window -->
          <div class="win">
            <div class="win-header">
              <span class="win-title">Info</span>
            </div>
            <div class="win-body">
              <div class="selected-header">
                <span class="selected-name">{detail.notes ?? detail.id.slice(0, 16) + '…'}</span>
                <span class="selected-type" style="color: {statusColor(detail.status)}">{detail.status}</span>
              </div>
              <div class="field-list">
                <div class="field-row">
                  <span class="field-label">id</span>
                  <span class="field-value mono truncate">{detail.id}</span>
                </div>
                <div class="field-row">
                  <span class="field-label">backend</span>
                  <span class="field-value mono">{detail.backend}</span>
                </div>
                <div class="field-row">
                  <span class="field-label">started</span>
                  <span class="field-value">{formatDate(detail.started_at)}</span>
                </div>
                <div class="field-row">
                  <span class="field-label">finished</span>
                  <span class="field-value">
                    {formatDate(detail.finished_at)}
                    {#if detail.started_at}
                      <span class="field-sub">({elapsedSeconds(detail.started_at, detail.finished_at)})</span>
                    {/if}
                  </span>
                </div>
              </div>
            </div>
          </div>

          <!-- Sweep params window (conditional) -->
          {#if Object.keys(detail.param_values).length > 0}
            <div class="win">
              <div class="win-header">
                <span class="win-title">Sweep parameters</span>
              </div>
              <div class="win-body">
                <div class="field-list no-mb">
                  {#each Object.entries(detail.param_values) as [k, v]}
                    <div class="field-row">
                      <span class="field-label">{k}</span>
                      <span class="field-value mono accent">{v}</span>
                    </div>
                  {/each}
                </div>
              </div>
            </div>
          {/if}

        </div>

        <!-- Right column: output + results -->
        <div class="col">

          <!-- Output window -->
          <div class="win output-win">
            <div class="win-header">
              <span class="win-title">Output</span>
              {#if detail.status === 'running'}
                <span class="live-badge">● live</span>
              {/if}
            </div>
            <div class="win-body log-body">
              <pre class="log">
{#if detail.error}ERROR: {detail.error}
{:else if log}{log}
{:else if detail.status === 'running' || detail.status === 'queued'}Waiting for output…
{:else}No output available.{/if}</pre>
            </div>
          </div>

          <!-- Results window -->
          <div class="win">
            <div class="win-header">
              <span class="win-title">Results</span>
            </div>
            <div class="win-body results-body">
              {#if detail.status === 'completed'}
                <button class="icon-text-btn accent" onclick={() => console.log('TODO: navigate to results', detail?.id)}>
                  View results →
                </button>
              {:else if detail.status === 'failed'}
                <span class="empty-hint error">Job failed — no results available.</span>
              {:else}
                <span class="empty-hint">Available after completion.</span>
              {/if}
            </div>
          </div>

        </div>
      </div>
    {/if}
  </div>
</div>

<style>
  .panel {
    display: flex;
    flex-direction: column;
    height: 100%;
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 10px;
    border-bottom: 1px solid var(--color-border);
    flex-shrink: 0;
    gap: 6px;
  }

  .panel-title {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--color-accent-hi);
  }

  .panel-actions {
    display: flex;
    gap: 4px;
    align-items: center;
  }

  .panel-body {
    flex: 1;
    overflow-y: auto;
    padding: 10px;
  }

  /* Empty / loading */
  .empty-hint {
    font-size: 11px;
    color: var(--color-subtext);
    text-align: center;
    padding: 24px 8px;
    line-height: 1.6;
    opacity: 0.7;
  }
  .empty-hint.error { color: #f87171; opacity: 1; }

  /* Two-column layout */
  .columns {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    height: 100%;
    min-height: 0;
  }

  .col {
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-width: 0;
    min-height: 0;
  }

  /* Window sub-panels — bordered, sharp corners, titlebar */
  .win {
    display: flex;
    flex-direction: column;
    border: 1px solid var(--color-border);
    overflow: hidden;
    min-height: 0;
  }

  .win-header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 8px;
    border-bottom: 1px solid var(--color-border);
    background: var(--color-bg-raised);
    flex-shrink: 0;
  }

  .win-title {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: var(--color-subtext);
  }

  .win-body {
    padding: 10px;
    flex: 1;
    min-height: 0;
  }

  /* Output window stretches to fill remaining space in its column */
  .output-win {
    flex: 1;
    min-height: 0;
    height: 0;
  }

  .log-body {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
    overflow: hidden;
    padding: 0;
  }

  /* Identity header */
  .selected-header {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--color-border);
  }

  .selected-name {
    font-family: var(--font-mono);
    font-size: 13px;
    color: var(--color-text);
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .selected-type {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }

  /* Field rows */
  .field-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-bottom: 0;
  }

  .field-list.no-mb { margin-bottom: 0; }

  .field-row {
    display: flex;
    align-items: baseline;
    gap: 8px;
  }

  .field-label {
    font-size: 11px;
    color: var(--color-subtext);
    font-family: var(--font-mono);
    flex-shrink: 0;
    white-space: nowrap;
  }

  .field-value {
    font-size: 12px;
    color: var(--color-text);
    display: flex;
    align-items: baseline;
    gap: 5px;
    flex: 1;
    min-width: 0;
  }

  .field-value.mono { font-family: var(--font-mono); }
  .field-value.truncate { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .field-value.accent { color: var(--color-accent-hi); }

  .field-sub {
    font-size: 10px;
    color: var(--color-subtext);
    opacity: 0.7;
  }

  /* Live badge */
  .live-badge {
    font-size: 9px;
    color: var(--color-accent);
    animation: pulse 1.5s ease-in-out infinite;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.35; }
  }

  /* Output log */
  .log {
    margin: 0;
    padding: 10px;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-subtext);
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.6;

    flex: 1;
    min-height: 0;

    overflow-y: auto;
  }

  /* Results body */
  .results-body {
    display: flex;
    align-items: center;
  }

  /* Buttons */
  .icon-text-btn {
    font-size: 10px;
    font-family: var(--font-mono);
    padding: 3px 8px;
    border-radius: 4px;
    border: 1px solid var(--color-border);
    background: var(--color-bg-raised);
    color: var(--color-subtext);
    cursor: pointer;
    text-decoration: none;
    display: inline-block;
    line-height: 1.5;
  }
  .icon-text-btn:hover:not(:disabled) { color: var(--color-text); border-color: var(--color-accent); }
  .icon-text-btn:disabled { opacity: 0.35; cursor: default; }
  .icon-text-btn.warning { border-color: #f59e0b; color: #f59e0b; }
  .icon-text-btn.warning:hover { background: rgba(245,158,11,0.08); }
  .icon-text-btn.danger { border-color: #ef4444; color: #f87171; }
  .icon-text-btn.danger:hover { background: rgba(239,68,68,0.08); }
  .icon-text-btn.accent { border-color: var(--color-accent); color: var(--color-accent-hi); }
  .icon-text-btn.accent:hover { background: rgba(6, 182, 212, 0.08); }
</style>