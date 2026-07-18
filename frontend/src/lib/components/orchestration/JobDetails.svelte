<script lang="ts">
  import { jobsState, refreshJobs } from './stores/jobs.svelte';
  import { ui } from '$lib/stores/ui.svelte';
  import { onDestroy } from 'svelte';
  import { tick } from 'svelte';
  import * as api from '$lib/api';
  import type { JobDetail, TallyResultSet, ResultsConfigSpec } from '$lib/types';

  let detail     = $state<JobDetail | null>(null);
  let log        = $state<string>('');
  let loading    = $state(false);
  let cancelling = $state(false);
  let deleting   = $state(false);
  let error      = $state<string | null>(null);
  let logInterval: ReturnType<typeof setInterval> | null = null;

  // ---------------------------------------------------------------------------
  // Live elapsed-time ticker — only runs while a job is actually running, so
  // the "elapsed" field in the Timing window updates without a full re-fetch.
  // ---------------------------------------------------------------------------
  let nowTick = $state(Date.now());
  let tickInterval: ReturnType<typeof setInterval> | null = null;

  $effect(() => {
    const running = detail?.status === 'running' || detail?.status === 'queued';
    if (running && !tickInterval) {
      tickInterval = setInterval(() => { nowTick = Date.now(); }, 1000);
    } else if (!running && tickInterval) {
      clearInterval(tickInterval);
      tickInterval = null;
    }
  });

  // ---------------------------------------------------------------------------
  // Geometry name resolution — JobDetail only carries geometry_id, so we
  // resolve it against the geometry list once and cache it.
  // ---------------------------------------------------------------------------
  let geometryNames = $state<Record<string, string>>({});

  async function loadGeometryNames() {
    try {
      const list = await api.geometry.list();
      const map: Record<string, string> = {};
      for (const g of list) map[g.id] = g.name;
      geometryNames = map;
    } catch {
      // Non-critical — we just fall back to showing the raw id.
    }
  }

  // ---------------------------------------------------------------------------
  // Quick results summary (k-eff, uncertainty) shown in the Results window
  // without needing to jump into the full results viewer.
  // ---------------------------------------------------------------------------
  let resultsSummary = $state<TallyResultSet | null>(null);
  let resultsLoading = $state(false);

  async function loadResultsSummary(id: string) {
    resultsSummary = null;
    resultsLoading = true;
    try {
      // Correction: api.results.get() doesn't exist on the client — the
      // real method is tallies(), which returns the TallyResultSet
      // (job_id, param_values, tallies, k_effective, k_uncertainty).
      // The previous version of this file called a method that would
      // have thrown on every single load.
      resultsSummary = await api.results.tallies(id);
    } catch {
      // Job may not have a statepoint yet, or the route 404s for some
      // other reason — silently skip, this is a "nice to have" preview.
    } finally {
      resultsLoading = false;
    }
  }

  $effect(() => {
    const id = jobsState.selectedJobId;
    if (id) {
      loadDetail(id);
    } else {
      detail = null;
      log = '';
      resultsSummary = null;
    }
    return () => stopLogPolling();
  });

  async function loadDetail(id: string) {
    loading = true;
    error   = null;
    log     = '';
    resultsSummary = null;
    if (Object.keys(geometryNames).length === 0) loadGeometryNames();
    try {
      detail = await api.jobs.get(id);
      await fetchLog(id);
      if (detail.status === 'running' || detail.status === 'queued') {
        startLogPolling(id);
      } else if (detail.status === 'completed') {
        loadResultsSummary(id);
      }
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to load job.';
    } finally {
      loading = false;
    }
  }

  // Fetch the full raw stdout for a job and replace log state.
  async function fetchLog(id: string) {
    try {
      const res = await api.jobs.stdout(id);
      log = res.available ? res.lines : '';
    } catch {
      // Results not yet available
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

        await fetchLog(id);

        if (updated.status !== 'running' && updated.status !== 'queued') {
          stopLogPolling();
          if (updated.status === 'completed') loadResultsSummary(id);
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

  function elapsedSeconds(start: string | null, end: string | null, now: number): string {
    if (!start) return '—';
    const ms = new Date(end ?? now).getTime() - new Date(start).getTime();
    const s  = Math.max(0, Math.floor(ms / 1000));
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return `${h}h ${m}m`;
  }

  function geometryLabel(id: string | null | undefined): string {
    if (!id) return '—';
    return geometryNames[id] ?? id;
  }

  // ---------------------------------------------------------------------------
  // Job configuration formatting — mirrors JobSubmitModal's RUN_MODES /
  // score labels so the two panels read consistently.
  // ---------------------------------------------------------------------------
  const RUN_MODE_LABELS: Record<string, string> = {
    eigenvalue:   'Eigenvalue (k-eff)',
    fixed_source: 'Fixed Source',
    depletion:    'Depletion / Burnup',
    r2s:          'R2S (activation)',
  };

  function fmtNumList(nums: number[] | undefined): string {
    if (!nums || nums.length === 0) return '—';
    return nums.join(', ');
  }

  function fmtScores(scores: string[] | undefined): string {
    if (!scores || scores.length === 0) return '—';
    return scores.join(', ');
  }

  function meshDims(mesh: { mesh_type: string; nx: number; ny: number; nz: number; nr: number; nz_cyl: number }): string {
    return mesh.mesh_type === 'cylindrical'
      ? `${mesh.nr}×${mesh.nz_cyl} (r×z)`
      : `${mesh.nx}×${mesh.ny}×${mesh.nz}`;
  }

  // File-upload references are stored as "{file_id}/{filename}" — show
  // just the filename (see JobSubmitModal's depChainFile/r2sDecayLibrary
  // comments for why the raw value isn't a bare filename).
  function fileLabel(ref: string | undefined): string {
    if (!ref) return '—';
    const parts = ref.split('/');
    return parts[parts.length - 1] || ref;
  }

  // ---------------------------------------------------------------------------
  // Copy-to-clipboard helper, shared by every "copy" affordance in the panel.
  // ---------------------------------------------------------------------------
  let copiedField = $state<string | null>(null);
  let copiedTimeout: ReturnType<typeof setTimeout> | null = null;

  async function copyText(text: string, field: string) {
    try {
      await navigator.clipboard.writeText(text);
      copiedField = field;
      if (copiedTimeout) clearTimeout(copiedTimeout);
      copiedTimeout = setTimeout(() => { copiedField = null; }, 1400);
    } catch {
      // Clipboard API unavailable — fail silently, nothing user-actionable to do.
    }
  }

  // ---------------------------------------------------------------------------
  // Console (output) — search, wrap, follow-tail, line coloring
  // ---------------------------------------------------------------------------
  let wrapLines   = $state(true);
  let followTail  = $state(true);
  let searchQuery = $state('');
  let currentMatch = $state(0);
  let logContainerEl: HTMLDivElement | undefined = $state();

  let logLines = $derived(log ? log.split('\n') : []);

  let matchIndices = $derived.by(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return [] as number[];
    const idxs: number[] = [];
    logLines.forEach((line, i) => {
      if (line.toLowerCase().includes(q)) idxs.push(i);
    });
    return idxs;
  });

  $effect(() => {
    // Reset match cursor whenever the query (or the underlying log) changes.
    searchQuery;
    logLines;
    currentMatch = 0;
  });

  function lineClass(line: string): string {
    if (/\b(error|traceback|exception|fatal)\b/i.test(line)) return 'line-error';
    if (/\bwarn(ing)?\b/i.test(line)) return 'line-warn';
    return '';
  }

  function highlightSegments(line: string, query: string): { text: string; match: boolean }[] {
    if (!query) return [{ text: line, match: false }];
    const q = query.toLowerCase();
    const lower = line.toLowerCase();
    const segments: { text: string; match: boolean }[] = [];
    let i = 0;
    while (i < line.length) {
      const idx = lower.indexOf(q, i);
      if (idx === -1) {
        segments.push({ text: line.slice(i), match: false });
        break;
      }
      if (idx > i) segments.push({ text: line.slice(i, idx), match: false });
      segments.push({ text: line.slice(idx, idx + q.length), match: true });
      i = idx + q.length;
    }
    return segments;
  }

  async function scrollToMatch(delta: number) {
    if (matchIndices.length === 0) return;
    currentMatch = (currentMatch + delta + matchIndices.length) % matchIndices.length;
    followTail = false;
    await tick();
    const target = logContainerEl?.querySelector(`[data-line-idx="${matchIndices[currentMatch]}"]`);
    target?.scrollIntoView({ block: 'center' });
  }

  function onLogScroll() {
    if (!logContainerEl) return;
    const { scrollTop, scrollHeight, clientHeight } = logContainerEl;
    const nearBottom = scrollHeight - scrollTop - clientHeight < 32;
    followTail = nearBottom;
  }

  function jumpToLatest() {
    followTail = true;
  }

  $effect(() => {
    // Any time the log content changes, auto-scroll if we're following the tail.
    log;
    if (followTail && logContainerEl) {
      tick().then(() => {
        if (logContainerEl) logContainerEl.scrollTop = logContainerEl.scrollHeight;
      });
    }
  });

  function copyLog() {
    if (log) copyText(log, 'log');
  }

  function downloadLog() {
    if (!log || !detail) return;
    const blob = new Blob([log], { type: 'text/plain' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url;
    a.download = `${detail.id.slice(0, 8)}.log`;
    a.click();
    URL.revokeObjectURL(url);
  }

  onDestroy(() => {
    stopLogPolling();
    if (tickInterval) clearInterval(tickInterval);
    if (copiedTimeout) clearTimeout(copiedTimeout);
  });
</script>

{#snippet copyBtn(text: string, field: string, label: string)}
  <button class="copy-btn" title="Copy {label}" onclick={() => copyText(text, field)}>
    {#if copiedField === field}
      <span class="copied-label">✓</span>
    {:else}
      <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.3">
        <rect x="4.5" y="4.5" width="8" height="8" rx="1"/>
        <path d="M2.5 9.5V2.5a1 1 0 011-1H10"/>
      </svg>
    {/if}
  </button>
{/snippet}

{#snippet resultsConfigBlock(rc: ResultsConfigSpec)}
  <div class="field-list no-mb">
    <div class="field-row">
      <span class="field-label">scalars</span>
      <span class="field-value">
        {#if rc.scalars.enabled}
          <span class="mono accent">{fmtScores(rc.scalars.scores)}</span>
          <span class="field-sub">{rc.scalars.all_cells ? 'all cells' : 'selected cells'}</span>
        {:else}
          <span class="field-sub">off</span>
        {/if}
      </span>
    </div>
    <div class="field-row">
      <span class="field-label">mesh</span>
      <span class="field-value">
        {#if rc.mesh.enabled}
          <span class="mono accent">{meshDims(rc.mesh)} {rc.mesh.mesh_type}</span>
          <span class="field-sub">{fmtScores(rc.mesh.scores)}</span>
        {:else}
          <span class="field-sub">off</span>
        {/if}
      </span>
    </div>
    {#if rc.spectra}
      <div class="field-row">
        <span class="field-label">spectra</span>
        <span class="field-value">
          {#if rc.spectra.enabled}
            <span class="mono accent">{rc.spectra.group_structure}-group</span>
            <span class="field-sub">{rc.spectra.per_material ? 'per material' : 'global'}</span>
          {:else}
            <span class="field-sub">off</span>
          {/if}
        </span>
      </div>
    {/if}
    {#if rc.diagnostics}
      <div class="field-row">
        <span class="field-label">diagnostics</span>
        <span class="field-value field-sub">
          {[
            rc.diagnostics.stochastic_volumes ? 'stochastic volumes' : null,
            rc.diagnostics.particle_tracks ? `${rc.diagnostics.n_tracks} tracks` : null,
          ].filter(Boolean).join(', ') || 'none'}
        </span>
      </div>
    {/if}
    {#if rc.apply_dose_conversion != null}
      <div class="field-row">
        <span class="field-label">dose conv.</span>
        <span class="field-value field-sub">{rc.apply_dose_conversion ? 'applied' : 'off'}</span>
      </div>
    {/if}
  </div>
{/snippet}

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

          <!-- Overview window -->
          <div class="win">
            <div class="win-header">
              <span class="win-title">Overview</span>
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
                  {@render copyBtn(detail.id, 'id', 'job id')}
                </div>
                <div class="field-row">
                  <span class="field-label">created</span>
                  <span class="field-value">{formatDate(detail.created_at)}</span>
                </div>
                <div class="field-row">
                  <span class="field-label">backend</span>
                  <span class="field-value mono">{detail.backend}</span>
                </div>
                <div class="field-row">
                  <span class="field-label">geometry</span>
                  <span class="field-value mono truncate">{geometryLabel(detail.geometry_id)}</span>
                  {#if detail.geometry_id}
                    {@render copyBtn(detail.geometry_id, 'geometry', 'geometry id')}
                  {/if}
                </div>
                {#if detail.working_dir}
                  <div class="field-row">
                    <span class="field-label">workdir</span>
                    <span class="field-value mono truncate">{detail.working_dir}</span>
                    {@render copyBtn(detail.working_dir, 'workdir', 'working directory')}
                  </div>
                {/if}
              </div>
            </div>
          </div>

          <!-- Timing window -->
          <div class="win">
            <div class="win-header">
              <span class="win-title">Timing</span>
              {#if detail.status === 'running'}
                <span class="live-badge">● live</span>
              {/if}
            </div>
            <div class="win-body">
              <div class="field-list no-mb">
                <div class="field-row">
                  <span class="field-label">started</span>
                  <span class="field-value">{formatDate(detail.started_at)}</span>
                </div>
                <div class="field-row">
                  <span class="field-label">finished</span>
                  <span class="field-value">{formatDate(detail.finished_at)}</span>
                </div>
                <div class="field-row">
                  <span class="field-label">elapsed</span>
                  <span class="field-value mono accent">
                    {elapsedSeconds(detail.started_at, detail.finished_at, nowTick)}
                  </span>
                </div>
              </div>
            </div>
          </div>

          <!-- Configuration window (conditional — needs backend to echo the
               submitted config back on GET /api/jobs/{id}; see JobDetail's
               config fields in $lib/types) -->
          {#if detail.run_mode}
            <div class="win">
              <div class="win-header">
                <span class="win-title">Configuration</span>
              </div>
              <div class="win-body">
                <div class="field-list no-mb">
                  <div class="field-row">
                    <span class="field-label">mode</span>
                    <span class="field-value mono accent">
                      {RUN_MODE_LABELS[detail.run_mode] ?? detail.run_mode}
                    </span>
                  </div>

                  {#if detail.monte_carlo}
                    <div class="field-row">
                      <span class="field-label">particles</span>
                      <span class="field-value mono">{detail.monte_carlo.particles.toLocaleString()}</span>
                    </div>
                    <div class="field-row">
                      <span class="field-label">batches</span>
                      <span class="field-value mono">
                        {detail.monte_carlo.batches}
                        {#if detail.monte_carlo.inactive != null}
                          <span class="field-sub">({detail.monte_carlo.inactive} inactive)</span>
                        {/if}
                      </span>
                    </div>
                    <div class="field-row">
                      <span class="field-label">seed</span>
                      <span class="field-value mono">{detail.monte_carlo.seed}</span>
                    </div>
                  {/if}

                  {#if detail.source}
                    <div class="field-row">
                      <span class="field-label">source</span>
                      <span class="field-value mono">
                        {detail.source.particle} / {detail.source.space_type}
                        <span class="field-sub">[{fmtNumList(detail.source.space_params)}]</span>
                      </span>
                    </div>
                    {#if detail.source.energy_mev != null}
                      <div class="field-row">
                        <span class="field-label">energy</span>
                        <span class="field-value mono">{detail.source.energy_mev} MeV</span>
                      </div>
                    {/if}
                  {/if}

                  {#if detail.depletion}
                    <div class="field-row">
                      <span class="field-label">power</span>
                      <span class="field-value mono">{detail.depletion.power_W.toLocaleString()} W</span>
                    </div>
                    <div class="field-row">
                      <span class="field-label">timesteps</span>
                      <span class="field-value mono truncate">{fmtNumList(detail.depletion.timesteps)}</span>
                    </div>
                    <div class="field-row">
                      <span class="field-label">chain</span>
                      <span class="field-value mono truncate">{fileLabel(detail.depletion.chain_file)}</span>
                    </div>
                    <div class="field-row">
                      <span class="field-label">integrator</span>
                      <span class="field-value mono">
                        {detail.depletion.integrator}
                        <span class="field-sub">({detail.depletion.substeps} substeps)</span>
                      </span>
                    </div>
                  {/if}

                  {#if detail.material_ids && detail.material_ids.length > 0}
                    <div class="field-row">
                      <span class="field-label">materials</span>
                      <span class="field-value mono truncate">{detail.material_ids.join(', ')}</span>
                    </div>
                  {/if}
                </div>
              </div>
            </div>
          {/if}

          <!-- R2S pipeline window (r2s jobs only) -->
          {#if detail.r2s}
            <div class="win">
              <div class="win-header">
                <span class="win-title">R2S Pipeline</span>
              </div>
              <div class="win-body">
                <div class="config-subheader">Neutron leg</div>
                <div class="field-list no-mb">
                  <div class="field-row">
                    <span class="field-label">source</span>
                    <span class="field-value mono">
                      {detail.r2s.neutron_leg_source.space_type}
                      <span class="field-sub">[{fmtNumList(detail.r2s.neutron_leg_source.space_params)}]</span>
                    </span>
                  </div>
                  <div class="field-row">
                    <span class="field-label">particles</span>
                    <span class="field-value mono">{detail.r2s.neutron_leg_mc.particles.toLocaleString()}</span>
                  </div>
                  <div class="field-row">
                    <span class="field-label">batches</span>
                    <span class="field-value mono">{detail.r2s.neutron_leg_mc.batches}</span>
                  </div>
                  <div class="field-row">
                    <span class="field-label">seed</span>
                    <span class="field-value mono">{detail.r2s.neutron_leg_mc.seed}</span>
                  </div>
                </div>

                <div class="config-subheader">Activation</div>
                <div class="field-list no-mb">
                  <div class="field-row">
                    <span class="field-label">power</span>
                    <span class="field-value mono">{detail.r2s.activation.irradiation_schedule.power_W.toLocaleString()} W</span>
                  </div>
                  <div class="field-row">
                    <span class="field-label">timesteps</span>
                    <span class="field-value mono truncate">{fmtNumList(detail.r2s.activation.irradiation_schedule.timesteps)}</span>
                  </div>
                  <div class="field-row">
                    <span class="field-label">cooling</span>
                    <span class="field-value mono truncate">{fmtNumList(detail.r2s.activation.cooling_times)}</span>
                  </div>
                  <div class="field-row">
                    <span class="field-label">library</span>
                    <span class="field-value mono truncate">{fileLabel(detail.r2s.activation.decay_library)}</span>
                  </div>
                </div>

                <div class="config-subheader">Photon leg</div>
                <div class="field-list no-mb">
                  <div class="field-row">
                    <span class="field-label">particles</span>
                    <span class="field-value mono">{detail.r2s.photon_leg_mc.particles.toLocaleString()}</span>
                  </div>
                  <div class="field-row">
                    <span class="field-label">batches</span>
                    <span class="field-value mono">{detail.r2s.photon_leg_mc.batches}</span>
                  </div>
                  <div class="field-row">
                    <span class="field-label">seed</span>
                    <span class="field-value mono">{detail.r2s.photon_leg_mc.seed}</span>
                  </div>
                  <div class="field-row">
                    <span class="field-label">VR</span>
                    <span class="field-value field-sub">
                      {detail.r2s.photon_leg_vr.weight_windows_enabled ? 'weight windows on' : 'off'}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          {/if}

          <!-- Results capture window (conditional) — what tallies were
               requested at submit time, distinct from the actual computed
               values shown in the Results window on the right. -->
          {#if detail.results_config || detail.r2s_results_config}
            <div class="win">
              <div class="win-header">
                <span class="win-title">Results capture</span>
              </div>
              <div class="win-body">
                {#if detail.results_config}
                  {@render resultsConfigBlock(detail.results_config)}
                {:else if detail.r2s_results_config}
                  <div class="config-subheader">Neutron leg</div>
                  {@render resultsConfigBlock(detail.r2s_results_config.neutron_leg)}
                  <div class="config-subheader">Photon leg</div>
                  {@render resultsConfigBlock(detail.r2s_results_config.photon_leg)}
                {/if}
              </div>
            </div>
          {/if}

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

          <!-- Error window (conditional) -->
          {#if detail.error}
            <div class="win error-win">
              <div class="win-header">
                <span class="win-title">Error</span>
              </div>
              <div class="win-body">
                <div class="error-text">{detail.error}</div>
              </div>
            </div>
          {/if}

        </div>

        <!-- Right column: console + results -->
        <div class="col">

          <!-- Console window -->
          <div class="win output-win">
            <div class="win-header">
              <span class="win-title">Console</span>
              {#if logLines.length > 0}
                <span class="line-count">{logLines.length} lines</span>
              {/if}
              {#if detail.status === 'running'}
                <span class="live-badge">● live</span>
              {/if}
              <div class="console-toolbar">
                <div class="search-box">
                  <input
                    type="text"
                    placeholder="Search…"
                    bind:value={searchQuery}
                    onkeydown={(e) => { if (e.key === 'Enter') scrollToMatch(e.shiftKey ? -1 : 1); }}
                  />
                  {#if searchQuery.trim()}
                    <span class="match-count">
                      {matchIndices.length > 0 ? `${currentMatch + 1}/${matchIndices.length}` : '0/0'}
                    </span>
                    <button class="tool-btn" disabled={matchIndices.length === 0} onclick={() => scrollToMatch(-1)}>↑</button>
                    <button class="tool-btn" disabled={matchIndices.length === 0} onclick={() => scrollToMatch(1)}>↓</button>
                  {/if}
                </div>
                <button class="tool-btn toggle" class:active={wrapLines} title="Wrap lines" onclick={() => wrapLines = !wrapLines}>
                  wrap
                </button>
                <button class="tool-btn toggle" class:active={followTail} title="Follow tail" onclick={() => followTail = !followTail}>
                  follow
                </button>
                <button class="tool-btn" title="Copy console output" disabled={!log} onclick={copyLog}>
                  {copiedField === 'log' ? '✓' : 'copy'}
                </button>
                <button class="tool-btn" title="Download .log" disabled={!log} onclick={downloadLog}>
                  save
                </button>
              </div>
            </div>
            <div class="win-body log-body">
              <div class="log" class:wrap={wrapLines} bind:this={logContainerEl} onscroll={onLogScroll}>
                {#if log}
                  {#each logLines as line, i (i)}
                    <div class="log-line {lineClass(line)}" data-line-idx={i}>
                      <span class="log-line-no">{i + 1}</span>
                      <span class="log-line-text">
                        {#if searchQuery.trim()}
                          {#each highlightSegments(line, searchQuery) as seg}
                            {#if seg.match}<mark>{seg.text}</mark>{:else}{seg.text}{/if}
                          {/each}
                        {:else}
                          {line || ' '}
                        {/if}
                      </span>
                    </div>
                  {/each}
                {:else if detail.status === 'running' || detail.status === 'queued'}
                  <p class="empty-hint">Waiting for output…</p>
                {:else}
                  <p class="empty-hint">No output available.</p>
                {/if}
              </div>
              {#if !followTail && log}
                <button class="jump-latest" onclick={jumpToLatest}>↓ Jump to latest</button>
              {/if}
            </div>
          </div>

          <!-- Results window -->
          <div class="win">
            <div class="win-header">
              <span class="win-title">Results</span>
            </div>
            <div class="win-body results-body">
              {#if detail.status === 'completed'}
                <div class="results-summary">
                  {#if resultsLoading}
                    <span class="empty-hint">Loading summary…</span>
                  {:else if resultsSummary?.k_effective != null}
                    <div class="field-list no-mb">
                      <div class="field-row">
                        <span class="field-label">k-eff</span>
                        <span class="field-value mono accent">
                          {resultsSummary.k_effective.toFixed(5)}
                          {#if resultsSummary.k_uncertainty != null}
                            <span class="field-sub">± {resultsSummary.k_uncertainty.toFixed(5)}</span>
                          {/if}
                        </span>
                      </div>
                    </div>
                  {/if}
                  <button class="icon-text-btn accent" onclick={() => {
                    const jobId = detail?.id ?? null;
                    jobsState.selectedResultJobId = jobId;
                    ui.activeTab = 'results';
                  }}>
                    View results →
                  </button>
                </div>
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
    flex-wrap: nowrap;
  }

  .win-title {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: var(--color-subtext);
    flex-shrink: 0;
  }

  .win-body {
    padding: 10px;
    flex: 1;
    min-height: 0;
  }

  .line-count {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-subtext);
    opacity: 0.6;
    flex-shrink: 0;
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
    position: relative;
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

  /* Standalone versions for spans nested inside a .field-value (e.g. the
     results-capture summary lines, which mix an accent value with a
     .field-sub annotation on the same row). */
  .mono { font-family: var(--font-mono); }
  .accent { color: var(--color-accent-hi); }

  .field-sub {
    font-size: 10px;
    color: var(--color-subtext);
    opacity: 0.7;
  }

  /* Copy button */
  .copy-btn {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 16px;
    height: 16px;
    padding: 0;
    background: transparent;
    border: none;
    color: var(--color-subtext);
    cursor: pointer;
    opacity: 0.5;
  }
  .copy-btn:hover { opacity: 1; color: var(--color-accent-hi); }
  .copy-btn svg { width: 11px; height: 11px; }
  .copied-label { font-size: 10px; color: #22c55e; }

  /* Live badge */
  .live-badge {
    font-size: 9px;
    color: var(--color-accent);
    animation: pulse 1.5s ease-in-out infinite;
    flex-shrink: 0;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.35; }
  }

  /* Sub-section header within a window (e.g. R2S leg groupings, per-leg
     results capture) — smaller than .win-title, sits inline in the flow
     rather than in a separate header bar. */
  .config-subheader {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--color-subtext);
    opacity: 0.6;
    margin: 10px 0 6px;
  }
  .config-subheader:first-child { margin-top: 0; }

  /* Error window */
  .error-win .win-header { border-bottom-color: rgba(239,68,68,0.4); }
  .error-win .win-title { color: #f87171; }
  .error-text {
    font-family: var(--font-mono);
    font-size: 11px;
    color: #f87171;
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.6;
  }

  /* Console toolbar */
  .console-toolbar {
    display: flex;
    align-items: center;
    gap: 4px;
    margin-left: auto;
    flex-shrink: 0;
  }

  .search-box {
    display: flex;
    align-items: center;
    gap: 3px;
    margin-right: 4px;
  }

  .search-box input {
    width: 90px;
    font-size: 10px;
    font-family: var(--font-mono);
    background: var(--color-bg-deep);
    border: 1px solid var(--color-border);
    border-radius: 3px;
    color: var(--color-text);
    padding: 2px 5px;
  }
  .search-box input:focus {
    outline: none;
    border-color: var(--color-accent);
  }

  .match-count {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-subtext);
    white-space: nowrap;
  }

  .tool-btn {
    font-size: 9px;
    font-family: var(--font-mono);
    text-transform: uppercase;
    letter-spacing: 0.03em;
    padding: 2px 6px;
    border-radius: 3px;
    border: 1px solid var(--color-border);
    background: var(--color-bg-deep);
    color: var(--color-subtext);
    cursor: pointer;
    line-height: 1.4;
  }
  .tool-btn:hover:not(:disabled) { color: var(--color-text); border-color: var(--color-accent); }
  .tool-btn:disabled { opacity: 0.35; cursor: default; }
  .tool-btn.toggle.active {
    color: var(--color-accent-hi);
    border-color: var(--color-accent);
    background: rgba(6,182,212,0.1);
  }

  /* Console log */
  .log {
    margin: 0;
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    overflow-x: auto;
    font-family: var(--font-mono);
    font-size: 11px;
    line-height: 1.6;
  }

  .log-line {
    display: flex;
    gap: 10px;
    padding: 0 10px;
    white-space: pre;
  }
  .log.wrap .log-line { white-space: pre-wrap; word-break: break-word; }

  .log-line:hover { background: rgba(255,255,255,0.02); }

  .log-line-no {
    flex-shrink: 0;
    width: 34px;
    text-align: right;
    color: var(--color-subtext);
    opacity: 0.35;
    user-select: none;
  }

  .log-line-text {
    color: var(--color-subtext);
    flex: 1;
    min-width: 0;
  }

  .log-line.line-error .log-line-text { color: #f87171; }
  .log-line.line-warn .log-line-text { color: #fbbf24; }

  .log mark {
    background: rgba(6,182,212,0.35);
    color: var(--color-text);
    border-radius: 2px;
  }

  .jump-latest {
    position: absolute;
    bottom: 10px;
    right: 14px;
    font-size: 10px;
    font-family: var(--font-mono);
    padding: 4px 10px;
    border-radius: 12px;
    border: 1px solid var(--color-accent);
    background: var(--color-bg-raised);
    color: var(--color-accent-hi);
    cursor: pointer;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
  }
  .jump-latest:hover { background: rgba(6,182,212,0.1); }

  /* Results body */
  .results-body {
    display: flex;
    align-items: center;
  }

  .results-summary {
    display: flex;
    flex-direction: column;
    gap: 10px;
    width: 100%;
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