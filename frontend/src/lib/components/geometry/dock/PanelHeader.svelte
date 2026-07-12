<script lang="ts">
  // PanelHeader — shared "title bar" chrome for docked panels, and now
  // also the drag handle / drop target for the dock layout itself.
  //
  // There is deliberately no separate tab-strip component sitting above
  // this — this bar IS the tab strip. When a single panel occupies a dock
  // group, the header looks like it always did (grip + title + actions)
  // and dragging it moves that panel. When multiple panels are tabbed
  // together in the same group, the title is replaced by a small inline
  // switcher — one label per tab — and each label is its own drag source.
  //
  // DockTabGroup provides all of this via context (see dockPanelContext.ts)
  // so ObjectPanel/TemplatePanel/ParametersPanel don't need to change at
  // all; they just render <PanelHeader> as before.

  import type { Snippet } from 'svelte';
  import { getContext } from 'svelte';
  import { DOCK_PANEL_CONTEXT_KEY, type DockPanelContext } from './dockPanelContext';
  import { dockDrag } from './dockStore.svelte.js';

  let { title, children }: { title: string; children?: Snippet } = $props();

  const dock = getContext<DockPanelContext | undefined>(DOCK_PANEL_CONTEXT_KEY);

  let isDropTarget = $state(false);

  // Only draggable as a whole when there's exactly one tab in the group —
  // with multiple tabs it's ambiguous which one "the header" refers to,
  // so each tab label becomes its own drag source instead (below).
  const headerDraggable = $derived(!!dock && dock.tabs.length <= 1);

  function onHeaderDragStart(e: DragEvent) {
    if (!dock) return;
    dock.onDragStart(dock.activeTab, e);
  }

  function onDragOver(e: DragEvent) {
    if (!dock || !dockDrag.active) return;
    e.preventDefault();
    isDropTarget = true;
  }

  function onDragLeave(e: DragEvent) {
    const related = e.relatedTarget as Node | null;
    if (related && (e.currentTarget as HTMLElement).contains(related)) return;
    isDropTarget = false;
  }

  function onDrop(e: DragEvent) {
    if (!dock || !dockDrag.active) return;
    e.preventDefault();
    isDropTarget = false;
    dock.onDropAsTab(e);
  }
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
  class="panel-header"
  class:drop-target={isDropTarget}
  draggable={headerDraggable ? 'true' : 'false'}
  ondragstart={onHeaderDragStart}
  ondragend={dock?.onDragEnd}
  ondragover={onDragOver}
  ondragleave={onDragLeave}
  ondrop={onDrop}
>
  <svg class="grip" viewBox="0 0 10 16" aria-hidden="true">
    <circle cx="2" cy="2" r="1.1" />
    <circle cx="8" cy="2" r="1.1" />
    <circle cx="2" cy="8" r="1.1" />
    <circle cx="8" cy="8" r="1.1" />
    <circle cx="2" cy="14" r="1.1" />
    <circle cx="8" cy="14" r="1.1" />
  </svg>

  {#if dock && dock.tabs.length > 1}
    <div class="panel-tabs">
      {#each dock.tabs as panelId (panelId)}
        <button
          type="button"
          class="panel-tab-label"
          class:active={panelId === dock.activeTab}
          draggable="true"
          ondragstart={(e) => dock.onDragStart(panelId, e)}
          ondragend={dock.onDragEnd}
          onclick={() => dock.setActiveTab(panelId)}
        >
          {dock.titleFor(panelId)}
        </button>
      {/each}
    </div>
  {:else}
    <span class="panel-title">{title}</span>
  {/if}

  <div class="panel-actions" draggable="false">
    {@render children?.()}
  </div>
</div>

<style>
  .panel-header {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 5px 8px 5px 7px;
    background: var(--color-bg-raised);
    border-bottom: 1px solid var(--color-border);
    flex-shrink: 0;
    cursor: grab;
    user-select: none;
    transition: background-color 0.1s;
  }

  .panel-header:active {
    cursor: grabbing;
  }

  .panel-header.drop-target {
    background: rgba(6, 182, 212, 0.14);
  }

  .grip {
    width: 7px;
    height: 14px;
    flex-shrink: 0;
    fill: var(--color-subtext);
    opacity: 0.4;
    transition: opacity 0.1s;
  }

  .panel-header:hover .grip {
    opacity: 0.8;
  }

  .panel-title {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--color-subtext);
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .panel-tabs {
    display: flex;
    align-items: center;
    gap: 3px;
    flex: 1;
    min-width: 0;
    overflow-x: auto;
    scrollbar-width: none;
  }

  .panel-tabs::-webkit-scrollbar {
    display: none;
  }

  .panel-tab-label {
    font-family: var(--font-mono);
    font-size: 10px;
    letter-spacing: 0.04em;
    color: var(--color-subtext);
    background: transparent;
    border: none;
    border-radius: 2px;
    padding: 3px 7px;
    cursor: grab;
    white-space: nowrap;
    flex-shrink: 0;
    transition: background-color 0.1s, color 0.1s;
  }

  .panel-tab-label:active {
    cursor: grabbing;
  }

  .panel-tab-label:hover {
    color: var(--color-text);
    background: var(--color-bg-panel);
  }

  .panel-tab-label.active {
    color: var(--color-accent-hi);
    background: var(--color-bg-panel);
  }

  .panel-actions {
    display: flex;
    gap: 4px;
    align-items: center;
    cursor: default;
  }
</style>