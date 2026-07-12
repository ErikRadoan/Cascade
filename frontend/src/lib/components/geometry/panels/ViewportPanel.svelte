<script lang="ts">
  // ViewportPanel — adapts Viewport3D (which takes scene/isStale as props)
  // into a no-props panel component, so it can live in PANEL_REGISTRY
  // alongside the other panels that already pull their own data from the
  // stores directly.

  import Viewport3D from './Viewport3D.svelte';
  import { activeProject } from '../stores/projects.svelte.js';
</script>

<div class="viewport-panel">
  <Viewport3D scene={activeProject().scene} isStale={activeProject().isSceneStale} />
</div>

<style>
  .viewport-panel {
    flex: 1;
    min-width: 0;
    min-height: 0;
    position: relative;
    background: var(--color-bg-deep);
  }
</style>