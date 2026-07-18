<script lang="ts">
  // GeometryEditor — top-level layout for the geometry tab.
  // Tab bar on top, then a JetBrains-style dock layout below it: panes can
  // be dragged by their tab into other panes (as a tab) or onto a pane's
  // edge (to split it), and resized via the splitters between them.
  // See dockStore.svelte.ts for the layout tree and its mutations.
  //
  // Everything below the tab bar operates on the ACTIVE project.

  import { onMount } from 'svelte';
  import {activeProject, setGeometryText, restoreProjects} from './stores/projects.svelte';
  import GeometryTabBar from './GeometryTabBar.svelte';
  import DockLayout from './dock/DockLayout.svelte';
  import { dock } from './dock/dockStore.svelte.js';

  onMount(() => {
    restoreProjects();
  });


  // Re-run scene load whenever the active tab changes AND that project
  // has never had its scene loaded yet (switching back to an
  // already-loaded tab shouldn't refetch).
  $effect(() => {
    const p = activeProject();
    if (!p.scene && !p.isLoadingScene && !p.isValidating) {
      setGeometryText(p.text, { immediate: true });
    }
  });
</script>

<div class="geometry-tab">
  <GeometryTabBar />

  <div class="geometry-editor">
    <DockLayout node={dock.layout} />
  </div>
</div>

<style>
  .geometry-tab {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }

  .geometry-editor {
    display: flex;
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }
</style>