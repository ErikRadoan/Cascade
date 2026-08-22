<script lang="ts">
  // ParametersPanel — shows editable fields for whatever is selected in
  // the Object or Template panel. Reads the current values from the
  // parsed YAML, writes edits back into the active project's text by patching the
  // corresponding YAML block.
  //
  // This is a thin, schema-agnostic editor: it renders one input per
  // top-level field found in the parsed block for the selected item.
  // Special case: BooleanPlacement.materials is a multi-select over the
  // materials reachable from the children (empty = all).

  import { activeProject, setGeometryText } from '../stores/projects.svelte.js';
  import { geometrySelection } from '../stores/selection.svelte.js';
  import yaml from '../yamlParseHelper';
  import {dump} from 'js-yaml';
  import PanelHeader from '../dock/PanelHeader.svelte';
  import { resolveFieldOptions, materialsFromPlacement } from '../fieldOptions';
  import SweepToggle from '../SweepToggle.svelte';
  import MaterialSearchSelect from '../MaterialSearchSelect.svelte';

  interface FieldEntry {
    key: string;
    value: string | number | boolean | string[];
    kind: 'string' | 'number' | 'boolean' | 'string[]';
    options: string[] | null;
  }

  let parsedDoc = $derived((): Record<string, Record<string, unknown>> | null => {
    const raw = yaml.parse(activeProject().text);
    if (!raw || typeof raw !== 'object') return null;
    return raw as Record<string, Record<string, unknown>>;
  });

  let selectedBlock = $derived((): Record<string, unknown> | null => {
    const doc = parsedDoc();
    const sel = geometrySelection.selectedItem;
    if (!doc || !sel) return null;
    return doc[sel.name] ?? null;
  });

  let fields = $derived((): FieldEntry[] => {
    const block = selectedBlock();
    const doc = parsedDoc();
    if (!block || !doc) return [];
    const compType = (block.type as string) ?? '';
    const sel = geometrySelection.selectedItem;

    return Object.entries(block)
      .filter(([k]) => k !== 'type')
      .map(([key, value]) => {
        let kind: FieldEntry['kind'] = 'string';
        if (Array.isArray(value)) kind = 'string[]';
        else if (typeof value === 'number') kind = 'number';
        else if (typeof value === 'boolean') kind = 'boolean';

        const isSweep = typeof value === 'string' && value.trim().startsWith('sweep(');
        let options: string[] | null = isSweep
          ? null
          : resolveFieldOptions(compType, key, doc as Record<string, { type?: string }>);

        // BooleanPlacement.materials: options = materials from children
        if (compType === 'BooleanPlacement' && key === 'materials' && sel) {
          const fromChildren = materialsFromPlacement(sel.name, doc);
          options = fromChildren.length > 0 ? fromChildren : options;
        }

        return {
          key,
          value: value as string | number | boolean | string[],
          kind,
          options,
        };
      });
  });

  let componentType = $derived(() => {
    const block = selectedBlock();
    return (block?.type as string) ?? null;
  });

  function isSweepExpression(value: unknown): boolean {
    return typeof value === 'string' && value.trim().startsWith('sweep(');
  }

  function formatLabel(key: string): string {
    return key.replace(/_/g, ' ');
  }

  let customFields = $state<Set<string>>(new Set());

  $effect(() => {
    geometrySelection.selectedItem;
    customFields = new Set();
  });

  function enterCustomMode(key: string) {
    customFields = new Set([...customFields, key]);
  }

  function updateField(key: string, newValue: string | number | boolean | string[]) {
    const doc = parsedDoc();
    const sel = geometrySelection.selectedItem;
    if (!doc || !sel || !doc[sel.name]) return;

    doc[sel.name][key] = newValue;
    const newText = dump(doc, { indent: 2, lineWidth: -1 });
    setGeometryText(newText);
  }

  function toggleMaterial(field: FieldEntry, mat: string, checked: boolean) {
    const current = Array.isArray(field.value) ? [...field.value] : [];
    // Empty list means "all" — first explicit toggle starts from full options
    let next: string[];
    if (current.length === 0 && field.options && field.options.length > 0) {
      next = checked
        ? [mat]
        : field.options.filter((m) => m !== mat);
    } else if (checked) {
      next = current.includes(mat) ? current : [...current, mat];
    } else {
      next = current.filter((m) => m !== mat);
    }
    // If user re-selected everything, store [] (= all)
    if (field.options && next.length === field.options.length) {
      next = [];
    }
    updateField(field.key, next);
  }

  function isMaterialChecked(field: FieldEntry, mat: string): boolean {
    if (!Array.isArray(field.value) || field.value.length === 0) return true; // empty = all
    return field.value.includes(mat);
  }

  function onInputChange(field: FieldEntry, e: Event) {
    const target = e.target as HTMLInputElement | HTMLSelectElement;
    if (isSweepExpression(field.value)) {
      updateField(field.key, target.value);
      return;
    }
    if (field.kind === 'number') {
      const n = parseFloat(target.value);
      if (!Number.isNaN(n)) updateField(field.key, n);
    } else if (field.kind === 'boolean') {
      updateField(field.key, (target as HTMLInputElement).checked);
    } else {
      updateField(field.key, target.value);
    }
  }

  function onSelectChange(field: FieldEntry, e: Event) {
    const value = (e.target as HTMLSelectElement).value;
    if (value === '__custom__') {
      enterCustomMode(field.key);
      return;
    }
    updateField(field.key, value);
  }

  function onApplySweep(field: FieldEntry, expression: string) {
    updateField(field.key, expression);
  }

  function onClearSweep(field: FieldEntry) {
    if (field.kind === 'number') {
      updateField(field.key, 0);
    } else if (field.options && field.options.length > 0) {
      updateField(field.key, field.options[0]);
    } else {
      updateField(field.key, '');
    }
  }
</script>

<div class="panel">
  <PanelHeader title="Parameters" />

  <div class="panel-body">
    {#if !geometrySelection.selectedItem}
      <p class="empty-hint">Select a template or object to edit its parameters.</p>
    {:else if !selectedBlock()}
      <p class="empty-hint">"{geometrySelection.selectedItem.name}" not found in the current YAML.</p>
    {:else}
      <div class="selected-header">
        <span class="selected-name">{geometrySelection.selectedItem.name}</span>
        {#if componentType()}
          <span class="selected-type">{componentType()}</span>
        {/if}
      </div>

      <div class="field-list">
        {#each fields() as field (field.key)}
          <div class="field-row">
            <label class="field-label" for="field-{field.key}">
              {formatLabel(field.key)}
              {#if isSweepExpression(field.value)}
                <span class="sweep-badge">sweep</span>
              {/if}
              {#if field.kind === 'string[]' && Array.isArray(field.value) && field.value.length === 0}
                <span class="sweep-badge">all</span>
              {/if}
            </label>

            <div class="field-input-row">
              {#if field.kind === 'string[]'}
                <div class="material-multi" id="field-{field.key}">
                  {#if !field.options || field.options.length === 0}
                    <span class="multi-empty">No materials found on children</span>
                  {:else}
                    {#each field.options as mat}
                      <label class="multi-item">
                        <input
                          type="checkbox"
                          checked={isMaterialChecked(field, mat)}
                          onchange={(e) => toggleMaterial(field, mat, (e.target as HTMLInputElement).checked)}
                        />
                        <span>{mat}</span>
                      </label>
                    {/each}
                  {/if}
                </div>
              {:else if field.kind !== 'boolean'}
                <SweepToggle
                  fieldKey={field.key}
                  isActive={isSweepExpression(field.value)}
                  isNumeric={field.kind === 'number'}
                  options={field.options}
                  currentValue={field.value as string | number}
                  onApply={(expr) => onApplySweep(field, expr)}
                  onClear={() => onClearSweep(field)}
                />
              {/if}

              {#if field.kind === 'boolean'}
                <input
                  id="field-{field.key}"
                  type="checkbox"
                  checked={field.value as boolean}
                  onchange={(e) => onInputChange(field, e)}
                />
              {:else if field.kind !== 'string[]' && field.key.includes('material') && !isSweepExpression(field.value)}
                <MaterialSearchSelect
                  value={String(field.value)}
                  onChange={(id) => updateField(field.key, id)}
                />
              {:else if field.kind !== 'string[]' && isSweepExpression(field.value)}
                <input
                  id="field-{field.key}"
                  type="text"
                  class="field-input sweep-input"
                  value={field.value}
                  onchange={(e) => onInputChange(field, e)}
                />
              {:else if field.kind !== 'string[]' && field.options && !customFields.has(field.key)}
                <div class="select-wrap">
                  <select
                    id="field-{field.key}"
                    class="field-input field-select"
                    value={field.options.includes(String(field.value)) ? field.value : '__custom__'}
                    onchange={(e) => onSelectChange(field, e)}
                  >
                    {#each field.options as opt}
                      <option value={opt}>{opt}</option>
                    {/each}
                    <option value="__custom__">Custom…</option>
                  </select>
                  <svg class="select-chevron" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                    <path d="M4.22 6.22a.75.75 0 011.06 0L8 8.94l2.72-2.72a.75.75 0 111.06 1.06l-3.25 3.25a.75.75 0 01-1.06 0L4.22 7.28a.75.75 0 010-1.06z"/>
                  </svg>
                </div>
              {:else if field.kind === 'number'}
                <input
                  id="field-{field.key}"
                  type="number"
                  step="any"
                  class="field-input"
                  value={field.value}
                  onchange={(e) => onInputChange(field, e)}
                />
              {:else if field.kind !== 'string[]'}
                <input
                  id="field-{field.key}"
                  type="text"
                  class="field-input"
                  value={field.value}
                  onchange={(e) => onInputChange(field, e)}
                  placeholder={field.options ? 'Custom value…' : undefined}
                />
              {/if}
            </div>
          </div>
        {/each}
      </div>
    {/if}
  </div>
</div>

<style>
  .panel {
    display: flex;
    flex-direction: column;
    height: 100%;
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

  .panel-body {
    flex: 1;
    overflow-y: auto;
    padding: 12px;
    background: var(--color-bg-panel);
  }

  .empty-hint {
    font-size: 11px;
    color: var(--color-subtext);
    text-align: center;
    padding: 24px 8px;
    line-height: 1.6;
    opacity: 0.7;
  }

  .selected-header {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin-bottom: 12px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--color-border);
  }

  .selected-name {
    font-family: var(--font-mono);
    font-size: 13px;
    color: var(--color-text);
    font-weight: 600;
  }

  .selected-type {
    font-size: 10px;
    color: var(--color-accent);
    background: rgba(6, 182, 212, 0.12);
    padding: 1px 6px;
    border-radius: 2px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }

  .field-list {
    display: grid;
    grid-template-columns: minmax(70px, 30%) 1fr;
    column-gap: 12px;
  }

  .field-row {
    display: contents;
  }

  .field-label {
    font-size: 11px;
    color: var(--color-subtext);
    font-family: var(--font-mono);
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 7px 0;
    border-bottom: 1px solid var(--color-border);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    box-sizing: border-box;
  }

  .field-input-row {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 5px 0;
    border-bottom: 1px solid var(--color-border);
    min-width: 0;
    box-sizing: border-box;
  }

  .field-input-row .field-input {
    flex: 1;
    min-width: 0;
  }

  .sweep-badge {
    font-size: 9px;
    color: var(--color-accent);
    background: rgba(6, 182, 212, 0.12);
    padding: 1px 5px;
    border-radius: 2px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .field-input {
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    border-radius: 2px;
    color: var(--color-text);
    font-family: var(--font-mono);
    font-size: 12px;
    padding: 5px 8px;
    width: 100%;
  }

  .field-input:focus {
    outline: none;
    border-color: var(--color-accent);
  }

  .sweep-input {
    color: var(--color-accent-hi);
  }

  .select-wrap {
    position: relative;
    flex: 1;
    min-width: 0;
    display: flex;
  }

  .field-select {
    appearance: none;
    padding-right: 24px;
    cursor: pointer;
  }

  .select-chevron {
    position: absolute;
    top: 50%;
    right: 8px;
    width: 12px;
    height: 12px;
    transform: translateY(-50%);
    color: var(--color-subtext);
    pointer-events: none;
  }

  input[type='checkbox'] {
    width: 16px;
    height: 16px;
    accent-color: var(--color-accent);
  }

  .material-multi {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 12px;
    flex: 1;
    padding: 2px 0;
  }

  .multi-item {
    display: flex;
    align-items: center;
    gap: 4px;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text);
    cursor: pointer;
  }

  .multi-empty {
    font-size: 11px;
    color: var(--color-subtext);
    opacity: 0.7;
  }
</style>
