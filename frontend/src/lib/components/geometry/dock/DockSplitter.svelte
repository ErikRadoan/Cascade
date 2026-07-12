<script lang="ts">
  // DockSplitter — thin drag handle between two panes in a DockSplit.
  // Reports resize deltas as a fraction of the split container's size
  // along its axis; dockStore.resizeSplit() applies them to the two
  // adjacent panes.

  let { direction, onResize }: { direction: 'row' | 'column'; onResize: (deltaFraction: number) => void } = $props();

  let dragging = $state(false);

  function onPointerDown(e: PointerEvent) {
    const handle = e.currentTarget as HTMLElement;
    const container = handle.parentElement as HTMLElement;
    if (!container) return;

    dragging = true;
    handle.setPointerCapture(e.pointerId);
    const rect = container.getBoundingClientRect();
    const totalSize = direction === 'row' ? rect.width : rect.height;
    let last = direction === 'row' ? e.clientX : e.clientY;

    function onMove(ev: PointerEvent) {
      const pos = direction === 'row' ? ev.clientX : ev.clientY;
      const deltaPx = pos - last;
      last = pos;
      if (totalSize > 0) onResize(deltaPx / totalSize);
    }

    function onUp(ev: PointerEvent) {
      dragging = false;
      handle.releasePointerCapture(ev.pointerId);
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    }

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
  class="dock-splitter"
  class:column={direction === 'column'}
  class:dragging
  role="separator"
  aria-orientation={direction === 'row' ? 'vertical' : 'horizontal'}
  tabindex="-1"
  onpointerdown={onPointerDown}
></div>

<style>
  .dock-splitter {
    position: relative;
    flex: 0 0 5px;
    cursor: col-resize;
    background: transparent;
  }

  .dock-splitter.column {
    cursor: row-resize;
  }

  .dock-splitter::before {
    content: '';
    position: absolute;
    top: 0;
    bottom: 0;
    left: 50%;
    width: 1px;
    transform: translateX(-50%);
    background: var(--color-border);
  }

  .dock-splitter.column::before {
    top: 50%;
    bottom: auto;
    left: 0;
    right: 0;
    width: auto;
    height: 1px;
    transform: translateY(-50%);
  }

  .dock-splitter:hover,
  .dock-splitter.dragging {
    background: rgba(6, 182, 212, 0.08);
  }

  .dock-splitter:hover::before,
  .dock-splitter.dragging::before {
    background: var(--color-accent);
  }
</style>
