<script lang="ts">
  // ObjectPanel — placements list with BooleanPlacement folder hierarchy,
  // multi-select → Boolean ops, and inline rename (double-click name / F2).

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
    depth: number;
    parent: string | null;
    childNames: string[];
  }

  const NON_TEMPLATE_TYPES = new Set([
    'SinglePlacement',
    'SquareLattice',
    'HexLattice',
    'BooleanPlacement',
  ]);

  const PLACE_TEMPLATE_TYPES = ['SinglePlacement', 'SquareLattice', 'HexLattice'];

  let parsedDoc = $derived((): Record<string, Record<string, unknown>> | null => {
    const raw = yaml.parse(activeProject().text);
    if (!raw || typeof raw !== 'object') return null;
    return raw as Record<string, Record<string, unknown>>;
  });

  const PLACEMENT_TYPE_SET = new Set([
    'SinglePlacement',
    'SquareLattice',
    'HexLattice',
    'BooleanPlacement',
  ]);

  /** Parent map: childName → BooleanPlacement name */
  let parentOf = $derived((): Map<string, string> => {
    const doc = parsedDoc();
    const map = new Map<string, string>();
    if (!doc) return map;
    for (const [name, block] of Object.entries(doc)) {
      if (block?.type !== 'BooleanPlacement') continue;
      const children = (block.children as string[] | undefined) ?? [];
      for (const c of children) map.set(c, name);
    }
    return map;
  });

  let groups = $derived((): Group[] => {
    const cells = csgState.data?.cells ?? [];
    const doc = parsedDoc();
    const parents = parentOf();
    const map = new Map<string, Group>();

    if (doc) {
      for (const [name, block] of Object.entries(doc)) {
        if (!block?.type || !PLACEMENT_TYPE_SET.has(block.type as string)) continue;
        const children =
          block.type === 'BooleanPlacement'
            ? ((block.children as string[] | undefined) ?? [])
            : [];
        map.set(name, {
          name,
          count: 0,
          type: block.type as string,
          depth: 0,
          parent: parents.get(name) ?? null,
          childNames: [...children],
        });
      }
    }

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
        name: groupName,
        count: 1,
        type: (doc?.[groupName]?.type as string) ?? 'Cell',
        depth: 0,
        parent: parents.get(groupName) ?? null,
        childNames: [],
      });
    }

    // Assign depth from parent chain
    for (const g of map.values()) {
      let d = 0;
      let p = g.parent;
      const seen = new Set<string>();
      while (p && !seen.has(p)) {
        seen.add(p);
        d++;
        p = parents.get(p) ?? null;
      }
      g.depth = d;
    }

    return [...map.values()];
  });

  /** Visible rows: top-level + children of expanded folders */
  let expanded = $state<Set<string>>(new Set());

  function toggleExpand(name: string, e: MouseEvent) {
    e.stopPropagation();
    const next = new Set(expanded);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    expanded = next;
  }

  let visibleGroups = $derived((): Group[] => {
    const all = groups();
    const byName = new Map(all.map((g) => [g.name, g]));
    const out: Group[] = [];
    const emitted = new Set<string>();

    // Emit children immediately under their BooleanPlacement parent (folder
    // order), not in YAML key order — otherwise children defined before the
    // union float to the top of the list when expanded.
    function emitChildren(parentName: string, ancestors: Set<string>) {
      const parent = byName.get(parentName);
      if (!parent || !expanded.has(parentName)) return;
      for (const childName of parent.childNames) {
        if (ancestors.has(childName) || emitted.has(childName)) continue; // cycle / dup guard
        const child = byName.get(childName);
        if (!child) continue;
        emitted.add(childName);
        out.push(child);
        const nextAnc = new Set(ancestors);
        nextAnc.add(childName);
        emitChildren(childName, nextAnc);
      }
    }

    for (const g of all) {
      if (g.parent) continue; // nested under emitChildren
      emitted.add(g.name);
      out.push(g);
      emitChildren(g.name, new Set([g.name]));
    }
    return out;
  });

  let availableTemplates = $derived((): string[] => {
    const doc = parsedDoc();
    if (!doc) return [];
    return Object.entries(doc)
      .filter(([, v]) => v && typeof v === 'object' && v.type && !NON_TEMPLATE_TYPES.has(v.type as string))
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
    const nameSet = new Set(names);

    // Detach selected items from any existing folder so nothing ends up
    // listed under two BooleanPlacements at once.
    const stripped: Record<string, Record<string, unknown>> = {};
    for (const [k, v] of Object.entries(doc)) {
      if (v?.type === 'BooleanPlacement' && Array.isArray(v.children)) {
        const filtered = (v.children as string[]).filter((c) => !nameSet.has(c));
        stripped[k] = filtered.length === v.children.length ? v : { ...v, children: filtered };
      } else {
        stripped[k] = v;
      }
    }

    const block = {
      ...PLACEMENT_DEFAULTS.BooleanPlacement(''),
      op,
      children: [...names],
      materials: [] as string[],
    };
    const updated = { ...stripped, [name]: block };
    const newText = dump(updated, { indent: 2, lineWidth: -1 });

    setGeometryText(newText, { immediate: true });
    geometrySelection.selectedItem = { kind: 'placement', name };
    geometrySelection.selectedNames = [name];
    // Auto-expand the new folder
    expanded = new Set([...expanded, name]);
  }

  function deleteSelected() {
    const names = geometrySelection.selectedNames.length > 0
      ? geometrySelection.selectedNames
      : (geometrySelection.selectedItem?.kind === 'placement'
          ? [geometrySelection.selectedItem.name]
          : []);
    const doc = parsedDoc();
    if (!doc || names.length === 0) return;

    const nameSet = new Set(names);
    // Immutable rebuild — avoid mutating shared objects from the shallow {...doc} copy.
    // Deleting a BooleanPlacement does not cascade-delete its children (they become top-level).
    const updated: Record<string, Record<string, unknown>> = {};
    for (const [k, v] of Object.entries(doc)) {
      if (nameSet.has(k)) continue;
      if (v?.type === 'BooleanPlacement' && Array.isArray(v.children)) {
        const filtered = (v.children as string[]).filter((c) => !nameSet.has(c));
        updated[k] = filtered.length === v.children.length ? v : { ...v, children: filtered };
      } else {
        updated[k] = v;
      }
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

  // ---- Rename ----
  let renaming = $state<string | null>(null);
  let renameDraft = $state('');

  function startRename(name: string, e?: MouseEvent) {
    e?.stopPropagation();
    renaming = name;
    renameDraft = name;
  }

  function commitRename() {
    const oldName = renaming;
    const newName = renameDraft.trim();
    renaming = null;
    if (!oldName || !newName || newName === oldName) return;

    const doc = parsedDoc();
    if (!doc || !(oldName in doc)) return;
    if (newName in doc) return; // collision — ignore

    const updated: Record<string, Record<string, unknown>> = {};
    for (const [k, v] of Object.entries(doc)) {
      const key = k === oldName ? newName : k;
      const block = { ...v };
      // Update references
      if (typeof block.template === 'string' && block.template === oldName) {
        block.template = newName;
      }
      if (Array.isArray(block.children)) {
        block.children = (block.children as string[]).map((c) => (c === oldName ? newName : c));
      }
      if (typeof block.a === 'string' && block.a === oldName) block.a = newName;
      if (typeof block.b === 'string' && block.b === oldName) block.b = newName;
      updated[key] = block;
    }

    const newText = dump(updated, { indent: 2, lineWidth: -1 });
    setGeometryText(newText, { immediate: true });
    geometrySelection.selectedItem = { kind: 'placement', name: newName };
    geometrySelection.selectedNames = geometrySelection.selectedNames.map((n) =>
      n === oldName ? newName : n,
    );
    if (expanded.has(oldName)) {
      const next = new Set(expanded);
      next.delete(oldName);
      next.add(newName);
      expanded = next;
    }
  }

  function onRenameKey(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      e.preventDefault();
      commitRename();
    } else if (e.key === 'Escape') {
      renaming = null;
    }
  }

  function onRowKey(e: KeyboardEvent, name: string) {
    if (e.key === 'F2') {
      e.preventDefault();
      startRename(name);
    }
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
      {#each visibleGroups() as group (group.name)}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div
          class="object-row"
          class:selected={isSelected(group.name)}
          class:hidden-row={!isVisible(group.name)}
          class:is-child={group.depth > 0}
          style="padding-left: {10 + group.depth * 14}px"
          onclick={(e) => select(group.name, e)}
          onkeydown={(e) => onRowKey(e, group.name)}
          tabindex="0"
        >
          {#if group.type === 'BooleanPlacement' && group.childNames.length > 0}
            <button
              class="chevron-btn"
              class:open={expanded.has(group.name)}
              title={expanded.has(group.name) ? 'Collapse' : 'Expand'}
              aria-label={expanded.has(group.name) ? 'Collapse' : 'Expand'}
              onclick={(e) => toggleExpand(group.name, e)}
            >
              <svg viewBox="0 0 16 16" fill="currentColor">
                <path d="M6.22 4.22a.75.75 0 011.06 0l3.25 3.25a.75.75 0 010 1.06l-3.25 3.25a.75.75 0 01-1.06-1.06L8.94 8 6.22 5.28a.75.75 0 010-1.06z"/>
              </svg>
            </button>
          {:else}
            <span class="chevron-spacer"></span>
          {/if}

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

          {#if renaming === group.name}
            <input
              class="rename-input"
              bind:value={renameDraft}
              onkeydown={onRenameKey}
              onblur={commitRename}
              onclick={(e) => e.stopPropagation()}
              autofocus
            />
          {:else}
            <!-- svelte-ignore a11y_no_static_element_interactions -->
            <span
              class="object-name"
              ondblclick={(e) => startRename(group.name, e)}
              title="Double-click or F2 to rename"
            >
              {group.name}
              {#if group.count > 1}
                <span class="object-count">({group.count})</span>
              {/if}
            </span>
          {/if}

          <span class="object-type">{group.type === 'BooleanPlacement' ? 'folder' : group.type}</span>
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

  .boolean-label { margin-right: 4px; }

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
    gap: 5px;
    padding: 5px 10px;
    cursor: pointer;
    transition: background 0.1s;
    font-size: 12px;
  }

  .object-row:hover { background: var(--color-bg-raised); }
  .object-row.selected { background: rgba(6, 182, 212, 0.1); color: var(--color-accent-hi); }
  .object-row.hidden-row { opacity: 0.45; }
  .object-row.is-child { opacity: 0.95; }

  .chevron-btn {
    width: 16px;
    height: 16px;
    flex-shrink: 0;
    border: none;
    background: transparent;
    color: var(--color-subtext);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 2px;
    padding: 0;
    transition: transform 0.12s;
  }
  .chevron-btn svg { width: 12px; height: 12px; }
  .chevron-btn.open { transform: rotate(90deg); }
  .chevron-btn:hover { color: var(--color-accent-hi); background: var(--color-bg-raised); }

  .chevron-spacer {
    width: 16px;
    height: 16px;
    flex-shrink: 0;
  }

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
    min-width: 0;
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

  .rename-input {
    flex: 1;
    min-width: 0;
    font-family: var(--font-mono);
    font-size: 12px;
    background: var(--color-bg-raised);
    border: 1px solid var(--color-accent);
    border-radius: 2px;
    color: var(--color-text);
    padding: 2px 6px;
  }
  .rename-input:focus { outline: none; }
</style>
