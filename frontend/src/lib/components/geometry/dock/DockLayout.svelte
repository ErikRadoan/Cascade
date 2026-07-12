<script lang="ts">
  // DockLayout — recursively renders a DockNode: a DockSplit becomes a
  // row/column of panes divided by resizable DockSplitters, a DockLeaf
  // becomes a DockTabGroup. This is the whole tree walker; all the actual
  // drag/snap/resize logic lives in dockStore.svelte.ts.

  import type { DockNode } from './dockTypes';
  import { resizeSplit } from './dockStore.svelte.js';
  import DockSplitter from './DockSplitter.svelte';
  import DockTabGroup from './DockTabGroup.svelte';
  import DockLayout from "$lib/components/geometry/dock/DockLayout.svelte";

  let { node }: { node: DockNode } = $props();
</script>

{#if node.type === 'split'}
  <div class="dock-split" class:column={node.direction === 'column'}>
    {#each node.children as child, i (child.id)}
      {#if i > 0}
        <DockSplitter direction={node.direction} onResize={(delta) => resizeSplit(node, i, delta)} />
      {/if}
      <div class="dock-pane" style="flex: {node.sizes[i]} 1 0%;">
        <DockLayout node={child} />
      </div>
    {/each}
  </div>
{:else}
  <DockTabGroup leaf={node} />
{/if}

<style>
  .dock-split {
    display: flex;
    flex-direction: row;
    width: 100%;
    height: 100%;
    min-width: 0;
    min-height: 0;
    overflow: hidden;
  }

  .dock-split.column {
    flex-direction: column;
  }

  .dock-pane {
    min-width: 0;
    min-height: 0;
    display: flex;
    overflow: hidden;
  }
</style>
