<script lang="ts">
  // DockTabGroup — a dock leaf's content area, plus the wiring that lets
  // each hosted panel's own PanelHeader act as this group's tab strip.
  //
  // There's no separate tab-strip element here anymore — that duplicated
  // chrome each panel already had. Instead this component exposes a
  // DockPanelContext (dockPanelContext.ts) that PanelHeader reads: drag
  // source, tabify-drop target, and (when >1 panel is tabbed together) the
  // inline tab switcher. This component only still owns the content-area
  // edge zones — dragging onto the 25% edges of a pane splits it.

  import { setContext } from 'svelte';
  import type { DockLeaf, DropZone } from './dockTypes';
  import { PANEL_REGISTRY } from '../panels/panelRegistry';
  import { dockDrag, setActiveTab, moveTabToGroup, splitWithTab } from './dockStore.svelte.js';
  import { DOCK_PANEL_CONTEXT_KEY, type DockPanelContext } from './dockPanelContext';

  let { leaf }: { leaf: DockLeaf } = $props();

  let contentEl: HTMLDivElement;
  let hoverZone = $state<DropZone | null>(null);

  setContext<DockPanelContext>(DOCK_PANEL_CONTEXT_KEY, {
    get tabs() {
      return leaf.tabs;
    },
    get activeTab() {
      return leaf.activeTab;
    },
    titleFor(panelId) {
      return PANEL_REGISTRY[panelId]?.title ?? panelId;
    },
    setActiveTab(panelId) {
      setActiveTab(leaf.id, panelId);
    },
    onDragStart(panelId, e) {
      dockDrag.active = { panelId, sourceLeafId: leaf.id };
      // Firefox requires data to actually be set for the drag to start.
      e.dataTransfer?.setData('text/plain', panelId);
      if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
    },
    onDragEnd() {
      dockDrag.active = null;
    },
    onDropAsTab(_e) {
      if (!dockDrag.active) return;
      moveTabToGroup(dockDrag.active, leaf.id, leaf.tabs.length);
      dockDrag.active = null;
    },
  });

  function computeZone(e: DragEvent): DropZone {
    const rect = contentEl.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    const EDGE = 0.25;
    if (x < EDGE) return 'left';
    if (x > 1 - EDGE) return 'right';
    if (y < EDGE) return 'top';
    if (y > 1 - EDGE) return 'bottom';
    return 'center';
  }

  function onContentDragOver(e: DragEvent) {
    if (!dockDrag.active) return;
    e.preventDefault();
    hoverZone = computeZone(e);
  }

  function onContentDragLeave(e: DragEvent) {
    const related = e.relatedTarget as Node | null;
    if (related && contentEl.contains(related)) return;
    hoverZone = null;
  }

  function onContentDrop(e: DragEvent) {
    if (!dockDrag.active) return;
    e.preventDefault();
    const payload = dockDrag.active;
    const zone = hoverZone ?? 'center';
    if (zone === 'center') {
      moveTabToGroup(payload, leaf.id, leaf.tabs.length);
    } else {
      splitWithTab(payload, leaf.id, zone);
    }
    hoverZone = null;
    dockDrag.active = null;
  }
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="tab-content" bind:this={contentEl} ondragover={onContentDragOver} ondragleave={onContentDragLeave} ondrop={onContentDrop}>
  {#each leaf.tabs as panelId (panelId)}
    {@const Comp = PANEL_REGISTRY[panelId]?.component}
    <div class="tab-pane" hidden={panelId !== leaf.activeTab}>
      {#if Comp}
        <Comp />
      {/if}
    </div>
  {/each}

  {#if hoverZone}
    <div class="snap-overlay snap-{hoverZone}"></div>
  {/if}
</div>

<style>
  .tab-content {
    position: relative;
    width: 100%;
    height: 100%;
    min-width: 0;
    min-height: 0;
    overflow: hidden;
    display: flex;
  }

  .tab-pane {
    flex: 1;
    min-width: 0;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }

  .tab-pane[hidden] {
    display: none;
  }

  .snap-overlay {
    position: absolute;
    background: rgba(6, 182, 212, 0.18);
    border: 2px solid var(--color-accent);
    pointer-events: none;
    z-index: 5;
  }

  .snap-center {
    inset: 6px;
  }

  .snap-left {
    top: 0;
    bottom: 0;
    left: 0;
    width: 50%;
  }

  .snap-right {
    top: 0;
    bottom: 0;
    right: 0;
    width: 50%;
  }

  .snap-top {
    left: 0;
    right: 0;
    top: 0;
    height: 50%;
  }

  .snap-bottom {
    left: 0;
    right: 0;
    bottom: 0;
    height: 50%;
  }
</style>