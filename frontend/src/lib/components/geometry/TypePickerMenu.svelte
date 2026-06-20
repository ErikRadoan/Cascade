<script lang="ts">
  // Small dropdown menu used by both ObjectPanel and TemplatePanel's
  // "+" button. Lists the available types and fires onPick(type) when
  // the user chooses one. Closes on outside click or Escape.

  let {
    options,
    onPick,
    anchorLabel = 'Add',
  }: {
    options: string[];
    onPick: (type: string) => void;
    anchorLabel?: string;
  } = $props();

  let open = $state(false);

  function pick(type: string) {
    open = false;
    onPick(type);
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') open = false;
  }
</script>

<svelte:window onkeydown={onKeydown} />

<div class="picker-root">
  <button
    class="icon-btn"
    title={anchorLabel}
    aria-label={anchorLabel}
    onclick={() => (open = !open)}
  >
    <svg viewBox="0 0 16 16" fill="currentColor">
      <path d="M8 2a.75.75 0 01.75.75v4.5h4.5a.75.75 0 010 1.5h-4.5v4.5a.75.75 0 01-1.5 0v-4.5h-4.5a.75.75 0 010-1.5h4.5v-4.5A.75.75 0 018 2z"/>
    </svg>
  </button>

  {#if open}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="overlay" onclick={() => (open = false)}></div>
    <ul class="picker-dropdown" role="menu">
      {#each options as opt}
        <li role="none">
          <button class="picker-item" role="menuitem" onclick={() => pick(opt)}>
            {opt}
          </button>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .picker-root {
    position: relative;
  }

  .icon-btn {
    width: 22px;
    height: 22px;
    border: none;
    background: transparent;
    color: var(--color-subtext);
    border-radius: 4px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .icon-btn svg { width: 14px; height: 14px; }
  .icon-btn:hover { color: var(--color-text); background: var(--color-bg-raised); }

  .overlay {
    position: fixed;
    inset: 0;
    z-index: 99;
  }

  .picker-dropdown {
    position: absolute;
    top: calc(100% + 4px);
    right: 0;
    min-width: 160px;
    background: var(--color-bg-panel);
    border: 1px solid var(--color-border);
    border-radius: 6px;
    padding: 4px;
    list-style: none;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
    z-index: 100;
  }

  .picker-item {
    width: 100%;
    text-align: left;
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--color-text);
    background: transparent;
    border: none;
    padding: 6px 10px;
    border-radius: 4px;
    cursor: pointer;
    display: block;
  }

  .picker-item:hover {
    background: var(--color-bg-raised);
    color: var(--color-accent-hi);
  }
</style>