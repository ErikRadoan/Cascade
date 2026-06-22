<script lang="ts">
  // SweepToggle — the small "%xy" button shown to the left of every
  // sweepable field in ParametersPanel. Clicking it opens a popover:
  //   - numeric fields  -> range mode (start / stop / step)
  //   - dropdown fields -> discrete list mode (checklist of options)
  //
  // Emits the raw sweep(...) string via onApply, or null via onClear
  // to remove the sweep and go back to a plain value.

  import MaterialSearchSelect from './MaterialSearchSelect.svelte';

  let {
    fieldKey,
    isActive,
    isNumeric,
    options,          // non-null only for dropdown-backed fields
    currentValue,     // current plain value, used to seed sensible defaults
    onApply,
    onClear,
  }: {
    fieldKey: string;
    isActive: boolean;
    isNumeric: boolean;
    options: string[] | null;
    currentValue: string | number;
    onApply: (sweepExpression: string) => void;
    onClear: () => void;
  } = $props();

  let open = $state(false);

  // Range mode state
  let rangeStart = $state(typeof currentValue === 'number' ? currentValue : 0);
  let rangeStop  = $state(typeof currentValue === 'number' ? currentValue + 1 : 1);
  let rangeStep  = $state(1);

  // List mode state — which options are checked
  let checked = $state<Set<string>>(new Set(options ? [String(currentValue)] : []));

  function togglePopover() {
    open = !open;
  }

  function closePopover() {
    open = false;
  }

  function toggleOption(opt: string) {
    const next = new Set(checked);
    if (next.has(opt)) next.delete(opt);
    else next.add(opt);
    checked = next;
  }

  function applyRange() {
    if (rangeStop < rangeStart) return;
    if (rangeStep <= 0) return;
    onApply(`sweep(${rangeStart} to ${rangeStop}, step=${rangeStep})`);
    open = false;
  }

  function applyList() {
    if (checked.size === 0) return;
    onApply(`sweep(${[...checked].join(', ')})`);
    open = false;
  }

  function clearSweep() {
    onClear();
    open = false;
  }
</script>

<div class="sweep-toggle-root">
  <button
    class="sweep-btn"
    class:active={isActive}
    title={isActive ? 'Edit sweep' : 'Sweep this field'}
    aria-label={isActive ? 'Edit sweep' : 'Sweep this field'}
    onclick={togglePopover}
  >
    <!-- Custom glyph: % shape but with x/y instead of the two o's -->
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3">
      <line x1="12.5" y1="3.5" x2="3.5" y2="12.5" stroke-linecap="round"/>
      <!-- top-left "x" -->
      <line x1="2.5" y1="2.5" x2="5.5" y2="5.5" stroke-linecap="round"/>
      <line x1="5.5" y1="2.5" x2="2.5" y2="5.5" stroke-linecap="round"/>
      <!-- bottom-right "y" -->
      <line x1="10.5" y1="10.3" x2="10.5" y2="13.2" stroke-linecap="round"/>
      <line x1="10.5" y1="11.6" x2="13.3" y2="9.8" stroke-linecap="round"/>
    </svg>
  </button>

  {#if open}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="overlay" onclick={closePopover}></div>

    <div class="popover">
      {#if isNumeric}
        <div class="popover-title">Sweep range</div>
        <div class="range-row">
          <label>
            <span>start</span>
            <input type="number" step="any" bind:value={rangeStart} />
          </label>
          <label>
            <span>stop</span>
            <input type="number" step="any" bind:value={rangeStop} />
          </label>
          <label>
            <span>step</span>
            <input type="number" step="any" min="0" bind:value={rangeStep} />
          </label>
        </div>
        <div class="popover-actions">
          {#if isActive}
            <button class="clear-btn" onclick={clearSweep}>Remove sweep</button>
          {/if}
          <button class="apply-btn" onclick={applyRange}>Apply</button>
        </div>
      {:else if options}
        <div class="popover-title">Sweep values</div>
        {#if fieldKey === 'material'}
          <MaterialSearchSelect
            multiSelect={true}
            checked={checked}
            onChange={(joined) => {
              checked = new Set(joined ? joined.split(', ').filter(Boolean) : []);
            }}
          />
        {:else}
        <div class="list-options">
          {#each options as opt}
            <label class="list-option">
              <input
                type="checkbox"
                checked={checked.has(opt)}
                onchange={() => toggleOption(opt)}
              />
              <span>{opt}</span>
            </label>
          {/each}
        </div>
        {/if}
        <div class="popover-actions">
          {#if isActive}
            <button class="clear-btn" onclick={clearSweep}>Remove sweep</button>
          {/if}
          <button class="apply-btn" onclick={applyList}>Apply</button>
        </div>
      {:else}
        <div class="popover-title">This field can't be swept as a free-text value.</div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .sweep-toggle-root {
    position: relative;
    flex-shrink: 0;
  }

  .sweep-btn {
    width: 22px;
    height: 22px;
    border: 1px solid var(--color-border);
    background: var(--color-bg-raised);
    color: var(--color-subtext);
    border-radius: 5px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: border-color 0.1s, color 0.1s;
  }

  .sweep-btn svg {
    width: 13px;
    height: 13px;
  }

  .sweep-btn:hover {
    border-color: var(--color-accent);
    color: var(--color-text);
  }

  .sweep-btn.active {
    border-color: var(--color-accent);
    background: rgba(6, 182, 212, 0.15);
    color: var(--color-accent-hi);
  }

  .overlay {
    position: fixed;
    inset: 0;
    z-index: 99;
  }

  .popover {
    position: absolute;
    top: calc(100% + 4px);
    left: 0;
    min-width: 220px;
    background: var(--color-bg-panel);
    border: 1px solid var(--color-border);
    border-radius: 8px;
    padding: 10px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
    z-index: 100;
  }

  .popover-title {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--color-accent-hi);
    margin-bottom: 8px;
  }

  .range-row {
    display: flex;
    gap: 6px;
  }

  .range-row label {
    display: flex;
    flex-direction: column;
    gap: 3px;
    flex: 1;
  }

  .range-row span {
    font-size: 9px;
    color: var(--color-subtext);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .range-row input {
    width: 100%;
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    border-radius: 4px;
    color: var(--color-text);
    font-family: var(--font-mono);
    font-size: 11px;
    padding: 4px 5px;
  }

  .range-row input:focus {
    outline: none;
    border-color: var(--color-accent);
  }

  .list-options {
    display: flex;
    flex-direction: column;
    gap: 4px;
    max-height: 180px;
    overflow-y: auto;
  }

  .list-option {
    display: flex;
    align-items: center;
    gap: 6px;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text);
    cursor: pointer;
    padding: 3px 4px;
    border-radius: 4px;
  }

  .list-option:hover {
    background: var(--color-bg-raised);
  }

  .list-option input {
    accent-color: var(--color-accent);
  }

  .popover-actions {
    display: flex;
    justify-content: flex-end;
    gap: 6px;
    margin-top: 10px;
  }

  .apply-btn {
    font-size: 11px;
    background: var(--color-accent);
    color: var(--color-bg-deep);
    border: none;
    padding: 5px 12px;
    border-radius: 5px;
    cursor: pointer;
    font-weight: 600;
  }

  .apply-btn:hover {
    background: var(--color-accent-hi);
  }

  .clear-btn {
    font-size: 11px;
    background: transparent;
    color: var(--color-subtext);
    border: 1px solid var(--color-border);
    padding: 5px 10px;
    border-radius: 5px;
    cursor: pointer;
  }

  .clear-btn:hover {
    color: #f87171;
    border-color: #f87171;
  }
</style>