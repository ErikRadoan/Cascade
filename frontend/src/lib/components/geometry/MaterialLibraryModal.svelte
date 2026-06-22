<script lang="ts">
  // MaterialLibraryModal — full material library editor.
  // Opened from Edit > Material list in the MenuBar.
  // Allows: search, filter by library, add, edit, delete materials,
  // import JSON libraries, and delete entire library sets.

  import { onMount } from 'svelte';
  import * as api from '$lib/api';
  import type { MaterialDetail, MaterialSummary } from '$lib/types';

  let { onClose }: { onClose: () => void } = $props();

  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------

  let query        = $state('');
  let activeLibTag = $state<string | null>(null);
  let libraries    = $state<string[]>([]);
  let items        = $state<MaterialSummary[]>([]);
  let total        = $state(0);
  let loading      = $state(false);
  let offset       = $state(0);
  const LIMIT      = 50;

  let selected     = $state<MaterialDetail | null>(null);
  let editing      = $state(false);   // is the right-hand form open for a new/edit entry
  let isNewMaterial = $state(false);

  // Edit form state
  let editId      = $state('');
  let editName    = $state('');
  let editDensity = $state('');
  let editComposition = $state('');  // raw JSON string
  let editLibTag  = $state('user');
  let editError   = $state<string | null>(null);
  let editSaving  = $state(false);

  // Import state
  let showImportPanel = $state(false);
  let importFile      = $state<File | null>(null);
  let importTag       = $state('');
  let importOverwrite = $state(false);
  let importResult    = $state<{ imported: number; skipped: number; errors: string[] } | null>(null);
  let importing       = $state(false);

  let debounceTimer: ReturnType<typeof setTimeout>;

  // ---------------------------------------------------------------------------
  // Data loading
  // ---------------------------------------------------------------------------

  async function loadLibraries() {
    libraries = await api.materials.libraries();
  }

  async function loadMaterials() {
    loading = true;
    try {
      const res = await api.materials.search({
        search:      query,
        library_tag: activeLibTag ?? undefined,
        limit:       LIMIT,
        offset,
      });
      items  = res.items;
      total  = res.total;
    } catch {
      items = [];
    } finally {
      loading = false;
    }
  }

  function onQueryInput(e: Event) {
    query = (e.target as HTMLInputElement).value;
    offset = 0;
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(loadMaterials, 200);
  }

  function setLibFilter(tag: string | null) {
    activeLibTag = tag;
    offset = 0;
    loadMaterials();
  }

  onMount(async () => {
    await Promise.all([loadLibraries(), loadMaterials()]);
  });

  // ---------------------------------------------------------------------------
  // Select / edit / delete
  // ---------------------------------------------------------------------------

  async function selectMaterial(mat: MaterialSummary) {
    const detail = await api.materials.get(mat.id);
    selected = detail;
    editing  = false;
  }

  function startEdit(mat: MaterialDetail) {
    editId          = mat.id;
    editName        = mat.name;
    editDensity     = String(mat.density ?? '');
    editComposition = JSON.stringify(mat.composition, null, 2);
    editLibTag      = 'user';
    editError       = null;
    isNewMaterial   = false;
    editing         = true;
  }

  function startNew() {
    editId          = '';
    editName        = '';
    editDensity     = '';
    editComposition = '{\n  "": 1.0\n}';
    editLibTag      = 'user';
    editError       = null;
    isNewMaterial   = true;
    selected        = null;
    editing         = true;
  }

  async function saveEdit() {
    editError = null;
    editSaving = true;
    try {
      let composition: Record<string, number>;
      try {
        composition = JSON.parse(editComposition);
      } catch {
        editError = 'Composition must be valid JSON.';
        return;
      }
      const density = parseFloat(editDensity);
      if (Number.isNaN(density) || density <= 0) {
        editError = 'Density must be a positive number.';
        return;
      }
      const body = { name: editName, density, composition };
      if (isNewMaterial) {
        await api.materials.create(body, editLibTag);
      } else {
        await api.materials.update(editId, body);
      }
      editing  = false;
      selected = null;
      await Promise.all([loadLibraries(), loadMaterials()]);
    } catch (e: unknown) {
      editError = e instanceof Error ? e.message : 'Save failed.';
    } finally {
      editSaving = false;
    }
  }

  async function deleteMaterial(id: string) {
    if (!confirm(`Delete material '${id}'? This cannot be undone.`)) return;
    await api.materials.delete(id);
    selected = null;
    editing  = false;
    await Promise.all([loadLibraries(), loadMaterials()]);
  }

  async function deleteLibrary(tag: string) {
    if (!confirm(`Delete all materials in library '${tag}'? This cannot be undone.`)) return;
    await api.materials.deleteLibrary(tag);
    activeLibTag = null;
    await Promise.all([loadLibraries(), loadMaterials()]);
  }

  // ---------------------------------------------------------------------------
  // Import
  // ---------------------------------------------------------------------------

  async function runImport() {
    if (!importFile || !importTag.trim()) return;
    importing = true;
    importResult = null;
    try {
      const res = await api.materials.importJson(importFile, importTag.trim(), importOverwrite);
      importResult = {
        imported: res.imported.length,
        skipped:  res.skipped.length,
        errors:   res.errors,
      };
      await Promise.all([loadLibraries(), loadMaterials()]);
    } catch (e: unknown) {
      importResult = { imported: 0, skipped: 0, errors: [e instanceof Error ? e.message : 'Import failed'] };
    } finally {
      importing = false;
    }
  }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="modal-backdrop" onclick={onClose}>
  <div class="modal" onclick={(e) => e.stopPropagation()}>

    <!-- Header -->
    <div class="modal-header">
      <span class="modal-title">Material Library</span>
      <div class="header-actions">
        <button class="header-btn" onclick={startNew}>+ New material</button>
        <button class="header-btn" onclick={() => showImportPanel = !showImportPanel}>
          {showImportPanel ? 'Cancel import' : '↑ Import JSON'}
        </button>
        <button class="close-btn" onclick={onClose}>✕</button>
      </div>
    </div>

    <!-- Import panel -->
    {#if showImportPanel}
      <div class="import-panel">
        <div class="import-row">
          <label class="import-label">
            File (JSON array)
            <input type="file" accept=".json" onchange={(e) => importFile = (e.target as HTMLInputElement).files?.[0] ?? null} />
          </label>
          <label class="import-label">
            Library name
            <input class="import-input" type="text" bind:value={importTag} placeholder="e.g. PWR_library" />
          </label>
          <label class="import-check">
            <input type="checkbox" bind:checked={importOverwrite} />
            Overwrite existing
          </label>
          <button
            class="header-btn accent"
            disabled={!importFile || !importTag.trim() || importing}
            onclick={runImport}
          >
            {importing ? 'Importing…' : 'Import'}
          </button>
        </div>
        {#if importResult}
          <div class="import-result">
            ✓ {importResult.imported} imported, {importResult.skipped} skipped
            {#if importResult.errors.length > 0}
              — {importResult.errors.length} errors: {importResult.errors[0]}
            {/if}
          </div>
        {/if}
      </div>
    {/if}

    <div class="modal-body">

      <!-- Left: library filter sidebar -->
      <nav class="lib-sidebar">
        <div class="lib-sidebar-title">Libraries</div>
        <button
          class="lib-item"
          class:active={activeLibTag === null}
          onclick={() => setLibFilter(null)}
        >All ({total})</button>
        {#each libraries as lib}
          <div class="lib-item-row">
            <button
              class="lib-item"
              class:active={activeLibTag === lib}
              onclick={() => setLibFilter(lib)}
            >{lib}</button>
            {#if lib !== 'builtin'}
              <button class="lib-delete-btn" title="Delete library" onclick={() => deleteLibrary(lib)}>✕</button>
            {/if}
          </div>
        {/each}
      </nav>

      <!-- Centre: searchable material list -->
      <div class="mat-list-pane">
        <div class="search-row">
          <input
            class="search-input"
            type="text"
            value={query}
            oninput={onQueryInput}
            placeholder="Search by id, name, or nuclide…"
          />
        </div>

        <div class="mat-list">
          {#if loading}
            <div class="list-hint">Loading…</div>
          {:else if items.length === 0}
            <div class="list-hint">No materials found.</div>
          {:else}
            {#each items as mat}
              <!-- svelte-ignore a11y_click_events_have_key_events -->
              <!-- svelte-ignore a11y_no_static_element_interactions -->
              <div
                class="mat-row"
                class:active={selected?.id === mat.id}
                onclick={() => selectMaterial(mat)}
              >
                <span class="mat-id">{mat.id}</span>
                <span class="mat-name">{mat.name}</span>
                <span class="mat-density">{mat.density ?? '—'} g/cm³</span>
              </div>
            {/each}
            {#if total > items.length + offset}
              <div class="list-hint">Showing {items.length} of {total} — refine search</div>
            {/if}
          {/if}
        </div>
      </div>

      <!-- Right: detail / edit panel -->
      <div class="detail-pane">
        {#if editing}
          <div class="edit-form">
            <div class="detail-title">{isNewMaterial ? 'New material' : `Edit — ${editId}`}</div>

            <label class="form-label">
              ID {#if isNewMaterial}(auto-derived from name){/if}
              <input class="form-input" type="text" bind:value={editId} disabled={!isNewMaterial} />
            </label>
            <label class="form-label">
              Name
              <input class="form-input" type="text" bind:value={editName}
                oninput={() => { if (isNewMaterial) editId = editName.replace(/ /g, '_').toUpperCase(); }} />
            </label>
            <label class="form-label">
              Density (g/cm³)
              <input class="form-input" type="number" step="any" min="0" bind:value={editDensity} />
            </label>
            <label class="form-label">
              Library tag
              <input class="form-input" type="text" bind:value={editLibTag} />
            </label>
            <label class="form-label">
              Composition (JSON: nuclide → atom fraction)
              <textarea class="form-textarea" bind:value={editComposition} rows={8}></textarea>
            </label>

            {#if editError}
              <div class="form-error">{editError}</div>
            {/if}

            <div class="form-actions">
              <button class="form-btn-cancel" onclick={() => editing = false}>Cancel</button>
              <button class="form-btn-save" disabled={editSaving} onclick={saveEdit}>
                {editSaving ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>

        {:else if selected}
          <div class="detail-view">
            <div class="detail-title">{selected.id}</div>
            <div class="detail-subtitle">{selected.name}</div>

            <table class="detail-table">
              <tbody>
                <tr>
                  <td class="dt-key">Density</td>
                  <td>{selected.density ?? '—'} g/cm³</td>
                </tr>
                <tr>
                  <td class="dt-key">Nuclides</td>
                  <td>{Object.keys(selected.composition).length}</td>
                </tr>
              </tbody>
            </table>

            <div class="composition-title">Composition (atom fractions)</div>
            <div class="composition-list">
              {#each Object.entries(selected.composition) as [nuclide, frac]}
                <div class="comp-row">
                  <span class="comp-nuclide">{nuclide}</span>
                  <span class="comp-frac">{frac}</span>
                </div>
              {/each}
            </div>

            <div class="detail-actions">
              <button class="form-btn-cancel" onclick={() => startEdit(selected!)}>Edit</button>
              <button class="form-btn-delete" onclick={() => deleteMaterial(selected!.id)}>Delete</button>
            </div>
          </div>

        {:else}
          <div class="detail-empty">Select a material to view its composition.</div>
        {/if}
      </div>

    </div>
  </div>
</div>

<style>
  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.65);
    z-index: 500;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .modal {
    width: 900px;
    max-width: 95vw;
    height: 600px;
    max-height: 90vh;
    background: var(--color-bg-panel);
    border: 1px solid var(--color-border);
    border-radius: 10px;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-shadow: 0 24px 64px rgba(0,0,0,0.6);
  }

  .modal-header {
    display: flex;
    align-items: center;
    padding: 10px 14px;
    border-bottom: 1px solid var(--color-border);
    gap: 8px;
    flex-shrink: 0;
  }

  .modal-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--color-text);
    flex: 1;
  }

  .header-actions { display: flex; gap: 6px; align-items: center; }

  .header-btn {
    font-size: 11px;
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    color: var(--color-text);
    padding: 4px 10px;
    border-radius: 5px;
    cursor: pointer;
  }

  .header-btn.accent { background: var(--color-accent); color: var(--color-bg-deep); border-color: transparent; font-weight: 600; }
  .header-btn:disabled { opacity: 0.45; cursor: default; }
  .header-btn:hover:not(:disabled) { border-color: var(--color-accent); }

  .close-btn {
    background: transparent; border: none; color: var(--color-subtext);
    font-size: 14px; cursor: pointer; padding: 2px 6px; border-radius: 4px;
  }
  .close-btn:hover { color: var(--color-text); background: var(--color-bg-raised); }

  .import-panel {
    padding: 10px 14px;
    background: rgba(6,182,212,0.05);
    border-bottom: 1px solid var(--color-border);
    flex-shrink: 0;
  }

  .import-row { display: flex; align-items: flex-end; gap: 10px; flex-wrap: wrap; }
  .import-label { display: flex; flex-direction: column; gap: 3px; font-size: 11px; color: var(--color-subtext); }
  .import-input {
    background: var(--color-bg-raised); border: 1px solid var(--color-border);
    border-radius: 4px; color: var(--color-text); font-family: var(--font-mono);
    font-size: 12px; padding: 4px 8px;
  }
  .import-check { display: flex; align-items: center; gap: 5px; font-size: 11px; color: var(--color-subtext); }
  .import-result {
    margin-top: 6px; font-size: 11px; color: var(--color-accent-hi);
    font-family: var(--font-mono);
  }

  .modal-body {
    display: grid;
    grid-template-columns: 140px 1fr 280px;
    flex: 1;
    overflow: hidden;
  }

  /* Library sidebar */
  .lib-sidebar {
    border-right: 1px solid var(--color-border);
    padding: 8px;
    overflow-y: auto;
    background: var(--color-bg-deep);
  }

  .lib-sidebar-title {
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--color-subtext); padding: 4px 4px 8px;
  }

  .lib-item-row { display: flex; align-items: center; gap: 2px; }

  .lib-item {
    flex: 1; text-align: left; font-size: 12px; padding: 5px 8px;
    background: transparent; border: none; border-radius: 5px;
    color: var(--color-subtext); cursor: pointer;
    font-family: var(--font-mono);
  }
  .lib-item:hover { background: var(--color-bg-raised); color: var(--color-text); }
  .lib-item.active { background: rgba(6,182,212,0.12); color: var(--color-accent-hi); }

  .lib-delete-btn {
    width: 18px; height: 18px; background: transparent; border: none;
    color: var(--color-subtext); cursor: pointer; border-radius: 3px;
    font-size: 10px; display: flex; align-items: center; justify-content: center;
  }
  .lib-delete-btn:hover { color: #f87171; background: var(--color-bg-raised); }

  /* Material list pane */
  .mat-list-pane {
    display: flex; flex-direction: column; overflow: hidden;
    border-right: 1px solid var(--color-border);
  }

  .search-row { padding: 8px; border-bottom: 1px solid var(--color-border); }

  .search-input {
    width: 100%; background: var(--color-bg-raised);
    border: 1px solid var(--color-border); border-radius: 5px;
    color: var(--color-text); font-family: var(--font-sans);
    font-size: 12px; padding: 5px 10px;
  }
  .search-input:focus { outline: none; border-color: var(--color-accent); }

  .mat-list { flex: 1; overflow-y: auto; }

  .mat-row {
    display: flex; align-items: center; gap: 8px;
    padding: 6px 12px; cursor: pointer; font-size: 12px;
    border-bottom: 1px solid rgba(51,65,85,0.3);
    transition: background 0.1s;
  }
  .mat-row:hover { background: var(--color-bg-raised); }
  .mat-row.active { background: rgba(6,182,212,0.1); }

  .mat-id {
    font-family: var(--font-mono); font-size: 11px;
    color: var(--color-accent-hi); min-width: 70px; flex-shrink: 0;
  }
  .mat-name { flex: 1; color: var(--color-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .mat-density { font-family: var(--font-mono); font-size: 10px; color: var(--color-subtext); flex-shrink: 0; }

  .list-hint {
    font-size: 11px; color: var(--color-subtext); text-align: center;
    padding: 12px; opacity: 0.7;
  }

  /* Detail pane */
  .detail-pane { padding: 14px; overflow-y: auto; }

  .detail-empty {
    font-size: 12px; color: var(--color-subtext);
    text-align: center; padding: 30px 0; opacity: 0.7;
  }

  .detail-title { font-family: var(--font-mono); font-size: 15px; font-weight: 700; color: var(--color-text); margin-bottom: 2px; }
  .detail-subtitle { font-size: 11px; color: var(--color-subtext); margin-bottom: 12px; }

  .detail-table { width: 100%; border-collapse: collapse; margin-bottom: 12px; font-size: 12px; }
  .dt-key { color: var(--color-subtext); padding-right: 12px; padding-bottom: 4px; }

  .composition-title {
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--color-subtext); margin-bottom: 6px;
  }

  .composition-list {
    background: var(--color-bg-raised); border-radius: 6px;
    padding: 8px; max-height: 160px; overflow-y: auto;
  }

  .comp-row {
    display: flex; justify-content: space-between;
    font-family: var(--font-mono); font-size: 11px;
    color: var(--color-text); padding: 2px 0;
    border-bottom: 1px solid rgba(51,65,85,0.3);
  }
  .comp-nuclide { color: var(--color-accent-hi); }
  .comp-frac { color: var(--color-subtext); }

  .detail-actions { display: flex; gap: 6px; margin-top: 12px; }

  /* Edit form */
  .edit-form { display: flex; flex-direction: column; gap: 8px; }

  .form-label { display: flex; flex-direction: column; gap: 3px; font-size: 11px; color: var(--color-subtext); }
  .form-input {
    background: var(--color-bg-raised); border: 1px solid var(--color-border);
    border-radius: 4px; color: var(--color-text); font-family: var(--font-mono);
    font-size: 12px; padding: 5px 8px;
  }
  .form-input:disabled { opacity: 0.5; }
  .form-input:focus { outline: none; border-color: var(--color-accent); }
  .form-textarea {
    background: var(--color-bg-raised); border: 1px solid var(--color-border);
    border-radius: 4px; color: var(--color-text); font-family: var(--font-mono);
    font-size: 11px; padding: 6px 8px; resize: vertical;
  }
  .form-textarea:focus { outline: none; border-color: var(--color-accent); }

  .form-error { font-size: 11px; color: #f87171; }

  .form-actions { display: flex; gap: 6px; justify-content: flex-end; margin-top: 4px; }

  .form-btn-cancel {
    font-size: 11px; background: transparent; border: 1px solid var(--color-border);
    color: var(--color-subtext); padding: 5px 12px; border-radius: 5px; cursor: pointer;
  }
  .form-btn-cancel:hover { color: var(--color-text); border-color: var(--color-text); }

  .form-btn-save {
    font-size: 11px; background: var(--color-accent); color: var(--color-bg-deep);
    border: none; padding: 5px 14px; border-radius: 5px; cursor: pointer; font-weight: 600;
  }
  .form-btn-save:disabled { opacity: 0.45; cursor: default; }
  .form-btn-save:hover:not(:disabled) { background: var(--color-accent-hi); }

  .form-btn-delete {
    font-size: 11px; background: transparent; border: 1px solid #ef4444;
    color: #f87171; padding: 5px 12px; border-radius: 5px; cursor: pointer;
  }
  .form-btn-delete:hover { background: rgba(239,68,68,0.1); }
</style>