<script lang="ts">
  // PanelHeader — shared "title bar" chrome for docked panels.
  //
  // This exists as its own component for two reasons: it keeps the three
  // panels visually identical without copy-pasted CSS drifting apart, and
  // it gives future drag-to-detach logic a single place to attach to
  // (the grip + header itself are already the drag surface — icon-btn
  // children in the actions slot are excluded so buttons keep working).

  import type { Snippet } from 'svelte';

  let { title, children }: { title: string; children?: Snippet } = $props();
</script>

<div class="panel-header">
  <svg class="grip" viewBox="0 0 10 16" aria-hidden="true">
    <circle cx="2" cy="2" r="1.1" />
    <circle cx="8" cy="2" r="1.1" />
    <circle cx="2" cy="8" r="1.1" />
    <circle cx="8" cy="8" r="1.1" />
    <circle cx="2" cy="14" r="1.1" />
    <circle cx="8" cy="14" r="1.1" />
  </svg>

  <span class="panel-title">{title}</span>

  <div class="panel-actions">
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
  }

  .panel-header:active {
    cursor: grabbing;
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

  .panel-actions {
    display: flex;
    gap: 4px;
    align-items: center;
    cursor: default;
  }
</style>