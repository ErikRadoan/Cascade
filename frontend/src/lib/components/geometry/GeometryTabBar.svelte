<script lang="ts">
  // GeometryTabBar — the row of geometry project tabs, IDE-style.
  // Sits above the three-panel GeometryEditor layout.

  import {
    projects,
    switchProject,
    newProject,
    closeProject,
    renameProject,
    openExistingProject,
    deleteProjectPermanently,
  } from './stores/projects.svelte';
  import * as api from '$lib/api';

  let renamingId = $state<string | null>(null);
  let renameValue = $state('');

  let showOpenMenu = $state(false);
  let existingGeometries = $state<{ id: string; name: string }[]>([]);
  let loadingExisting = $state(false);

  function startRename(id: string, currentName: string, e: MouseEvent) {
    e.stopPropagation();
    renamingId = id;
    renameValue = currentName;
  }

  function commitRename() {
    if (renamingId) renameProject(renamingId, renameValue);
    renamingId = null;
  }

  function onRenameKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') commitRename();
    if (e.key === 'Escape') renamingId = null;
  }

  function onCloseTab(id: string, e: MouseEvent) {
    e.stopPropagation();
    closeProject(id);
  }

  async function openMenu() {
    showOpenMenu = true;
    loadingExisting = true;
    try {
      existingGeometries = await api.geometry.list();
    } catch {
      existingGeometries = [];
    } finally {
      loadingExisting = false;
    }
  }

  async function pickExisting(id: string) {
    showOpenMenu = false;
    await openExistingProject(id);
  }

  function closeOpenMenu() {
    showOpenMenu = false;
  }

  let deletingId = $state<string | null>(null);

  async function onDeleteExisting(e: MouseEvent, id: string, name: string) {
    e.stopPropagation(); // don't trigger pickExisting
    if (!confirm(`Delete geometry "${name}"? This cannot be undone.`)) return;
    deletingId = id;
    try {
      await deleteProjectPermanently(id);
      existingGeometries = existingGeometries.filter(g => g.id !== id);
    } finally {
      deletingId = null;
    }
  }
</script>

<div class="tab-bar">
  <div class="tabs">
    {#each projects.list as project (project.id)}
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div
        class="tab"
        class:active={projects.activeId === project.id}
        onclick={() => switchProject(project.id)}
        ondblclick={(e) => startRename(project.id, project.name, e)}
        tabindex="0"
        role="tab"
        aria-selected={projects.activeId === project.id}
      >
        {#if renamingId === project.id}
          <input
            class="rename-input"
            bind:value={renameValue}
            onblur={commitRename}
            onkeydown={onRenameKeydown}
            onclick={(e) => e.stopPropagation()}
            autofocus
          />
        {:else}
          <span class="tab-name">{project.name}</span>
        {/if}

        {#if project.isDirty}
          <span class="dirty-mark" title="Unsaved changes"></span>
        {/if}

        {#if projects.list.length > 1}
          <button
            class="close-btn"
            title="Close tab"
            aria-label="Close tab"
            onclick={(e) => onCloseTab(project.id, e)}
          >
            <svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="square">
              <path d="M2.4 2.4l7.2 7.2M9.6 2.4l-7.2 7.2" />
            </svg>
          </button>
        {/if}
      </div>
    {/each}
  </div>

  <div class="tab-bar-actions">
    <button class="action-btn" title="New geometry" aria-label="New geometry" onclick={() => newProject()}>
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="square">
        <path d="M8 3v10M3 8h10" />
      </svg>
    </button>
    <span class="action-divider"></span>
    <button class="action-btn" title="Open existing geometry" aria-label="Open existing geometry" onclick={openMenu}>
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="square" stroke-linejoin="miter">
        <path d="M1.5 5.5V2h3.4M14.5 5.5V2h-3.4M1.5 10.5V14h3.4M14.5 10.5V14h-3.4" />
        <path d="M8 5.8v4.4M5.8 8h4.4" stroke-width="1.1" />
      </svg>
    </button>
  </div>

  {#if showOpenMenu}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="overlay" onclick={closeOpenMenu}></div>
    <div class="open-menu">
      <div class="open-menu-title">Open existing geometry</div>
      {#if loadingExisting}
        <div class="open-menu-empty">Loading…</div>
      {:else if existingGeometries.length === 0}
        <div class="open-menu-empty">No saved geometries yet.</div>
      {:else}
        {#each existingGeometries as g}
          <div class="open-menu-row">
            <button class="open-menu-item" onclick={() => pickExisting(g.id)}>
              <span class="open-menu-item-mark"></span>
              {g.name}
            </button>
            <button
              class="open-menu-delete"
              title="Delete geometry"
              aria-label="Delete geometry"
              disabled={deletingId === g.id}
              onclick={(e) => onDeleteExisting(e, g.id, g.name)}
            >
              <svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.3">
                <path d="M2 3.5h8M4.5 3.5V2.2h3v1.3M4.8 5.5v3.4M7.2 5.5v3.4M3 3.5l.6 5.8h4.8l.6-5.8" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </button>
          </div>
        {/each}
      {/if}
    </div>
  {/if}
</div>

<style>
  .tab-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: var(--color-bg-deep);
    border-bottom: 1px solid var(--color-border);
    height: 36px;
    flex-shrink: 0;
    position: relative;
  }

  .tabs {
    display: flex;
    align-items: flex-end;
    height: 100%;
    overflow-x: auto;
    overflow-y: hidden;
  }

  .tab {
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 0 11px 0 12px;
    height: calc(100% - 1px);
    cursor: pointer;
    border-right: 1px solid var(--color-border);
    color: var(--color-subtext);
    font-size: 12px;
    white-space: nowrap;
    position: relative;
    /* chamfered top-right corner — a small, deliberate geometric cut,
       echoing the app's own subject matter in the chrome itself. */
    clip-path: polygon(0 0, calc(100% - 9px) 0, 100% 9px, 100% 100%, 0 100%);
    transition: background-color 0.1s ease, color 0.1s ease;
  }

  .tab:hover {
    background: var(--color-bg-panel);
    color: var(--color-text);
  }

  .tab:focus-visible {
    outline: 1px solid var(--color-accent);
    outline-offset: -1px;
  }

  .tab.active {
    background: var(--color-bg-panel);
    color: var(--color-accent-hi);
    box-shadow: inset 0 -2px 0 var(--color-accent);
  }

  .tab-name {
    font-family: var(--font-mono);
    pointer-events: none;
  }

  .rename-input {
    font-family: var(--font-mono);
    font-size: 12px;
    background: var(--color-bg-raised);
    border: 1px solid var(--color-accent);
    border-radius: 2px;
    color: var(--color-text);
    padding: 1px 4px;
    width: 100px;
  }

  /* Unsaved-changes marker: a small rotated square rather than a dot,
     matching the tab bar's chamfered / faceted vocabulary. */
  .dirty-mark {
    width: 6px;
    height: 6px;
    background: var(--color-accent);
    transform: rotate(45deg);
    flex-shrink: 0;
  }

  .close-btn {
    width: 16px;
    height: 16px;
    border: none;
    background: transparent;
    color: var(--color-subtext);
    border-radius: 2px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }

  .close-btn svg { width: 9px; height: 9px; }
  .close-btn:hover { background: var(--color-bg-raised); color: var(--color-text); }
  .close-btn:focus-visible { outline: 1px solid var(--color-accent); outline-offset: 1px; }

  .tab-bar-actions {
    display: flex;
    align-items: center;
    gap: 3px;
    padding: 0 8px;
    flex-shrink: 0;
  }

  .action-btn {
    width: 26px;
    height: 26px;
    border: none;
    background: transparent;
    color: var(--color-subtext);
    border-radius: 3px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .action-btn svg { width: 15px; height: 15px; }
  .action-btn:hover { color: var(--color-accent-hi); background: var(--color-bg-raised); }
  .action-btn:focus-visible { outline: 1px solid var(--color-accent); outline-offset: 1px; }

  .action-divider {
    width: 1px;
    height: 14px;
    background: var(--color-border);
    flex-shrink: 0;
  }

  .overlay {
    position: fixed;
    inset: 0;
    z-index: 99;
  }

  .open-menu {
    position: absolute;
    top: calc(100% + 2px);
    right: 6px;
    min-width: 220px;
    max-height: 280px;
    overflow-y: auto;
    background: var(--color-bg-panel);
    border: 1px solid var(--color-border);
    padding: 4px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
    z-index: 100;
    /* Same chamfer language as the tabs above it, sized up slightly. */
    clip-path: polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%);
  }

  .open-menu-title {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--color-subtext);
    padding: 6px 8px 4px;
  }

  .open-menu-empty {
    font-size: 11px;
    color: var(--color-subtext);
    padding: 10px 8px;
    text-align: center;
    opacity: 0.7;
  }

  .open-menu-item {
    width: 100%;
    text-align: left;
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--color-text);
    background: transparent;
    border: none;
    border-left: 2px solid transparent;
    padding: 6px 8px 6px 6px;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 7px;
  }

  .open-menu-item-mark {
    width: 5px;
    height: 5px;
    background: var(--color-subtext);
    transform: rotate(45deg);
    flex-shrink: 0;
    opacity: 0.6;
  }

  .open-menu-item:hover {
    background: var(--color-bg-raised);
    color: var(--color-accent-hi);
    border-left-color: var(--color-accent);
  }

  .open-menu-item:hover .open-menu-item-mark {
    background: var(--color-accent);
    opacity: 1;
  }

  .open-menu-item:focus-visible {
    outline: 1px solid var(--color-accent);
    outline-offset: -1px;
  }

.open-menu-row {
  display: flex;
  align-items: center;
}
.open-menu-row .open-menu-item { flex: 1; }

.open-menu-delete {
  width: 22px; height: 22px;
  flex-shrink: 0;
  border: none;
  background: transparent;
  color: var(--color-subtext);
  border-radius: 3px;
  cursor: pointer;
  display: flex; align-items: center; justify-content: center;
}
.open-menu-delete:hover { color: #f87171; background: var(--color-bg-raised); }
.open-menu-delete:disabled { opacity: 0.4; cursor: default; }
</style>