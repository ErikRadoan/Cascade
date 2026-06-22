<script lang="ts">
  // GeometryEditor — top-level layout for the geometry tab.
  // Tab bar on top, then three-column layout below:
  // Object/Template panels | Viewport | Parameters panel.
  // Everything below the tab bar operates on the ACTIVE project.

  import { onMount } from 'svelte';
  import { projects, activeProject, setGeometryText } from '$lib/stores/index.svelte';
  import GeometryTabBar from './GeometryTabBar.svelte';
  import Viewport3D from './Viewport3D.svelte';
  import ObjectPanel from './ObjectPanel.svelte';
  import TemplatePanel from './TemplatePanel.svelte';
  import ParametersPanel from './ParametersPanel.svelte';

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
    <aside class="left-panels">
      <ObjectPanel />
      <TemplatePanel />
    </aside>

    <section class="viewport-area">
      <Viewport3D scene={activeProject().scene} isStale={activeProject().isSceneStale} />
    </section>

    <aside class="right-panel">
      <ParametersPanel />
    </aside>
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
    display: grid;
    grid-template-columns: var(--panel-left-w) 1fr var(--panel-right-w);
    flex: 1;
    overflow: hidden;
  }

  .left-panels {
    display: flex;
    flex-direction: column;
    border-right: 1px solid var(--color-border);
    overflow: hidden;
  }

  .viewport-area {
    overflow: hidden;
    position: relative;
    background: var(--color-bg-deep);
  }

  .right-panel {
    border-left: 1px solid var(--color-border);
    overflow-y: auto;
    background: var(--color-bg-panel);
  }
</style>