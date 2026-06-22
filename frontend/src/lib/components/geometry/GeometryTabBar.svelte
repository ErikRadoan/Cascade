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
  } from '$lib/stores/index.svelte';
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
          <span class="dirty-dot" title="Unsaved changes"></span>
        {/if}

        {#if projects.list.length > 1}
          <button
            class="close-btn"
            title="Close tab"
            aria-label="Close tab"
            onclick={(e) => onCloseTab(project.id, e)}
          >
            <svg viewBox="0 0 12 12" fill="currentColor">
              <path d="M2.22 2.22a.75.75 0 011.06 0L6 4.94l2.72-2.72a.75.75 0 111.06 1.06L7.06 6l2.72 2.72a.75.75 0 11-1.06 1.06L6 7.06 3.28 9.78a.75.75 0 01-1.06-1.06L4.94 6 2.22 3.28a.75.75 0 010-1.06z"/>
            </svg>
          </button>
        {/if}
      </div>
    {/each}
  </div>

  <div class="tab-bar-actions">
    <button class="add-btn" title="New geometry" aria-label="New geometry" onclick={() => newProject()}>
      <svg viewBox="0 0 16 16" fill="currentColor">
        <path d="M8 2a.75.75 0 01.75.75v4.5h4.5a.75.75 0 010 1.5h-4.5v4.5a.75.75 0 01-1.5 0v-4.5h-4.5a.75.75 0 010-1.5h4.5v-4.5A.75.75 0 018 2z"/>
      </svg>
    </button>
    <button class="add-btn" title="Open existing geometry" aria-label="Open existing geometry" onclick={openMenu}>
      <svg viewBox="0 0 16 16" fill="currentColor">
        <path d="M1.75 3A1.75 1.75 0 000 4.75v6.5C0 12.216.784 13 1.75 13h12.5A1.75 1.75 0 0016 11.25v-5A1.75 1.75 0 0014.25 4.5H7.5l-1.7-1.7A.75.75 0 005.25 2.5H1.75z"/>
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
          <button class="open-menu-item" onclick={() => pickExisting(g.id)}>
            {g.name}
          </button>
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
    height: 32px;
    flex-shrink: 0;
    position: relative;
  }

  .tabs {
    display: flex;
    height: 100%;
    overflow-x: auto;
  }

  .tab {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 0 10px;
    height: 100%;
    cursor: pointer;
    border-right: 1px solid var(--color-border);
    color: var(--color-subtext);
    font-size: 12px;
    white-space: nowrap;
    position: relative;
  }

  .tab:hover {
    background: var(--color-bg-panel);
    color: var(--color-text);
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
    border-radius: 3px;
    color: var(--color-text);
    padding: 1px 4px;
    width: 100px;
  }

  .dirty-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--color-accent);
    flex-shrink: 0;
  }

  .close-btn {
    width: 16px;
    height: 16px;
    border: none;
    background: transparent;
    color: var(--color-subtext);
    border-radius: 3px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }

  .close-btn svg { width: 10px; height: 10px; }
  .close-btn:hover { background: var(--color-bg-raised); color: var(--color-text); }

  .tab-bar-actions {
    display: flex;
    gap: 2px;
    padding: 0 6px;
    flex-shrink: 0;
  }

  .add-btn {
    width: 24px;
    height: 24px;
    border: none;
    background: transparent;
    color: var(--color-subtext);
    border-radius: 4px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .add-btn svg { width: 14px; height: 14px; }
  .add-btn:hover { color: var(--color-accent-hi); background: var(--color-bg-raised); }

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
    border-radius: 6px;
    padding: 4px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
    z-index: 100;
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
    padding: 6px 8px;
    border-radius: 4px;
    cursor: pointer;
    display: block;
  }

  .open-menu-item:hover {
    background: var(--color-bg-raised);
    color: var(--color-accent-hi);
  }
</style>