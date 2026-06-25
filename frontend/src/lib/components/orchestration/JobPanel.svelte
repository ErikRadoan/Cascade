<script lang="ts">
  import { jobsState, startJobsPolling, stopJobsPolling, activeProject } from '$lib/stores/index.svelte';
  import { ui } from '$lib/stores/index.svelte';
  import { onMount } from 'svelte';
  import * as api from '$lib/api';
  import type { JobSummary } from '$lib/types';

  // ---------------------------------------------------------------------------
  // Run types and their per-mode settings
  // ---------------------------------------------------------------------------

  type RunMode = 'eigenvalue' | 'fixed_source' | 'depletion' | 'r2s';

  interface RunModeConfig {
    label:       string;
    description: string;
    needsInactive: boolean;
  }

  const RUN_MODES: Record<RunMode, RunModeConfig> = {
    eigenvalue: {
      label:         'Eigenvalue (k-eff)',
      description:   'Criticality calculation. Returns k-effective.',
      needsInactive: true,
    },
    fixed_source: {
      label:         'Fixed Source',
      description:   'Fixed neutron source transport. No k-eff.',
      needsInactive: false,
    },
    depletion: {
      label:         'Depletion / Burnup',
      description:   'Time-dependent burnup using a depletion chain.',
      needsInactive: true,
    },
    r2s: {
      label:         'R2S (activation)',
      description:   'Rigorous Two-Step activation analysis.',
      needsInactive: false,
    },
  };

  // ---------------------------------------------------------------------------
  // Submit drawer state
  // ---------------------------------------------------------------------------

  let showDrawer    = $state(false);
  let runMode       = $state<RunMode>('eigenvalue');
  let particles     = $state(1000);
  let inactive      = $state(20);
  let batches       = $state(100);
  let seed          = $state(1);
  let notes         = $state('');
  // Depletion-specific
  let power_W       = $state(1000.0);
  let timesteps     = $state('10, 20, 30');   // comma-separated days
  // R2S-specific
  let neutronSrcFile = $state('');
  // Backend
  let backendType   = $state<'docker' | 'local' | 'slurm'>('docker');

  let submitting    = $state(false);
  let submitError   = $state<string | null>(null);

  // ---------------------------------------------------------------------------
  // Filter
  // ---------------------------------------------------------------------------

  type Filter = 'all' | 'running' | 'completed' | 'failed';
  let filter = $state<Filter>('all');

  let filtered = $derived(
    filter === 'all'
      ? jobsState.list
      : jobsState.list.filter(j => j.status === filter)
  );

  // Counts per status for filter tab badges
  let counts = $derived({
    all:       jobsState.list.length,
    running:   jobsState.list.filter(j => j.status === 'running' || j.status === 'queued').length,
    completed: jobsState.list.filter(j => j.status === 'completed').length,
    failed:    jobsState.list.filter(j => j.status === 'failed').length,
  });

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  function select(job: JobSummary) {
    jobsState.selectedJobId = job.id;
  }

  function extractMaterialIds(text: string): string[] {
    const matches = text.match(
      /(?:^|\s)(?:material|pellet_material|gap_material|clad_material|fill_material):\s*([A-Za-z0-9_]+)/gm
    ) ?? [];
    const ids = new Set<string>();
    for (const m of matches) {
      const val = m.split(':')[1]?.trim();
      if (val && val !== 'null') ids.add(val);
    }
    return [...ids];
  }

  async function submitJob() {
    submitError = null;
    submitting  = true;

    const project = activeProject();
    if (!project.isValid) {
      submitError = 'Active geometry has validation errors. Fix them first.';
      submitting = false;
      return;
    }

    const materialIds = extractMaterialIds(project.text);
    if (materialIds.length === 0) {
      submitError = 'No material IDs found in geometry. Check your YAML.';
      submitting = false;
      return;
    }

    try {
      const body: Record<string, unknown> = {
        geometry_text: project.text,
        material_ids:  materialIds,
        particles,
        batches,
        seed,
        notes:         notes || undefined,
        run_mode:      runMode,
        backend_config: { type: backendType },
      };

      if (RUN_MODES[runMode].needsInactive) body.inactive = inactive;

      if (runMode === 'depletion') {
        body.power_W   = power_W;
        body.timesteps = timesteps.split(',').map(s => parseFloat(s.trim())).filter(n => !isNaN(n));
      }

      if (runMode === 'r2s') {
        body.neutron_source_file = neutronSrcFile;
      }

      const result = await api.jobs.submit(body as Parameters<typeof api.jobs.submit>[0]);

      // Handle both single job and sweep response
      if ('sweep_id' in result) {
        // Sweep — add all child jobs
        jobsState.list.unshift(...result.jobs);
        jobsState.selectedJobId = result.jobs[0]?.id ?? null;
      } else {
        jobsState.list.unshift(result);
        jobsState.selectedJobId = result.id;
      }

      showDrawer = false;
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      // Surface the FastAPI detail if available
      try {
        const parsed = JSON.parse(msg.replace(/^API \d+: /, ''));
        submitError = Array.isArray(parsed) ? parsed[0]?.msg ?? msg : parsed.detail ?? msg;
      } catch {
        submitError = msg;
      }
    } finally {
      submitting = false;
    }
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

  // Is this a sweep job (has param_values)?
  function isSweep(job: JobSummary): boolean {
    return Object.keys(job.param_values ?? {}).length > 0;
  }

  onMount(() => {
    startJobsPolling();
    return () => stopJobsPolling();
  });
</script>

<div class="panel">

  <!-- ── Header ── -->
  <div class="panel-header">
    <span class="panel-title">Jobs</span>
    <button
      class="new-btn"
      class:active={showDrawer}
      onclick={() => { showDrawer = !showDrawer; submitError = null; }}
    >
      {#if showDrawer}
        <svg viewBox="0 0 16 16" fill="currentColor"><path d="M3.72 3.72a.75.75 0 011.06 0L8 6.94l3.22-3.22a.75.75 0 111.06 1.06L9.06 8l3.22 3.22a.75.75 0 11-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 01-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 010-1.06z"/></svg>
      {:else}
        <svg viewBox="0 0 16 16" fill="currentColor"><path d="M8 2a.75.75 0 01.75.75v4.5h4.5a.75.75 0 010 1.5h-4.5v4.5a.75.75 0 01-1.5 0v-4.5h-4.5a.75.75 0 010-1.5h4.5v-4.5A.75.75 0 018 2z"/></svg>
      {/if}
      New job
    </button>
  </div>

  <!-- ── Submit drawer ── -->
  {#if showDrawer}
    <div class="drawer">
      <div class="drawer-section">
        <div class="drawer-section-title">Run type</div>
        <div class="run-mode-grid">
          {#each Object.entries(RUN_MODES) as [mode, cfg]}
            <button
              class="run-mode-btn"
              class:active={runMode === mode}
              onclick={() => runMode = mode as RunMode}
            >
              <span class="run-mode-label">{cfg.label}</span>
              <span class="run-mode-desc">{cfg.description}</span>
            </button>
          {/each}
        </div>
      </div>

      <div class="drawer-section">
        <div class="drawer-section-title">Monte Carlo settings</div>
        <div class="settings-grid">
          <label class="field">
            <span>Particles / batch</span>
            <input type="number" min="1" bind:value={particles} />
          </label>
          {#if RUN_MODES[runMode].needsInactive}
            <label class="field">
              <span>Inactive batches</span>
              <input type="number" min="1" bind:value={inactive} />
            </label>
          {/if}
          <label class="field">
            <span>Total batches</span>
            <input type="number" min="2" bind:value={batches} />
          </label>
          <label class="field">
            <span>Random seed</span>
            <input type="number" min="1" bind:value={seed} />
          </label>
        </div>
      </div>

      <!-- Depletion-specific settings -->
      {#if runMode === 'depletion'}
        <div class="drawer-section">
          <div class="drawer-section-title">Depletion settings</div>
          <div class="settings-grid">
            <label class="field">
              <span>Power (W)</span>
              <input type="number" min="0" step="any" bind:value={power_W} />
            </label>
            <label class="field full">
              <span>Timesteps (days, comma-separated)</span>
              <input type="text" placeholder="10, 30, 60, 90" bind:value={timesteps} />
            </label>
          </div>
          <p class="drawer-note">⚠ Depletion requires a depletion chain file configured in the backend.</p>
        </div>
      {/if}

      <!-- R2S-specific settings -->
      {#if runMode === 'r2s'}
        <div class="drawer-section">
          <div class="drawer-section-title">R2S settings</div>
          <div class="settings-grid">
            <label class="field full">
              <span>Neutron source file path (on host)</span>
              <input type="text" placeholder="/path/to/source.h5" bind:value={neutronSrcFile} />
            </label>
          </div>
          <p class="drawer-note">⚠ R2S requires additional backend configuration. See documentation.</p>
        </div>
      {/if}

      <div class="drawer-section">
        <div class="drawer-section-title">Execution backend</div>
        <div class="backend-row">
          {#each ['docker', 'local', 'slurm'] as bt}
            <button
              class="backend-btn"
              class:active={backendType === bt}
              onclick={() => backendType = bt as typeof backendType}
            >{bt}</button>
          {/each}
        </div>
      </div>

      <div class="drawer-section">
        <label class="field full">
          <span>Notes (optional)</span>
          <input type="text" placeholder="e.g. baseline run, pin cell sweep…" bind:value={notes} />
        </label>
      </div>

      {#if submitError}
        <div class="submit-error">{submitError}</div>
      {/if}

      <button class="run-btn" disabled={submitting} onclick={submitJob}>
        {#if submitting}
          <span class="spinner"></span> Submitting…
        {:else}
          ▶ Run {RUN_MODES[runMode].label}
        {/if}
      </button>
    </div>
  {/if}

  <!-- ── Filter tabs ── -->
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

  <!-- ── Job list ── -->
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
          <!-- Status strip on left edge -->
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

  /* ── Header ── */
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
  .new-btn.active { border-color: var(--color-accent); color: var(--color-accent-hi); background: rgba(6,182,212,0.08); }

  /* ── Drawer ── */
  .drawer {
    border-bottom: 1px solid var(--color-border);
    overflow-y: auto;
    max-height: 65vh;
    display: flex;
    flex-direction: column;
    gap: 0;
    flex-shrink: 0;
  }

  .drawer-section {
    padding: 10px 12px;
    border-bottom: 1px solid rgba(51,65,85,0.4);
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .drawer-section-title {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: var(--color-accent-hi);
    font-weight: 600;
  }

  /* Run mode grid */
  .run-mode-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
  }

  .run-mode-btn {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 8px 10px;
    border-radius: 6px;
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    cursor: pointer;
    text-align: left;
    transition: border-color 0.1s;
  }
  .run-mode-btn:hover { border-color: var(--color-border); background: #334155; }
  .run-mode-btn.active {
    border-color: var(--color-accent);
    background: rgba(6,182,212,0.08);
  }

  .run-mode-label {
    font-size: 11px;
    font-weight: 600;
    color: var(--color-text);
    font-family: var(--font-mono);
  }

  .run-mode-btn.active .run-mode-label { color: var(--color-accent-hi); }

  .run-mode-desc {
    font-size: 9px;
    color: var(--color-subtext);
    line-height: 1.4;
  }

  /* Settings grid */
  .settings-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: 3px;
    font-size: 10px;
    color: var(--color-subtext);
  }

  .field.full { grid-column: 1 / -1; }

  .field input {
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    border-radius: 4px;
    color: var(--color-text);
    font-family: var(--font-mono);
    font-size: 11px;
    padding: 5px 7px;
  }
  .field input:focus { outline: none; border-color: var(--color-accent); }

  .drawer-note {
    font-size: 10px;
    color: #f59e0b;
    line-height: 1.4;
  }

  /* Backend buttons */
  .backend-row { display: flex; gap: 6px; }

  .backend-btn {
    flex: 1;
    padding: 5px;
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    border-radius: 5px;
    color: var(--color-subtext);
    font-family: var(--font-mono);
    font-size: 11px;
    cursor: pointer;
    text-align: center;
    text-transform: lowercase;
  }
  .backend-btn:hover { border-color: var(--color-border); color: var(--color-text); }
  .backend-btn.active { border-color: var(--color-accent); color: var(--color-accent-hi); background: rgba(6,182,212,0.08); }

  .submit-error {
    padding: 0 12px;
    font-size: 11px;
    color: #f87171;
    line-height: 1.4;
  }

  .run-btn {
    margin: 10px 12px;
    background: var(--color-accent);
    color: var(--color-bg-deep);
    border: none;
    padding: 9px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.02em;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
  }
  .run-btn:disabled { opacity: 0.45; cursor: default; }
  .run-btn:hover:not(:disabled) { background: var(--color-accent-hi); }

  .spinner {
    width: 12px;
    height: 12px;
    border: 2px solid rgba(15,23,42,0.3);
    border-top-color: var(--color-bg-deep);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ── Filters ── */
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
  .filter-btn.active .filter-count { background: rgba(6,182,212,0.15); color: var(--color-accent-hi); }

  /* ── Job list ── */
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

  /* Job card */
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

  .status-strip {
    width: 3px;
    flex-shrink: 0;
    opacity: 0.85;
  }

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

  .card-badges {
    display: flex;
    gap: 4px;
    flex-shrink: 0;
  }

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