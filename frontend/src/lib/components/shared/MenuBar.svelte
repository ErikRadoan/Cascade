<script lang="ts">
  // Menu bar — File / Edit / View
  // View menu controls panel visibility and can open the YAML editor.

  import MaterialLibraryModal from '$lib/components/geometry/MaterialLibraryModal.svelte';

  let openMenu = $state<string | null>(null);
  let showMaterialLibrary = $state(false);

  const menus: {
    id: string;
    label: string;
    items: ({ label: string; action: () => void; divider?: never } | { divider: true; label?: never; action?: never })[];
  }[] = [
    {
      id: 'file',
      label: 'File',
      items: [
        { label: 'New geometry',    action: () => newGeometry() },
        { label: 'Open...',         action: () => openFile() },
        { label: 'Save',            action: () => saveFile() },
        { divider: true },
        { label: 'Export OpenMC XML', action: () => exportXML('openmc') },
        { label: 'Export Serpent deck', action: () => exportXML('serpent') },
      ],
    },
    {
      id: 'edit',
      label: 'Edit',
      items: [
        { label: 'Undo',  action: () => {} },
        { label: 'Redo',  action: () => {} },
        { divider: true },
        { label: 'Select all', action: () => {} },
        { divider: true },
        { label: 'Material library…', action: () => openMaterialLibrary() },
      ],
    },
    {
      id: 'view',
      label: 'View',
      items: [
        { label: 'Toggle YAML editor',    action: () => toggleYaml() },
        { label: 'Toggle template panel', action: () => toggleTemplates() },
        { label: 'Toggle object panel',   action: () => toggleObjects() },
        { divider: true },
        { label: 'Reset viewport camera', action: () => resetCamera() },
      ],
    },
  ];

  // Placeholder actions — wire up when state is ready
  function newGeometry()       { /* TODO */ closeMenu(); }
  function openFile()          { /* TODO */ closeMenu(); }
  function saveFile()          { /* TODO */ closeMenu(); }
  function exportXML(_: string){ /* TODO */ closeMenu(); }
  function toggleYaml()        { /* TODO: toggle editor panel via ui store */ closeMenu(); }
  function toggleTemplates()   { /* TODO */ closeMenu(); }
  function toggleObjects()     { /* TODO */ closeMenu(); }
  function resetCamera()       { /* TODO: emit event to Viewport3D */ closeMenu(); }
  function openMaterialLibrary() { showMaterialLibrary = true; closeMenu(); }

  function closeMenu() { openMenu = null; }
  function toggleMenu(id: string) {
    openMenu = openMenu === id ? null : id;
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') closeMenu();
  }
</script>

<svelte:window onkeydown={handleKeydown} />

{#if showMaterialLibrary}
  <MaterialLibraryModal onClose={() => showMaterialLibrary = false} />
{/if}

<!-- Click-outside overlay -->
{#if openMenu}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="overlay" onclick={closeMenu}></div>
{/if}

<header class="menubar">
  <span class="app-name">Cascade</span>

  {#each menus as menu}
    <div class="menu-root">
      <button
        class="menu-trigger"
        class:open={openMenu === menu.id}
        onclick={() => toggleMenu(menu.id)}
      >
        {menu.label}
      </button>

      {#if openMenu === menu.id}
        <ul class="menu-dropdown" role="menu">
          {#each menu.items as item}
            {#if item.divider}
              <li class="menu-divider" role="separator"></li>
            {:else}
              <li role="none">
                <button
                  class="menu-item"
                  role="menuitem"
                  onclick={item.action}
                >
                  {item.label}
                </button>
              </li>
            {/if}
          {/each}
        </ul>
      {/if}
    </div>
  {/each}
</header>

<style>
  .menubar {
    height: var(--menubar-h);
    background: var(--color-bg-panel);
    border-bottom: 1px solid var(--color-border);
    display: flex;
    align-items: center;
    padding: 0 8px;
    gap: 2px;
    flex-shrink: 0;
    position: relative;
    z-index: 100;
    user-select: none;
  }

  .app-name {
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    color: var(--color-accent);
    padding: 0 8px 0 4px;
    margin-right: 4px;
    letter-spacing: 0.05em;
  }

  .menu-root {
    position: relative;
  }

  .menu-trigger {
    font-family: var(--font-sans);
    font-size: 12px;
    color: var(--color-subtext);
    background: transparent;
    border: none;
    padding: 2px 8px;
    border-radius: 4px;
    cursor: pointer;
    height: 22px;
    transition: color 0.1s, background 0.1s;
  }

  .menu-trigger:hover,
  .menu-trigger.open {
    color: var(--color-text);
    background: var(--color-bg-raised);
  }

  .menu-dropdown {
    position: absolute;
    top: calc(100% + 2px);
    left: 0;
    min-width: 200px;
    background: var(--color-bg-panel);
    border: 1px solid var(--color-border);
    border-radius: 6px;
    padding: 4px;
    list-style: none;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
    z-index: 200;
  }

  .menu-item {
    width: 100%;
    text-align: left;
    font-family: var(--font-sans);
    font-size: 12px;
    color: var(--color-text);
    background: transparent;
    border: none;
    padding: 5px 10px;
    border-radius: 4px;
    cursor: pointer;
    display: block;
  }

  .menu-item:hover {
    background: var(--color-bg-raised);
    color: var(--color-accent-hi);
  }

  .menu-divider {
    height: 1px;
    background: var(--color-border);
    margin: 4px 0;
  }

  .overlay {
    position: fixed;
    inset: 0;
    z-index: 99;
  }
</style>