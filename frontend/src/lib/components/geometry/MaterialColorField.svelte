<script lang="ts">
  // MaterialColorField — colour picker for one material.
  //
  // Native <input type="color"> + a hex text box + an "Auto" reset.
  //   value      : the material's override as #RRGGBB, or null = automatic.
  //   defaultHex : what "automatic" resolves to (palette/hash) — shown as the
  //                swatch and text placeholder while value is null, so the
  //                picker always previews what the 3D view will actually use.
  //   onchange   : fired on COMMIT only (picker closed / hex box blurred or
  //                Enter / Auto clicked), never per drag tick, so a parent
  //                that saves to the backend isn't hit on every mouse move.
  //                Passes canonical #RRGGBB, or null for "back to automatic".
  import { normalizeHex } from './materialColors';

  let { value, defaultHex, onchange, disabled = false }: {
    value: string | null;
    defaultHex: string;
    onchange: (next: string | null) => void;
    disabled?: boolean;
  } = $props();

  const effective = $derived(normalizeHex(value) ?? normalizeHex(defaultHex) ?? '#A0A0A0');

  // Text box draft; null = not being edited (show the committed value).
  let draft = $state<string | null>(null);
  let invalid = $state(false);
  const shown = $derived(draft ?? value ?? '');

  function onPick(e: Event) {
    const hex = normalizeHex((e.currentTarget as HTMLInputElement).value);
    invalid = false;
    draft = null;
    if (hex && hex !== value) onchange(hex);
  }

  function commitText() {
    const raw = (draft ?? '').trim();
    draft = null;
    if (raw === '') { invalid = false; if (value !== null) onchange(null); return; }
    const hex = normalizeHex(raw);
    if (hex === null) { invalid = true; return; }   // revert to committed value
    invalid = false;
    if (hex !== value) onchange(hex);
  }

  function reset() { invalid = false; draft = null; if (value !== null) onchange(null); }
</script>

<div class="color-field" class:disabled>
  <input
    class="swatch-input"
    type="color"
    value={effective.toLowerCase()}
    {disabled}
    onchange={onPick}
    title="Pick the colour this material is drawn with in the 3D view"
    aria-label="Material colour"
  />
  <input
    class="hex-input"
    class:invalid
    type="text"
    spellcheck="false"
    maxlength="7"
    placeholder={defaultHex.toUpperCase()}
    value={shown}
    {disabled}
    oninput={(e) => { draft = (e.currentTarget as HTMLInputElement).value; invalid = false; }}
    onblur={commitText}
    onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commitText(); } }}
    aria-label="Material colour hex value"
    title={invalid ? 'Enter a hex colour like #E8703A' : 'Hex colour (empty = automatic)'}
  />
  {#if value !== null}
    <button class="auto-btn" type="button" {disabled} onclick={reset}
      title="Remove the custom colour and use the automatic one">Auto</button>
  {:else}
    <span class="auto-tag">auto</span>
  {/if}
</div>

<style>
  .color-field { display: flex; align-items: center; gap: 6px; }
  .color-field.disabled { opacity: 0.5; }
  .swatch-input {
    width: 30px; height: 24px; padding: 0; cursor: pointer;
    background: none; border: 1px solid var(--color-border); border-radius: 4px;
  }
  .hex-input {
    width: 78px; background: var(--color-bg-raised); border: 1px solid var(--color-border);
    border-radius: 4px; color: var(--color-text); font-family: var(--font-mono);
    font-size: 12px; padding: 4px 6px; text-transform: uppercase;
  }
  .hex-input::placeholder { color: var(--color-subtext); opacity: 0.6; }
  .hex-input:focus { outline: none; border-color: var(--color-accent); }
  .hex-input.invalid { border-color: #ef4444; }
  .auto-btn {
    background: none; border: 1px solid var(--color-border); color: var(--color-subtext);
    font-size: 10px; padding: 3px 7px; border-radius: 4px; cursor: pointer;
  }
  .auto-btn:hover { color: var(--color-text); border-color: var(--color-accent); }
  .auto-tag { font-size: 10px; color: var(--color-subtext); opacity: 0.7; }
</style>
