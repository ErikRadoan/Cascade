<script lang="ts">
  import * as api from '$lib/api';
  import type { BackendProfile } from '$lib/types';

  interface Props {
    profiles:       BackendProfile[];
    onClose:        () => void;
    onProfilesChange: (profiles: BackendProfile[]) => void;
  }

  let { profiles, onClose, onProfilesChange }: Props = $props();

  // ---------------------------------------------------------------------------
  // Constants
  // ---------------------------------------------------------------------------

  const BACKEND_TYPE_LABELS: Record<string, string> = {
    docker: 'Docker / Podman',
    local:  'Local process',
    slurm:  'SLURM cluster',
  };

  interface FieldDef {
    key:          string;
    label:        string;
    type:         'text' | 'number' | 'select';
    options?:     string[];
    placeholder?: string;
    required?:    boolean;
    hint?:        string;
    wide?:        boolean;
  }

  const BACKEND_FIELDS: Record<string, FieldDef[]> = {
    docker: [
      { key: 'cli',                         label: 'Container CLI',               type: 'select', options: ['podman', 'docker'], required: true },
      { key: 'image',                       label: 'Container image',             type: 'text',   placeholder: 'cascade-openmc:latest', required: true },
      { key: 'openmc_bin',                  label: 'OpenMC binary (container)',   type: 'text',   placeholder: '/opt/miniconda/envs/openmc/bin/openmc', wide: true },
      { key: 'nuclear_data_path',           label: 'Nuclear data path (host)',    type: 'text',   placeholder: '~/.cascade/data', hint: 'Directory containing cross_sections.xml.', wide: true },
      { key: 'nuclear_data_container_path', label: 'Nuclear data mount',         type: 'text',   placeholder: '/nuclear-data', wide: true },
      { key: 'jobs_base_dir',               label: 'Jobs base directory',         type: 'text',   placeholder: '~/.cascade/jobs', wide: true },
      { key: 'memory_limit',                label: 'Memory limit',                type: 'text',   placeholder: '4g', hint: "e.g. '4g', '16g', '512m'" },
      { key: 'cpu_limit',                   label: 'CPU limit',                   type: 'text',   placeholder: '0', hint: "'0' = no limit, '2.0' = 2 cores" },
    ],
    local: [
      { key: 'openmc_bin',        label: 'OpenMC binary',       type: 'text', placeholder: 'openmc', hint: "Use 'openmc' if on PATH.", wide: true },
      { key: 'nuclear_data_path', label: 'Nuclear data path',   type: 'text', placeholder: '~/.cascade/data', hint: 'Directory containing cross_sections.xml.', wide: true },
      { key: 'jobs_base_dir',     label: 'Jobs base directory', type: 'text', placeholder: '~/.cascade/jobs', wide: true },
    ],
    slurm: [
      { key: 'host',             label: 'Login node hostname',       type: 'text',   placeholder: 'metacentrum.muni.cz', required: true },
      { key: 'username',         label: 'SSH username',              type: 'text',   placeholder: 'xradovan',            required: true },
      { key: 'ssh_key_path',     label: 'SSH private key',           type: 'text',   placeholder: '~/.ssh/id_ed25519',  wide: true },
      { key: 'ssh_port',         label: 'SSH port',                  type: 'number', placeholder: '22' },
      { key: 'partition',        label: 'Partition / queue',         type: 'text',   placeholder: 'compute' },
      { key: 'nodes',            label: 'Nodes',                     type: 'number', placeholder: '1' },
      { key: 'tasks_per_node',   label: 'Tasks per node',            type: 'number', placeholder: '8', hint: 'MPI tasks = CPU cores' },
      { key: 'walltime',         label: 'Walltime',                  type: 'text',   placeholder: '02:00:00', hint: 'HH:MM:SS' },
      { key: 'memory_per_node',  label: 'Memory per node',           type: 'text',   placeholder: '16G' },
      { key: 'account',          label: 'Account / project',         type: 'text',   placeholder: '(optional)' },
      { key: 'openmc_module',    label: 'OpenMC env module',         type: 'text',   placeholder: 'OpenMC/0.15.3-foss-2023a', hint: 'Leave blank to use binary path.', wide: true },
      { key: 'openmc_bin',       label: 'OpenMC binary (cluster)',   type: 'text',   placeholder: 'openmc', hint: 'Used when no module is set.', wide: true },
      { key: 'remote_work_dir',  label: 'Remote work directory',     type: 'text',   placeholder: '/scratch/username/cascade_jobs', required: true, wide: true, hint: 'Writable directory on the cluster.' },
      { key: 'nuclear_data_path',label: 'Nuclear data path (cluster)', type: 'text', placeholder: '/shared/openmc_data', wide: true },
      { key: 'jobs_base_dir',    label: 'Jobs base dir (local)',     type: 'text',   placeholder: '~/.cascade/jobs', wide: true },
    ],
  };

  const DEFAULTS: Record<string, Record<string, string>> = {
    docker: {
      cli: 'podman', image: 'cascade-openmc:latest',
      openmc_bin: '/opt/miniconda/envs/openmc/bin/openmc',
      nuclear_data_path: '~/.cascade/data',
      nuclear_data_container_path: '/nuclear-data',
      jobs_base_dir: '~/.cascade/jobs',
      memory_limit: '4g', cpu_limit: '0',
    },
    local: {
      openmc_bin: 'openmc',
      nuclear_data_path: '~/.cascade/data',
      jobs_base_dir: '~/.cascade/jobs',
    },
    slurm: {
      host: '', username: '', ssh_key_path: '~/.ssh/id_ed25519', ssh_port: '22',
      partition: 'compute', nodes: '1', tasks_per_node: '8',
      walltime: '02:00:00', memory_per_node: '16G', account: '',
      openmc_module: '', openmc_bin: 'openmc',
      remote_work_dir: '', nuclear_data_path: '/shared/openmc_data',
      jobs_base_dir: '~/.cascade/jobs',
    },
  };

  // ---------------------------------------------------------------------------
  // View state: 'list' | 'editor'
  // ---------------------------------------------------------------------------

  type View = 'list' | 'editor';
  let view = $state<View>('list');

  type EditorMode = 'create' | 'edit';
  let editorMode       = $state<EditorMode>('create');
  let editorSaving     = $state(false);
  let editorError      = $state<string | null>(null);

  let editName         = $state('');
  let editBackendType  = $state<'docker' | 'local' | 'slurm'>('docker');
  let editDescription  = $state('');
  let editConfigData   = $state<Record<string, string>>({});
  let editOriginalName = $state('');

  let deleteConfirm    = $state<string | null>(null);
  let listError        = $state<string | null>(null);

  // ---------------------------------------------------------------------------
  // Navigation
  // ---------------------------------------------------------------------------

  function openCreate() {
    editorMode       = 'create';
    editName         = '';
    editBackendType  = 'docker';
    editDescription  = '';
    editConfigData   = { ...DEFAULTS.docker };
    editOriginalName = '';
    editorError      = null;
    view             = 'editor';
  }

  function openEdit(profile: BackendProfile) {
    editorMode       = 'edit';
    editName         = profile.name;
    editBackendType  = profile.backend_type;
    editDescription  = profile.description ?? '';
    editConfigData   = stringify(profile.config_data);
    editOriginalName = profile.name;
    editorError      = null;
    view             = 'editor';
  }

  function backToList() {
    view        = 'list';
    editorError = null;
  }

  function onBackendTypeChange(t: 'docker' | 'local' | 'slurm') {
    editBackendType = t;
    editConfigData  = { ...DEFAULTS[t] };
  }

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  function stringify(data: Record<string, unknown>): Record<string, string> {
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries(data)) {
      out[k] = v === null || v === undefined ? '' : String(v);
    }
    return out;
  }

  function parseConfig(type: string, form: Record<string, string>): Record<string, unknown> {
    const numeric = new Set(type === 'slurm' ? ['ssh_port', 'nodes', 'tasks_per_node'] : []);
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(form)) {
      out[k] = v === '' ? null : numeric.has(k) ? Number(v) : v;
    }
    return out;
  }

  function backendTypeColor(t: string): string {
    switch (t) {
      case 'docker': return '#06b6d4';
      case 'local':  return '#22c55e';
      case 'slurm':  return '#a78bfa';
      default:       return '#94a3b8';
    }
  }

  // ---------------------------------------------------------------------------
  // API actions
  // ---------------------------------------------------------------------------

  async function save() {
    editorError  = null;
    editorSaving = true;
    const config_data   = parseConfig(editBackendType, editConfigData);
    const description   = editDescription.trim() || null;

    try {
      let updated: BackendProfile;
      if (editorMode === 'create') {
        updated = await (api as any).profiles.create({
          name: editName.trim(), backend_type: editBackendType, config_data, description,
        });
        onProfilesChange([...profiles, updated].sort((a, b) => a.name.localeCompare(b.name)));
      } else {
        updated = await (api as any).profiles.update(editOriginalName, {
          backend_type: editBackendType, config_data, description,
        });
        onProfilesChange(profiles.map(p => p.name === editOriginalName ? updated : p));
      }
      backToList();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      try {
        const parsed = JSON.parse(msg.replace(/^API \d+: /, ''));
        editorError = Array.isArray(parsed)
          ? parsed.map((p: any) => `${p.loc?.join('.')} — ${p.msg}`).join('\n')
          : (parsed.detail ?? msg);
      } catch { editorError = msg; }
    } finally {
      editorSaving = false;
    }
  }

  async function deleteProfile(name: string) {
    listError = null;
    try {
      await (api as any).profiles.delete(name);
      onProfilesChange(profiles.filter(p => p.name !== name));
      deleteConfirm = null;
    } catch (e: unknown) {
      listError = e instanceof Error ? e.message : String(e);
    }
  }

  // ---------------------------------------------------------------------------
  // Keyboard / backdrop
  // ---------------------------------------------------------------------------

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      if (view === 'editor') backToList();
      else onClose();
    }
  }

  function onBackdropClick(e: MouseEvent) {
    if (e.target === e.currentTarget) onClose();
  }
</script>

<svelte:window onkeydown={onKeydown} />

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="backdrop" onclick={onBackdropClick}>
  <div class="modal" role="dialog" aria-modal="true" aria-label="Backend profiles">

    <!-- ══════════════════════════════════════════════════════════════ -->
    <!-- LIST VIEW                                                      -->
    <!-- ══════════════════════════════════════════════════════════════ -->
    {#if view === 'list'}

      <div class="modal-header">
        <div class="modal-title-group">
          <svg viewBox="0 0 16 16" fill="currentColor" class="title-icon">
            <path d="M1.75 2h12.5A1.75 1.75 0 0116 3.75v2.5A1.75 1.75 0 0114.25 8H1.75A1.75 1.75 0 010 6.25v-2.5A1.75 1.75 0 011.75 2zM1.5 3.75v2.5c0 .138.112.25.25.25h12.5a.25.25 0 00.25-.25v-2.5a.25.25 0 00-.25-.25H1.75a.25.25 0 00-.25.25zM1.75 9h12.5A1.75 1.75 0 0116 10.75v2.5A1.75 1.75 0 0114.25 15H1.75A1.75 1.75 0 010 13.25v-2.5A1.75 1.75 0 011.75 9zm-.25 1.75v2.5c0 .138.112.25.25.25h12.5a.25.25 0 00.25-.25v-2.5a.25.25 0 00-.25-.25H1.75a.25.25 0 00-.25.25z"/>
            <circle cx="12.5" cy="5" r="1"/>
            <circle cx="12.5" cy="12" r="1"/>
          </svg>
          <span class="modal-title">Backend profiles</span>
        </div>
        <div class="header-right">
          <button class="add-btn" onclick={openCreate}>
            <svg viewBox="0 0 16 16" fill="currentColor"><path d="M8 2a.75.75 0 01.75.75v4.5h4.5a.75.75 0 010 1.5h-4.5v4.5a.75.75 0 01-1.5 0v-4.5h-4.5a.75.75 0 010-1.5h4.5v-4.5A.75.75 0 018 2z"/></svg>
            New profile
          </button>
          <button class="close-btn" onclick={onClose} aria-label="Close">
            <svg viewBox="0 0 16 16" fill="currentColor"><path d="M3.72 3.72a.75.75 0 011.06 0L8 6.94l3.22-3.22a.75.75 0 111.06 1.06L9.06 8l3.22 3.22a.75.75 0 11-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 01-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 010-1.06z"/></svg>
          </button>
        </div>
      </div>

      <div class="modal-body">
        {#if listError}
          <p class="list-error">{listError}</p>
        {/if}

        {#if profiles.length === 0}
          <div class="empty-state">
            <p>No profiles yet.</p>
            <button class="add-btn" onclick={openCreate}>Create your first profile</button>
          </div>
        {:else}
          <div class="profile-list">
            {#each profiles as profile (profile.name)}
              <div class="profile-row" class:confirming={deleteConfirm === profile.name}>

                <div class="profile-row-left" onclick={() => openEdit(profile)} role="button" tabindex="0"
                  onkeydown={e => e.key === 'Enter' && openEdit(profile)}>
                  <span class="type-dot" style="background:{backendTypeColor(profile.backend_type)}"></span>
                  <div class="profile-info">
                    <span class="profile-name">{profile.name}</span>
                    <span class="profile-meta">
                      {BACKEND_TYPE_LABELS[profile.backend_type] ?? profile.backend_type}
                      {#if profile.description}<span class="meta-sep">·</span>{profile.description}{/if}
                    </span>
                  </div>
                </div>

                <div class="profile-row-right">
                  {#if deleteConfirm === profile.name}
                    <span class="confirm-label">Delete?</span>
                    <button class="action-btn danger" onclick={() => deleteProfile(profile.name)}>Yes</button>
                    <button class="action-btn" onclick={() => deleteConfirm = null}>No</button>
                  {:else}
                    <button class="action-btn" onclick={() => openEdit(profile)}>Edit</button>
                    {#if profile.name !== 'default'}
                      <button class="action-btn danger" onclick={() => deleteConfirm = profile.name}>Delete</button>
                    {/if}
                  {/if}
                </div>

              </div>
            {/each}
          </div>
        {/if}
      </div>

    <!-- ══════════════════════════════════════════════════════════════ -->
    <!-- EDITOR VIEW                                                    -->
    <!-- ══════════════════════════════════════════════════════════════ -->
    {:else}

      <div class="modal-header">
        <div class="modal-title-group">
          <button class="back-btn" onclick={backToList} aria-label="Back to list">
            <svg viewBox="0 0 16 16" fill="currentColor"><path d="M9.78 12.78a.75.75 0 01-1.06 0L4.47 8.53a.75.75 0 010-1.06l4.25-4.25a.75.75 0 011.06 1.06L6.06 8l3.72 3.72a.75.75 0 010 1.06z"/></svg>
          </button>
          <span class="modal-title">
            {editorMode === 'create' ? 'New backend profile' : `Edit · ${editOriginalName}`}
          </span>
        </div>
        <button class="close-btn" onclick={onClose} aria-label="Close">
          <svg viewBox="0 0 16 16" fill="currentColor"><path d="M3.72 3.72a.75.75 0 011.06 0L8 6.94l3.22-3.22a.75.75 0 111.06 1.06L9.06 8l3.22 3.22a.75.75 0 11-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 01-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 010-1.06z"/></svg>
        </button>
      </div>

      <div class="modal-body">

        <!-- Identity -->
        <section class="section">
          <div class="section-label">Identity</div>
          <div class="fields-grid">
            {#if editorMode === 'create'}
              <label class="field wide">
                <span>Profile name <span class="required">*</span></span>
                <input
                  type="text"
                  placeholder="e.g. metacentrum, local_dev, aws_batch"
                  bind:value={editName}
                  autocomplete="off"
                />
                <span class="field-hint">Alphanumeric, dashes, underscores only.</span>
              </label>
            {/if}
            <label class="field wide">
              <span>Description <span class="optional">(optional)</span></span>
              <input
                type="text"
                placeholder="e.g. FI MUNI MetaCentrum, 8 cores"
                bind:value={editDescription}
              />
            </label>
          </div>
        </section>

        <!-- Backend type -->
        <section class="section">
          <div class="section-label">Backend type</div>
          <div class="type-row">
            {#each (['docker', 'local', 'slurm'] as const) as bt}
              <button
                class="type-btn"
                class:active={editBackendType === bt}
                onclick={() => onBackendTypeChange(bt)}
              >
                <span class="type-dot-sm" style="background:{backendTypeColor(bt)}"></span>
                {BACKEND_TYPE_LABELS[bt]}
              </button>
            {/each}
          </div>
        </section>

        <!-- Dynamic config fields -->
        <section class="section">
          <div class="section-label">Configuration</div>
          <div class="fields-grid">
            {#each BACKEND_FIELDS[editBackendType] ?? [] as fdef (fdef.key)}
              <label class="field" class:wide={fdef.wide}>
                <span>
                  {fdef.label}
                  {#if fdef.required}<span class="required">*</span>{/if}
                </span>
                {#if fdef.type === 'select' && fdef.options}
                  <select bind:value={editConfigData[fdef.key]}>
                    {#each fdef.options as opt}
                      <option value={opt}>{opt}</option>
                    {/each}
                  </select>
                {:else}
                  <input
                    type={fdef.type === 'number' ? 'number' : 'text'}
                    placeholder={fdef.placeholder}
                    bind:value={editConfigData[fdef.key]}
                  />
                {/if}
                {#if fdef.hint}
                  <span class="field-hint">{fdef.hint}</span>
                {/if}
              </label>
            {/each}
          </div>
        </section>

      </div>

      <div class="modal-footer">
        {#if editorError}
          <p class="editor-error">{editorError}</p>
        {/if}
        <div class="footer-actions">
          <button class="cancel-btn" onclick={backToList}>Cancel</button>
          <button class="save-btn" disabled={editorSaving} onclick={save}>
            {#if editorSaving}
              <span class="spinner"></span> Saving…
            {:else}
              {editorMode === 'create' ? 'Create profile' : 'Save changes'}
            {/if}
          </button>
        </div>
      </div>

    {/if}

  </div>
</div>

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.55);
    backdrop-filter: blur(2px);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 200;
    padding: 20px;
  }

  .modal {
    background: var(--color-bg-panel);
    border: 1px solid var(--color-border);
    border-radius: 10px;
    width: 600px;
    max-width: 100%;
    max-height: calc(100vh - 40px);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-shadow: 0 24px 64px rgba(0,0,0,0.5);
  }

  /* ── Header ── */
  .modal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 16px 12px;
    border-bottom: 1px solid var(--color-border);
    flex-shrink: 0;
    gap: 8px;
  }

  .modal-title-group {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }

  .title-icon {
    width: 14px;
    height: 14px;
    color: var(--color-accent-hi);
    flex-shrink: 0;
  }

  .modal-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--color-text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .header-right {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }

  .add-btn {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 11px;
    font-weight: 500;
    color: var(--color-accent-hi);
    background: rgba(6,182,212,0.08);
    border: 1px solid rgba(6,182,212,0.3);
    padding: 4px 10px;
    border-radius: 5px;
    cursor: pointer;
    transition: background 0.1s;
  }
  .add-btn svg { width: 11px; height: 11px; }
  .add-btn:hover { background: rgba(6,182,212,0.15); }

  .close-btn {
    background: transparent;
    border: none;
    color: var(--color-subtext);
    cursor: pointer;
    padding: 3px;
    display: flex;
    align-items: center;
    border-radius: 4px;
    transition: color 0.1s;
    flex-shrink: 0;
  }
  .close-btn svg { width: 14px; height: 14px; }
  .close-btn:hover { color: var(--color-text); }

  .back-btn {
    background: transparent;
    border: none;
    color: var(--color-subtext);
    cursor: pointer;
    padding: 3px;
    display: flex;
    align-items: center;
    border-radius: 4px;
    transition: color 0.1s;
    flex-shrink: 0;
  }
  .back-btn svg { width: 16px; height: 16px; }
  .back-btn:hover { color: var(--color-text); }

  /* ── Body ── */
  .modal-body {
    flex: 1;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
  }

  /* ── List view ── */
  .list-error {
    font-size: 11px;
    color: #f87171;
    padding: 8px 16px 0;
    margin: 0;
  }

  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
    padding: 48px 24px;
    color: var(--color-subtext);
    font-size: 13px;
  }

  .profile-list {
    display: flex;
    flex-direction: column;
    padding: 8px;
    gap: 3px;
  }

  .profile-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 10px 10px 12px;
    border-radius: 7px;
    border: 1px solid transparent;
    background: var(--color-bg-raised);
    gap: 8px;
    transition: border-color 0.1s;
  }
  .profile-row:hover { border-color: var(--color-border); }
  .profile-row.confirming { border-color: rgba(239,68,68,0.4); background: rgba(239,68,68,0.04); }

  .profile-row-left {
    display: flex;
    align-items: center;
    gap: 10px;
    flex: 1;
    min-width: 0;
    cursor: pointer;
  }

  .type-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
    opacity: 0.85;
  }

  .profile-info {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  .profile-name {
    font-size: 12px;
    font-family: var(--font-mono);
    font-weight: 600;
    color: var(--color-text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .profile-meta {
    font-size: 10px;
    color: var(--color-subtext);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    display: flex;
    gap: 4px;
  }

  .meta-sep { opacity: 0.4; }

  .profile-row-right {
    display: flex;
    align-items: center;
    gap: 4px;
    flex-shrink: 0;
  }

  .confirm-label {
    font-size: 10px;
    color: #f87171;
    font-family: var(--font-mono);
    margin-right: 2px;
  }

  .action-btn {
    font-size: 10px;
    font-family: var(--font-mono);
    padding: 3px 9px;
    border-radius: 4px;
    border: 1px solid var(--color-border);
    background: transparent;
    color: var(--color-subtext);
    cursor: pointer;
    transition: color 0.1s, border-color 0.1s;
  }
  .action-btn:hover { color: var(--color-text); border-color: #475569; }
  .action-btn.danger { color: #f87171; border-color: rgba(239,68,68,0.3); }
  .action-btn.danger:hover { background: rgba(239,68,68,0.08); border-color: #f87171; }

  /* ── Editor view sections ── */
  .section {
    padding: 14px 16px;
    border-bottom: 1px solid rgba(51,65,85,0.5);
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .section:last-child { border-bottom: none; }

  .section-label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    font-weight: 600;
    color: var(--color-subtext);
  }

  /* Backend type selector */
  .type-row {
    display: flex;
    gap: 6px;
  }

  .type-btn {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 7px 8px;
    border-radius: 6px;
    border: 1px solid var(--color-border);
    background: var(--color-bg-raised);
    color: var(--color-subtext);
    font-size: 11px;
    font-family: var(--font-mono);
    cursor: pointer;
    transition: border-color 0.1s, color 0.1s;
    white-space: nowrap;
  }
  .type-btn:hover { color: var(--color-text); }
  .type-btn.active {
    border-color: var(--color-accent);
    color: var(--color-accent-hi);
    background: rgba(6,182,212,0.07);
  }

  .type-dot-sm {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    flex-shrink: 0;
    opacity: 0.85;
  }

  /* Fields */
  .fields-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 10px;
    color: var(--color-subtext);
  }

  .field.wide { grid-column: 1 / -1; }

  .field input,
  .field select {
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    border-radius: 5px;
    color: var(--color-text);
    font-family: var(--font-mono);
    font-size: 12px;
    padding: 6px 8px;
    transition: border-color 0.1s;
  }
  .field input:focus,
  .field select:focus { outline: none; border-color: var(--color-accent); }
  .field select { cursor: pointer; }

  .field-hint {
    font-size: 9px;
    color: var(--color-subtext);
    opacity: 0.65;
    line-height: 1.4;
  }

  .required { color: #f87171; margin-left: 2px; }
  .optional {
    font-size: 9px;
    opacity: 0.6;
    text-transform: none;
    font-weight: 400;
    letter-spacing: 0;
    margin-left: 2px;
  }

  /* ── Footer ── */
  .modal-footer {
    padding: 12px 16px;
    border-top: 1px solid var(--color-border);
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .editor-error {
    font-size: 10px;
    color: #f87171;
    line-height: 1.5;
    white-space: pre-wrap;
    font-family: var(--font-mono);
    margin: 0;
  }

  .footer-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }

  .cancel-btn {
    font-size: 12px;
    padding: 7px 16px;
    border-radius: 6px;
    border: 1px solid var(--color-border);
    background: transparent;
    color: var(--color-subtext);
    cursor: pointer;
    transition: color 0.1s;
  }
  .cancel-btn:hover { color: var(--color-text); }

  .save-btn {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    font-weight: 700;
    padding: 7px 20px;
    border-radius: 6px;
    border: none;
    background: var(--color-accent);
    color: var(--color-bg-deep);
    cursor: pointer;
    transition: background 0.1s;
  }
  .save-btn:disabled { opacity: 0.45; cursor: default; }
  .save-btn:hover:not(:disabled) { background: var(--color-accent-hi); }

  .spinner {
    width: 11px;
    height: 11px;
    border: 2px solid rgba(15,23,42,0.3);
    border-top-color: var(--color-bg-deep);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>