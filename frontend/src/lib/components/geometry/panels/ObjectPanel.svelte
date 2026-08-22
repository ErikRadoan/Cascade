<script lang="ts">
  // ObjectPanel — Phase D of geometry-restructuring-plan.md.
  //
  // Lists /geometry/csg cells grouped by owning placement. Supports
  // multi-select (ctrl/cmd-click) and BooleanPlacement creation from
  // two or more selected placements.

  import { activeProject, setGeometryText } from '../stores/projects.svelte.js';
  import { geometrySelection } from '../stores/selection.svelte.js';
  import { isVisible, toggleVisibility } from '../stores/visibility.svelte.js';
  import { csgState, requestCsgRefresh } from '../stores/csg.svelte.js';
  import { baseGroupName } from '../csgCellGrouping';
  import yaml from '../yamlParseHelper';
  import {dump} from 'js-yaml';
  import PanelHeader from '../dock/PanelHeader.svelte';
  import TypePickerMenu from '../TypePickerMenu.svelte';
  import { PLACEMENT_DEFAULTS, uniqueName } from '../componentDefaults';

  $effect(() => {
    requestCsgRefresh(activeProject().text);
  });

  interface Group {
    name: string;
    count: number;
    type: string;
  }

  const NON_TEMPLATE_TYPES = new Set([
    'SinglePlacement',
    'SquareLattice',
    'HexLattice',
    'BooleanPlacement',
  ]);

  // Placement types offered by "Place template" — exclude BooleanPlacement
  // (that is created via multi-select → Boolean, not from a template).
  const PLACE_TEMPLATE_TYPES = ['SinglePlacement', 'SquareLattice', 'HexLattice'];

  let parsedDoc = $derived((): Record<string, { type?: string; children?: string[] }> | null => {
    const raw = yaml.parse(activeProject().text);
    if (!raw || typeof raw !== 'object') return null;
    return raw as Record<string, { type?: string; children?: string[] }>;
  });

  const PLACEMENT_TYPE_SET = new Set([
    'SinglePlacement',
    'SquareLattice',
    'HexLattice',
    'BooleanPlacement',
  ]);

  /**
   * Object list is driven primarily by the YAML document so the hierarchy
   * remains selectable even when CSG expansion fails. Cell counts come from
   * CSG data when available.
   */
  let groups = $derived((): Group[] => {
    const cells = csgState.data?.cells ?? [];
    const doc = parsedDoc();
    const map = new Map<string, Group>();

    // 1. Every placement defined in YAML (works even when CSG errors)
    if (doc) {
      for (const [name, block] of Object.entries(doc)) {
        if (!block?.type || !PLACEMENT_TYPE_SET.has(block.type)) continue;
        map.set(name, { name, count: 0, type: block.type });
      }
    }

    // 2. Fold CSG cells into counts / add any leftover Cell-only names
    for (const cell of cells) {
      if (cell.material_id == null) continue;
      const rawName = cell.name ?? cell.id;
      const groupName = baseGroupName(rawName);
      const existing = map.get(groupName);
      if (existing) {
        existing.count++;
        continue;
      }
      map.set(groupName, {
        name:  groupName,
        count: 1,
        type:  doc?.[groupName]?.type ?? 'Cell',
      });
    }

    return [...map.values()];
  });

  let availableTemplates = $derived((): string[] => {
    const doc = parsedDoc();
    if (!doc) return [];
    return Object.entries(doc)
      .filter(([, v]) => v && typeof v === 'object' && v.type && !NON_TEMPLATE_TYPES.has(v.type))
      .map(([name]) => name);
  });

  let pendingTemplate = $state<string | null>(null);

  function select(name: string, e?: MouseEvent) {
    const multi = e && (e.ctrlKey || e.metaKey);
    if (multi) {
      const set = new Set(geometrySelection.selectedNames);
      if (set.has(name)) set.delete(name);
      else set.add(name);
      geometrySelection.selectedNames = [...set];
      geometrySelection.selectedItem = { kind: 'placement', name };
    } else {
      geometrySelection.selectedNames = [name];
      geometrySelection.selectedItem = { kind: 'placement', name };
    }
  }

  function isSelected(name: string): boolean {
    return geometrySelection.selectedNames.includes(name)
      || (geometrySelection.selectedItem?.kind === 'placement'
          && geometrySelection.selectedItem.name === name);
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
    geometrySelection.selectedItem = { kind: 'placement', name };
    geometrySelection.selectedNames = [name];
    pendingTemplate = null;
  }

  function cancelCreate() {
    pendingTemplate = null;
  }

  function createBoolean(op: 'union' | 'subtraction' | 'intersection') {
    const names = geometrySelection.selectedNames;
    if (names.length < 2) return;
    const doc = parsedDoc() ?? {};
    const base = op === 'union' ? 'union' : op === 'subtraction' ? 'subtraction' : 'intersection';
    const name = uniqueName(base, new Set(Object.keys(doc)));

    const block = {
      ...PLACEMENT_DEFAULTS.BooleanPlacement(''),
      op,
      children: [...names],
      materials: [] as string[],
    };
    const updated = { ...doc, [name]: block };
    const newText = dump(updated, { indent: 2, lineWidth: -1 });

    setGeometryText(newText, { immediate: true });
    geometrySelection.selectedItem = { kind: 'placement', name };
    geometrySelection.selectedNames = [name];
  }

  function deleteSelected() {
    const names = geometrySelection.selectedNames.length > 0
      ? geometrySelection.selectedNames
      : (geometrySelection.selectedItem?.kind === 'placement'
          ? [geometrySelection.selectedItem.name]
          : []);
    const doc = parsedDoc();
    if (!doc || names.length === 0) return;

    const updated = { ...doc };
    for (const n of names) {
      delete updated[n];
      // If a BooleanPlacement listed this as a child, leave the reference —
      // expander will raise a clear error until the user fixes it.
    }
    const newText = dump(updated, { indent: 2, lineWidth: -1 });

    setGeometryText(newText, { immediate: true });
    geometrySelection.selectedItem = null;
    geometrySelection.selectedNames = [];
  }

  function onToggleVisibility(e: MouseEvent, name: string) {
    e.stopPropagation();
    toggleVisibility(name);
  }

  let multiCount = $derived(geometrySelection.selectedNames.length);
</script>

<div class="panel">
  <PanelHeader title="Objects">
    {#if availableTemplates().length === 0}
      <span class="hint-text">create a template first</span>
    {:else}
      <TypePickerMenu options={availableTemplates()} onPick={startCreate} anchorLabel="Place template" />
    {/if}
    <button
      class="icon-btn"
      title="Delete selected object(s)"
      aria-label="Delete selected object(s)"
      disabled={multiCount === 0 && geometrySelection.selectedItem?.kind !== 'placement'}
      onclick={deleteSelected}
    >
      <svg viewBox="0 0 16 16" fill="currentColor">
        <path d="M3.25 8a.75.75 0 01.75-.75h8a.75.75 0 010 1.5H4A.75.75 0 013.25 8z"/>
      </svg>
    </button>
  </PanelHeader>

  {#if pendingTemplate}
    <div class="create-step">
      <span>Place <strong>{pendingTemplate}</strong> as:</span>
      <div class="create-options">
        {#each PLACE_TEMPLATE_TYPES as pt}
          <button class="create-option" onclick={() => finishCreate(pt)}>{pt}</button>
        {/each}
        <button class="create-cancel" onclick={cancelCreate}>Cancel</button>
      </div>
    </div>
  {/if}

  {#if multiCount >= 2}
    <div class="boolean-bar">
      <span class="boolean-label">{multiCount} selected — Boolean:</span>
      <button class="create-option" onclick={() => createBoolean('union')}>Union</button>
      <button class="create-option" onclick={() => createBoolean('subtraction')}>Subtract</button>
      <button class="create-option" onclick={() => createBoolean('intersection')}>Intersect</button>
    </div>
  {/if}

  <div class="panel-body">
    {#if csgState.error}
      <p class="error-banner" title={csgState.error}>{csgState.error}</p>
    {/if}
    {#if csgState.loading && !csgState.data && groups().length === 0}
      <p class="empty-hint">Loading…</p>
    {:else if groups().length === 0}
      <p class="empty-hint">No objects placed yet.<br>Add a SinglePlacement, lattice, or a Cell.</p>
    {:else}
      {#each groups() as group}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div
          class="object-row"
          class:selected={isSelected(group.name)}
          class:hidden-row={!isVisible(group.name)}
          onclick={(e) => select(group.name, e)}
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
    min-height: 0;
    border: 1px solid var(--color-border);
    box-shadow: 0 1px 0 rgba(0, 0, 0, 0.15);
    transition: box-shadow 0.12s, border-color 0.12s;
  }

  .panel:hover {
    box-shadow: 0 3px 10px rgba(0, 0, 0, 0.28);
  }

  .panel:focus-within {
    border-color: var(--color-accent);
    box-shadow: 0 3px 14px rgba(0, 0, 0, 0.32);
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
    border-radius: 2px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .icon-btn svg { width: 14px; height: 14px; }
  .icon-btn:hover:not(:disabled) { color: var(--color-text); background: var(--color-bg-raised); }
  .icon-btn:disabled { opacity: 0.35; cursor: default; }

  .create-step, .boolean-bar {
    padding: 8px 10px;
    background: rgba(6, 182, 212, 0.06);
    border-bottom: 1px solid var(--color-border);
    font-size: 11px;
    color: var(--color-subtext);
  }

  .boolean-bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
  }

  .boolean-label {
    margin-right: 4px;
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
    border-radius: 2px;
    cursor: pointer;
  }

  .create-option:hover { border-color: var(--color-accent); color: var(--color-accent-hi); }

  .create-cancel {
    font-size: 10px;
    background: transparent;
    border: 1px solid transparent;
    color: var(--color-subtext);
    padding: 4px 8px;
    border-radius: 2px;
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

  .empty-hint.error { color: #f87171; opacity: 1; }

  .error-banner {
    font-size: 10px;
    color: #f87171;
    background: rgba(248, 113, 113, 0.08);
    border-bottom: 1px solid rgba(248, 113, 113, 0.25);
    padding: 6px 10px;
    line-height: 1.4;
    max-height: 4.2em;
    overflow: hidden;
    text-overflow: ellipsis;
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
    border-radius: 2px;
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
