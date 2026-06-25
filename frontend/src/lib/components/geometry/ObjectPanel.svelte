<script lang="ts">
  // ObjectPanel — shows ALL placed objects, including Box placements
  // (boundary boxes are real placements, not a special hidden category).
  // Each row has an eye toggle to show/hide that placement in the
  // viewport — Boxes default to visible but are the most common thing
  // a user will want to hide since they can occlude inner geometry.

  import { activeProject, ui, setGeometryText, isVisible, toggleVisibility } from '$lib/stores/index.svelte';
  import yaml from './yamlParseHelper';
  import {dump} from 'js-yaml';
  import TypePickerMenu from './TypePickerMenu.svelte';
  import { PLACEMENT_DEFAULTS, PLACEMENT_TYPES, uniqueName } from './componentDefaults';
  import type { SceneComponent } from '$lib/types';

  let placements = $derived(activeProject().scene?.components ?? []);

  interface Group {
    name: string;
    count: number;
    type: string;
    firstComponent: SceneComponent;
  }

  let groups = $derived((): Group[] => {
    const map = new Map<string, Group>();
    for (const comp of placements) {
      const baseName = comp.name.replace(/_\d+$/, '');
      if (map.has(baseName)) {
        map.get(baseName)!.count++;
      } else {
        map.set(baseName, { name: baseName, count: 1, type: comp.type, firstComponent: comp });
      }
    }
    return [...map.values()];
  });

  const NON_TEMPLATE_TYPES = new Set(['SinglePlacement', 'SquareLattice', 'HexLattice']);

  let parsedDoc = $derived((): Record<string, { type?: string }> | null => {
    const raw = yaml.parse(activeProject().text);
    if (!raw || typeof raw !== 'object') return null;
    return raw as Record<string, { type?: string }>;
  });

  let availableTemplates = $derived((): string[] => {
    const doc = parsedDoc();
    if (!doc) return [];
    return Object.entries(doc)
      .filter(([, v]) => v && typeof v === 'object' && v.type && !NON_TEMPLATE_TYPES.has(v.type))
      .map(([name]) => name);
  });

  let pendingTemplate = $state<string | null>(null);

  function select(name: string) {
    ui.selectedItem = { kind: 'placement', name };
  }

  function startCreate(templateName: string) {
    pendingTemplate = templateName;
  }

  function finishCreate(placementType: string) {
    if (!pendingTemplate) return;
    const doc = parsedDoc() ?? {};
    const baseName = placementType === 'SinglePlacement' ? `${pendingTemplate}_placed` : 'lattice';
    const name = uniqueName(baseName, new Set(Object.keys(doc)));

    const updated = { ...doc, [name]: PLACEMENT_DEFAULTS[placementType](pendingTemplate) };
    const newText = dump(updated, { indent: 2, lineWidth: -1 });

    setGeometryText(newText, { immediate: true });
    ui.selectedItem = { kind: 'placement', name };
    pendingTemplate = null;
  }

  function cancelCreate() {
    pendingTemplate = null;
  }

  function deleteSelected() {
    const sel = ui.selectedItem;
    const doc = parsedDoc();
    if (!sel || sel.kind !== 'placement' || !doc || !(sel.name in doc)) return;

    const updated = { ...doc };
    delete updated[sel.name];
    const newText = dump(updated, { indent: 2, lineWidth: -1 });

    setGeometryText(newText, { immediate: true });
    ui.selectedItem = null;
  }

  function onToggleVisibility(e: MouseEvent, name: string) {
    e.stopPropagation(); // don't also select the row
    toggleVisibility(name);
  }
</script>

<div class="panel">
  <div class="panel-header">
    <span class="panel-title">Objects</span>
    <div class="panel-actions">
      {#if availableTemplates().length === 0}
        <span class="hint-text">create a template first</span>
      {:else}
        <TypePickerMenu options={availableTemplates()} onPick={startCreate} anchorLabel="Place template" />
      {/if}
      <button
        class="icon-btn"
        title="Delete selected object"
        aria-label="Delete selected object"
        disabled={ui.selectedItem?.kind !== 'placement'}
        onclick={deleteSelected}
      >
        <svg viewBox="0 0 16 16" fill="currentColor">
          <path d="M3.25 8a.75.75 0 01.75-.75h8a.75.75 0 010 1.5H4A.75.75 0 013.25 8z"/>
        </svg>
      </button>
    </div>
  </div>

  {#if pendingTemplate}
    <div class="create-step">
      <span>Place <strong>{pendingTemplate}</strong> as:</span>
      <div class="create-options">
        {#each PLACEMENT_TYPES as pt}
          <button class="create-option" onclick={() => finishCreate(pt)}>{pt}</button>
        {/each}
        <button class="create-cancel" onclick={cancelCreate}>Cancel</button>
      </div>
    </div>
  {/if}

  <div class="panel-body">
    {#if groups().length === 0}
      <p class="empty-hint">No objects placed yet.<br>Add a SinglePlacement or lattice.</p>
    {:else}
      {#each groups() as group}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div
          class="object-row"
          class:selected={ui.selectedItem?.name === group.name && ui.selectedItem?.kind === 'placement'}
          class:hidden-row={!isVisible(group.name)}
          onclick={() => select(group.name)}
        >
          <button
            class="eye-btn"
            title={isVisible(group.name) ? 'Hide in preview' : 'Show in preview'}
            aria-label={isVisible(group.name) ? 'Hide in preview' : 'Show in preview'}
            onclick={(e) => onToggleVisibility(e, group.name)}
          >
            {#if isVisible(group.name)}
              <svg viewBox="0 0 16 16" fill="currentColor">
                <path d="M8 3C4.5 3 1.73 5.11.5 8c1.23 2.89 4 5 7.5 5s6.27-2.11 7.5-5c-1.23-2.89-4-5-7.5-5zm0 8.5A3.5 3.5 0 118 4.5a3.5 3.5 0 010 7zM8 6a2 2 0 100 4 2 2 0 000-4z"/>
              </svg>
            {:else}
              <svg viewBox="0 0 16 16" fill="currentColor">
                <path d="M2.28 1.22a.75.75 0 00-1.06 1.06l3.04 3.04C2.6 6.2 1.36 7.46.5 8c1.23 2.89 4 5 7.5 5 1.13 0 2.19-.22 3.14-.62l3.08 3.08a.75.75 0 101.06-1.06L2.28 1.22zM8 11.5a3.48 3.48 0 01-2.45-1.01l1.1-1.1A2 2 0 008 10c.16 0 .32-.02.46-.06l1.1 1.1A3.48 3.48 0 018 11.5zm.94-5.43L7.93 5.06A2 2 0 0110 7.07l-1.06-1zM15.5 8a8.7 8.7 0 01-1.78 2.58l-1.07-1.07A6.8 6.8 0 0013.06 8 6.6 6.6 0 008 4.5c-.4 0-.79.03-1.16.1L5.6 3.36C6.36 3.13 7.16 3 8 3c3.5 0 6.27 2.11 7.5 5z"/>
              </svg>
            {/if}
          </button>

          <span class="type-dot" style="background: var(--color-accent)"></span>

          <span class="object-name">
            {group.name}
            {#if group.count > 1}
              <span class="object-count">({group.count})</span>
            {/if}
          </span>

          <span class="object-type">{group.type}</span>
        </div>
      {/each}
    {/if}
  </div>
</div>

<style>
  .panel {
    display: flex;
    flex-direction: column;
    flex: 1;
    overflow: hidden;
    border-bottom: 1px solid var(--color-border);
    min-height: 0;
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 10px;
    border-bottom: 1px solid var(--color-border);
    flex-shrink: 0;
    gap: 6px;
  }

  .panel-title {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--color-subtext);
  }

  .panel-actions {
    display: flex;
    gap: 4px;
    align-items: center;
  }

  .hint-text {
    font-size: 9px;
    color: var(--color-subtext);
    opacity: 0.6;
    white-space: nowrap;
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
  .icon-btn:hover:not(:disabled) { color: var(--color-text); background: var(--color-bg-raised); }
  .icon-btn:disabled { opacity: 0.35; cursor: default; }

  .create-step {
    padding: 8px 10px;
    background: rgba(6, 182, 212, 0.06);
    border-bottom: 1px solid var(--color-border);
    font-size: 11px;
    color: var(--color-subtext);
  }

  .create-step strong {
    color: var(--color-accent-hi);
    font-family: var(--font-mono);
  }

  .create-options {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 6px;
  }

  .create-option {
    font-family: var(--font-mono);
    font-size: 10px;
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    color: var(--color-text);
    padding: 4px 8px;
    border-radius: 4px;
    cursor: pointer;
  }

  .create-option:hover { border-color: var(--color-accent); color: var(--color-accent-hi); }

  .create-cancel {
    font-size: 10px;
    background: transparent;
    border: 1px solid transparent;
    color: var(--color-subtext);
    padding: 4px 8px;
    border-radius: 4px;
    cursor: pointer;
  }

  .create-cancel:hover { color: var(--color-text); }

  .panel-body {
    flex: 1;
    overflow-y: auto;
    padding: 4px 0;
  }

  .empty-hint {
    font-size: 11px;
    color: var(--color-subtext);
    text-align: center;
    padding: 20px 12px;
    line-height: 1.6;
    opacity: 0.7;
  }

  .object-row {
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 5px 10px;
    cursor: pointer;
    transition: background 0.1s;
    font-size: 12px;
  }

  .object-row:hover { background: var(--color-bg-raised); }
  .object-row.selected { background: rgba(6, 182, 212, 0.1); color: var(--color-accent-hi); }
  .object-row.hidden-row { opacity: 0.45; }

  .eye-btn {
    width: 18px;
    height: 18px;
    flex-shrink: 0;
    border: none;
    background: transparent;
    color: var(--color-subtext);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 3px;
  }

  .eye-btn svg { width: 13px; height: 13px; }
  .eye-btn:hover { color: var(--color-accent-hi); background: var(--color-bg-raised); }

  .type-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    flex-shrink: 0;
    opacity: 0.8;
  }

  .object-name {
    flex: 1;
    font-family: var(--font-mono);
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--color-text);
  }

  .object-count {
    font-size: 10px;
    color: var(--color-subtext);
    margin-left: 3px;
  }

  .object-type {
    font-size: 9px;
    color: var(--color-subtext);
    text-transform: uppercase;
    letter-spacing: 0.03em;
    flex-shrink: 0;
    opacity: 0.7;
  }
</style>