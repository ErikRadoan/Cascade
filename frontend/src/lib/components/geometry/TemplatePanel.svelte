<script lang="ts">
  // TemplatePanel — shows template definitions as thumbnail cards.
  // The "+" button now actually creates a new template block in the YAML.

  import { activeProject, ui, setGeometryText } from '$lib/stores/index.svelte';
  import yaml from './yamlParseHelper';
  import {dump} from 'js-yaml';
  import TypePickerMenu from './TypePickerMenu.svelte';
  import { TEMPLATE_DEFAULTS, TEMPLATE_TYPES, uniqueName } from './componentDefaults';

  interface TemplateEntry {
    name: string;
    type: string;
  }

  const PLACEMENT_TYPES = new Set(['SinglePlacement', 'SquareLattice', 'HexLattice']);

  let parsedDoc = $derived((): Record<string, { type?: string }> | null => {
    const raw = yaml.parse(activeProject().text);
    if (!raw || typeof raw !== 'object') return null;
    return raw as Record<string, { type?: string }>;
  });

  let templates = $derived((): TemplateEntry[] => {
    const doc = parsedDoc();
    if (!doc) return [];
    return Object.entries(doc)
      .filter(([, v]) => v && typeof v === 'object' && v.type && !PLACEMENT_TYPES.has(v.type))
      .map(([name, v]) => ({ name, type: v.type! }));
  });

  function select(name: string) {
    ui.selectedItem = { kind: 'template', name };
  }

  function iconFor(type: string): string {
    switch (type) {
      case 'FuelPin': return 'cylinder';
      case 'Box':     return 'cube';
      default:        return 'shape';
    }
  }

  function createTemplate(type: string) {
    const doc = parsedDoc() ?? {};
    const baseName = `my_${type.replace(/([A-Z])/g, (m, c, i) => (i === 0 ? c.toLowerCase() : '_' + c.toLowerCase()))}`;
    const name = uniqueName(baseName, new Set(Object.keys(doc)));

    const updated = { ...doc, [name]: TEMPLATE_DEFAULTS[type] };
    const newText = dump(updated, { indent: 2, lineWidth: -1 });

    setGeometryText(newText, { immediate: true });
    ui.selectedItem = { kind: 'template', name };
  }

  function deleteSelected() {
    const sel = ui.selectedItem;
    const doc = parsedDoc();
    if (!sel || sel.kind !== 'template' || !doc || !(sel.name in doc)) return;

    const updated = { ...doc };
    delete updated[sel.name];
    const newText = dump(updated, { indent: 2, lineWidth: -1 });

    setGeometryText(newText, { immediate: true });
    ui.selectedItem = null;
  }
</script>

<div class="panel">
  <div class="panel-header">
    <span class="panel-title">Templates</span>
    <div class="panel-actions">
      <TypePickerMenu options={TEMPLATE_TYPES} onPick={createTemplate} anchorLabel="New template" />
      <button
        class="icon-btn"
        title="Delete selected template"
        aria-label="Delete selected template"
        disabled={ui.selectedItem?.kind !== 'template'}
        onclick={deleteSelected}
      >
        <svg viewBox="0 0 16 16" fill="currentColor">
          <path d="M3.25 8a.75.75 0 01.75-.75h8a.75.75 0 010 1.5H4A.75.75 0 013.25 8z"/>
        </svg>
      </button>
    </div>
  </div>

  <div class="panel-body">
    {#if templates().length === 0}
      <p class="empty-hint">No templates defined yet.<br>Click + to create one.</p>
    {:else}
      <div class="template-grid">
        {#each templates() as tpl}
          <!-- svelte-ignore a11y_click_events_have_key_events -->
          <!-- svelte-ignore a11y_no_static_element_interactions -->
          <div
            class="template-card"
            class:selected={ui.selectedItem?.name === tpl.name && ui.selectedItem?.kind === 'template'}
            onclick={() => select(tpl.name)}
          >
            <div class="template-icon">
              {#if iconFor(tpl.type) === 'cylinder'}
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                  <ellipse cx="12" cy="6" rx="7" ry="3"/>
                  <path d="M5 6v12c0 1.66 3.13 3 7 3s7-1.34 7-3V6"/>
                </svg>
              {:else if iconFor(tpl.type) === 'cube'}
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path stroke-linejoin="round" d="M3 7.5l9-4.5 9 4.5-9 4.5-9-4.5z"/>
                  <path stroke-linejoin="round" d="M3 7.5v9l9 4.5m0-9v9m0-9l9-4.5m-9 4.5l9 4.5m0-9v9"/>
                </svg>
              {:else}
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                  <circle cx="12" cy="12" r="8"/>
                </svg>
              {/if}
            </div>
            <span class="template-name">{tpl.name}</span>
            <span class="template-type">{tpl.type}</span>
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
    flex: 1;
    overflow: hidden;
    min-height: 0;
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 10px;
    border-bottom: 1px solid var(--color-border);
    flex-shrink: 0;
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
    gap: 2px;
    align-items: center;
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

  .panel-body {
    flex: 1;
    overflow-y: auto;
    padding: 8px;
  }

  .empty-hint {
    font-size: 11px;
    color: var(--color-subtext);
    text-align: center;
    padding: 20px 12px;
    opacity: 0.7;
  }

  .template-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 8px;
  }

  .template-card {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    padding: 10px 6px;
    border-radius: 8px;
    cursor: pointer;
    background: var(--color-bg-raised);
    border: 1px solid transparent;
    transition: border-color 0.1s, background 0.1s;
  }

  .template-card:hover { border-color: var(--color-border); }
  .template-card.selected { border-color: var(--color-accent); background: rgba(6, 182, 212, 0.1); }

  .template-icon {
    width: 36px;
    height: 36px;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--color-accent);
    background: rgba(6, 182, 212, 0.08);
    border-radius: 6px;
  }

  .template-icon svg { width: 22px; height: 22px; }

  .template-name {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text);
    text-align: center;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
  }

  .template-type {
    font-size: 9px;
    color: var(--color-subtext);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
</style>