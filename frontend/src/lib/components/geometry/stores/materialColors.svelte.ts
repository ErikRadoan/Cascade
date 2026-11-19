// materialColors.svelte.ts — user-chosen material colours, shared app-wide.
//
// The material library (backend ~/.cascade/materials.json) is the source of
// truth. This store mirrors its per-material `color` overrides so renderers
// that only know a material *id* (CsgViewportPanel) can resolve the colour
// synchronously while building GPU tables.
//
// Reactivity contract: `materialColorState.version` bumps whenever the
// override set actually changes. A consumer that needs to re-render on colour
// edits reads `version` inside an $effect (CsgViewportPanel does). Resolve
// functions deliberately do NOT track — they are called from inside already
// untracked rebuild paths, and tracking there would re-run a full GPU rebuild
// on unrelated store writes.
//
// Palette/hash fallback + hex parsing live in ../materialColors.ts (pure).

import * as api from '$lib/api';
import {
  defaultMaterialColorRgb01, hexToRgb01, lookupOverride, normalizeHex,
  type Rgb01,
} from '../materialColors';

export const materialColorState = $state<{
  version: number;
  loaded: boolean;
  overrides: Record<string, string>;
}>({ version: 0, loaded: false, overrides: {} });

// Drop responses from superseded loads (rapid save → save sequences).
let loadSeq = 0;
const PAGE = 500;   // backend caps `limit` at 500 (api/materials.py)

function sameMap(a: Record<string, string>, b: Record<string, string>): boolean {
  const ka = Object.keys(a);
  if (ka.length !== Object.keys(b).length) return false;
  return ka.every((k) => a[k] === b[k]);
}

/**
 * Fetch every material's colour override from the backend and publish it.
 * Safe to call repeatedly (mount, after any library edit). Failures keep the
 * previous overrides — a transient network error must not wipe user colours
 * from the viewport.
 */
export async function loadMaterialColors(): Promise<void> {
  const seq = ++loadSeq;
  try {
    const next: Record<string, string> = {};
    let offset = 0;
    for (;;) {
      const res = await api.materials.search({ limit: PAGE, offset });
      for (const m of res.items) {
        const hex = normalizeHex(m.color);
        if (hex) next[m.id] = hex;
      }
      offset += res.items.length;
      if (res.items.length === 0 || offset >= res.total) break;
    }
    if (seq !== loadSeq) return;                       // a newer load owns the state
    const changed = !sameMap(materialColorState.overrides, next);
    materialColorState.overrides = next;
    materialColorState.loaded = true;
    if (changed) materialColorState.version++;
  } catch (e) {
    console.warn('[materialColors] could not load material colours; keeping previous', e);
  }
}

/** Effective colour (override → palette → hash) as `#RRGGBB`. */
export function resolveMaterialColorHex(id: string): string {
  const o = lookupOverride(materialColorState.overrides, id);
  if (o) return o;
  const [r, g, b] = defaultMaterialColorRgb01(id);
  return '#' + [r, g, b].map((v) => Math.round(v * 255).toString(16).padStart(2, '0')).join('').toUpperCase();
}

/** Effective colour as 0–1 RGB, the form the shader proto table wants. */
export function resolveMaterialColorRgb01(id: string): Rgb01 {
  const o = lookupOverride(materialColorState.overrides, id);
  return o ? hexToRgb01(o) : defaultMaterialColorRgb01(id);
}
