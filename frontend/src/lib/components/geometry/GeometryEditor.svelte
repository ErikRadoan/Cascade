<script lang="ts">
  // GeometryEditor — top-level layout for the geometry tab.
  // Three-column layout: Object/Template panels | Viewport | Parameters panel

  import { onMount } from 'svelte';
  import { editor, setGeometryText } from '$lib/stores/index.svelte';
  import Viewport3D from './Viewport3D.svelte';
  import ObjectPanel from './ObjectPanel.svelte';
  import TemplatePanel from './TemplatePanel.svelte';
  import ParametersPanel from './ParametersPanel.svelte';

  // Load the initial scene once when this tab first mounts.
  // Subsequent edits go through setGeometryText() from wherever they
  // originate (ParametersPanel, raw YAML editor, template creation, etc.)
  // — this component no longer owns the validate/refresh logic.
  onMount(() => {
    if (!editor.scene) {
      setGeometryText(editor.text, { immediate: true });
    }
  });
</script>

<div class="geometry-editor">
  <aside class="left-panels">
    <ObjectPanel />
    <TemplatePanel />
  </aside>

  <section class="viewport-area">
    <Viewport3D scene={editor.scene} isStale={editor.isSceneStale} />
  </section>

  <aside class="right-panel">
    <ParametersPanel />
  </aside>
</div>

<style>
  .geometry-editor {
    display: grid;
    grid-template-columns: var(--panel-left-w) 1fr var(--panel-right-w);
    height: 100%;
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