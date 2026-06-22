<script lang="ts">
  // MaterialSearchSelect — a searchable dropdown for picking a material ID
  // from the live material library. Replaces the static 6-option select.
  //
  // Props:
  //   value       — currently selected material_id string
  //   onChange    — called with the new material_id when the user picks one
  //   multiSelect — if true, renders checkboxes for sweep selection;
  //                 onChange is called with a comma-joined string of all
  //                 checked IDs. Used by SweepToggle for material fields.
  //   checked     — Set<string> of currently checked IDs (multiSelect mode)

  import { onMount } from 'svelte';
  import * as api from '$lib/api';
  import type { MaterialSummary } from '$lib/types';

  let {
    value = '',
    onChange,
    multiSelect = false,
    checked = new Set<string>(),
    placeholder = 'Search materials…',
  }: {
    value?: string;
    onChange: (v: string) => void;
    multiSelect?: boolean;
    checked?: Set<string>;
    placeholder?: string;
  } = $props();

  let open = $state(false);
  let query = $state('');
  let results = $state<MaterialSummary[]>([]);
  let total = $state(0);
  let loading = $state(false);
  let debounceTimer: ReturnType<typeof setTimeout>;

  // The display name shown in the closed state
  let displayName = $state(value || placeholder);

  async function fetchResults(q: string) {
    loading = true;
    try {
      const res = await api.materials.search({ search: q, limit: 30 });
      results = res.items;
      total   = res.total;
    } catch {
      results = [];
    } finally {
      loading = false;
    }
  }

  function onQueryInput(e: Event) {
    query = (e.target as HTMLInputElement).value;
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => fetchResults(query), 200);
  }

  function openDropdown() {
    open = true;
    fetchResults(query);
  }

  function closeDropdown() {
    open = false;
  }

  function selectItem(mat: MaterialSummary) {
    if (!multiSelect) {
      displayName = `${mat.id} — ${mat.name}`;
      onChange(mat.id);
      open = false;
    }
  }

  function toggleItem(mat: MaterialSummary) {
    const next = new Set(checked);
    if (next.has(mat.id)) next.delete(mat.id);
    else next.add(mat.id);
    onChange([...next].join(', '));
  }

  onMount(() => {
    if (value) displayName = value;
  });
</script>

<div class="material-select-root">
  {#if !open}
    <button
      class="select-trigger"
      onclick={openDropdown}
      type="button"
    >
      <span class="trigger-text" class:placeholder={!value && !multiSelect}>
        {multiSelect ? (checked.size > 0 ? `${checked.size} selected` : placeholder) : displayName}
      </span>
      <svg class="chevron" viewBox="0 0 16 16" fill="currentColor">
        <path d="M4.22 6.22a.75.75 0 011.06 0L8 8.94l2.72-2.72a.75.75 0 111.06 1.06l-3.25 3.25a.75.75 0 01-1.06 0L4.22 7.28a.75.75 0 010-1.06z"/>
      </svg>
    </button>
  {:else}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="overlay" onclick={closeDropdown}></div>
    <div class="dropdown">
      <div class="search-row">
        <input
          class="search-input"
          type="text"
          value={query}
          oninput={onQueryInput}
          placeholder="Search by name, id, nuclide…"
          autofocus
        />
        <button class="close-search" onclick={closeDropdown} type="button">✕</button>
      </div>

      <div class="results-list">
        {#if loading}
          <div class="results-hint">Searching…</div>
        {:else if results.length === 0}
          <div class="results-hint">No materials found.</div>
        {:else}
          {#each results as mat}
            {#if multiSelect}
              <label class="result-row">
                <input
                  type="checkbox"
                  checked={checked.has(mat.id)}
                  onchange={() => toggleItem(mat)}
                />
                <span class="mat-id">{mat.id}</span>
                <span class="mat-name">{mat.name}</span>
                {#if mat.density}
                  <span class="mat-density">{mat.density} g/cm³</span>
                {/if}
              </label>
            {:else}
              <!-- svelte-ignore a11y_click_events_have_key_events -->
              <!-- svelte-ignore a11y_no_static_element_interactions -->
              <div
                class="result-row"
                class:selected={mat.id === value}
                onclick={() => selectItem(mat)}
              >
                <span class="mat-id">{mat.id}</span>
                <span class="mat-name">{mat.name}</span>
                {#if mat.density}
                  <span class="mat-density">{mat.density} g/cm³</span>
                {/if}
              </div>
            {/if}
          {/each}
          {#if total > results.length}
            <div class="results-hint">{total - results.length} more — refine search</div>
          {/if}
        {/if}
      </div>
    </div>
  {/if}
</div>

<style>
  .material-select-root {
    position: relative;
    width: 100%;
  }

  .select-trigger {
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    border-radius: 5px;
    color: var(--color-text);
    font-family: var(--font-mono);
    font-size: 12px;
    padding: 5px 8px;
    cursor: pointer;
    text-align: left;
  }

  .select-trigger:focus { outline: none; border-color: var(--color-accent); }

  .trigger-text { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .trigger-text.placeholder { color: var(--color-subtext); }

  .chevron { width: 14px; height: 14px; color: var(--color-subtext); flex-shrink: 0; }

  .overlay {
    position: fixed;
    inset: 0;
    z-index: 199;
  }

  .dropdown {
    position: absolute;
    top: calc(100% + 3px);
    left: 0;
    right: 0;
    background: var(--color-bg-panel);
    border: 1px solid var(--color-border);
    border-radius: 6px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.45);
    z-index: 200;
    overflow: hidden;
  }

  .search-row {
    display: flex;
    align-items: center;
    padding: 6px;
    border-bottom: 1px solid var(--color-border);
    gap: 4px;
  }

  .search-input {
    flex: 1;
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    border-radius: 4px;
    color: var(--color-text);
    font-family: var(--font-sans);
    font-size: 12px;
    padding: 4px 8px;
  }

  .search-input:focus { outline: none; border-color: var(--color-accent); }

  .close-search {
    background: transparent;
    border: none;
    color: var(--color-subtext);
    cursor: pointer;
    font-size: 12px;
    padding: 2px 4px;
  }

  .results-list {
    max-height: 220px;
    overflow-y: auto;
  }

  .results-hint {
    font-size: 11px;
    color: var(--color-subtext);
    text-align: center;
    padding: 10px;
    opacity: 0.7;
  }

  .result-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 10px;
    cursor: pointer;
    font-size: 12px;
    transition: background 0.1s;
  }

  .result-row:hover { background: var(--color-bg-raised); }
  .result-row.selected { background: rgba(6, 182, 212, 0.12); }

  label.result-row { cursor: pointer; }
  label.result-row input { accent-color: var(--color-accent); }

  .mat-id {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-accent-hi);
    flex-shrink: 0;
    min-width: 60px;
  }

  .mat-name {
    flex: 1;
    color: var(--color-text);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .mat-density {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-subtext);
    flex-shrink: 0;
  }
</style>