<script lang="ts">
  // ParametersPanel — shows editable fields for whatever is selected in
  // the Object or Template panel. Reads the current values from the
  // parsed YAML, writes edits back into the active project's text by patching the
  // corresponding YAML block.
  //
  // This is a thin, schema-agnostic editor: it renders one input per
  // top-level field found in the parsed block for the selected item.
  // It does not know about FuelPin/Box/SinglePlacement specifically —
  // that keeps it correct as new schema types are added on the backend
  // without needing frontend changes.

  import { activeProject, ui, setGeometryText } from '$lib/stores/index.svelte';
  import yaml from './yamlParseHelper';
  import {dump} from 'js-yaml';
  import PanelHeader from './PanelHeader.svelte';
  import { resolveFieldOptions } from './fieldOptions';
  import SweepToggle from './SweepToggle.svelte';
  import MaterialSearchSelect from './MaterialSearchSelect.svelte';

  interface FieldEntry {
    key: string;
    value: string | number | boolean;
    kind: 'string' | 'number' | 'boolean';
    options: string[] | null;
  }

  // Parse the full document and extract the block for the selected item
  let parsedDoc = $derived((): Record<string, Record<string, unknown>> | null => {
    const raw = yaml.parse(activeProject().text);
    if (!raw || typeof raw !== 'object') return null;
    return raw as Record<string, Record<string, unknown>>;
  });

  let selectedBlock = $derived((): Record<string, unknown> | null => {
    const doc = parsedDoc();
    const sel = ui.selectedItem;
    if (!doc || !sel) return null;
    return doc[sel.name] ?? null;
  });

  let fields = $derived((): FieldEntry[] => {
    const block = selectedBlock();
    const doc = parsedDoc();
    if (!block || !doc) return [];
    const compType = (block.type as string) ?? '';

    return Object.entries(block)
      .filter(([k]) => k !== 'type') // type is shown separately, not editable here
      .map(([key, value]) => {
        let kind: FieldEntry['kind'] = 'string';
        if (typeof value === 'number') kind = 'number';
        else if (typeof value === 'boolean') kind = 'boolean';

        // sweep(...) expressions must stay free text regardless of
        // whether the field normally has a dropdown — you can't select
        // a sweep range from a fixed option list.
        const isSweep = typeof value === 'string' && value.trim().startsWith('sweep(');
        const options = isSweep
          ? null
          : resolveFieldOptions(compType, key, doc as Record<string, { type?: string }>);

        return { key, value: value as string | number | boolean, kind, options };
      });
  });

  let componentType = $derived(() => {
    const block = selectedBlock();
    return (block?.type as string) ?? null;
  });

  // Whether the current field value is a sweep(...) expression —
  // these render as text so the user can edit the expression directly
  // rather than losing it to a numeric input or dropdown.
  function isSweepExpression(value: unknown): boolean {
    return typeof value === 'string' && value.trim().startsWith('sweep(');
  }

  // Display-only: "material_id" -> "material id". The raw key is still
  // used for the input's id/name and for writing back to the YAML.
  function formatLabel(key: string): string {
    return key.replace(/_/g, ' ');
  }

  // Fields where the user picked "Custom..." in a dropdown — render as
  // free text instead, so a material/template not in the static option
  // list is never unreachable. Keyed by field key, reset on selection change.
  let customFields = $state<Set<string>>(new Set());

  $effect(() => {
    ui.selectedItem; // dependency — clear custom-entry state on reselect
    customFields = new Set();
  });

  function enterCustomMode(key: string) {
    customFields = new Set([...customFields, key]);
  }

  function updateField(key: string, newValue: string | number | boolean) {
    const doc = parsedDoc();
    const sel = ui.selectedItem;
    if (!doc || !sel || !doc[sel.name]) return;

    doc[sel.name][key] = newValue;

    // Re-serialize the whole document back to YAML.
    // This rewrites formatting/comments — acceptable tradeoff for now
    // since the form is the primary editing surface, not raw text edits.
    const newText = dump(doc, { indent: 2, lineWidth: -1 });

    // Route through the shared handler so validation + scene refresh
    // actually fire. Mutating the project text directly here was the bug —
    // nothing was listening for that mutation.
    setGeometryText(newText);
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

  // Sweep handlers — applying writes a sweep(...) string into the field,
  // exactly like any other value. The expander/sweep.py backend treats
  // it identically whether it came from this UI or hand-typed YAML.
  function onApplySweep(field: FieldEntry, expression: string) {
    updateField(field.key, expression);
  }

  function onClearSweep(field: FieldEntry) {
    // Restore a sensible plain value: the field's default-ish fallback.
    // We don't have the schema default here, so fall back to a neutral
    // value per kind — the user will almost always immediately edit it
    // anyway since removing a sweep implies they want a specific value.
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
    {#if !ui.selectedItem}
      <p class="empty-hint">Select a template or object to edit its parameters.</p>
    {:else if !selectedBlock()}
      <p class="empty-hint">"{ui.selectedItem.name}" not found in the current YAML.</p>
    {:else}
      <div class="selected-header">
        <span class="selected-name">{ui.selectedItem.name}</span>
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
            </label>

            <div class="field-input-row">
              {#if field.kind !== 'boolean'}
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
              {:else if field.key.includes('material') && !isSweepExpression(field.value)}
                <MaterialSearchSelect
                  value={String(field.value)}
                  onChange={(id) => updateField(field.key, id)}
                />
              {:else if isSweepExpression(field.value)}
                <input
                  id="field-{field.key}"
                  type="text"
                  class="field-input sweep-input"
                  value={field.value}
                  onchange={(e) => onInputChange(field, e)}
                />
              {:else if field.options && !customFields.has(field.key)}
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
              {:else}
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
</style>