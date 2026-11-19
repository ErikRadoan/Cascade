// materialColors.ts — pure (no Svelte runes, no DOM) material colour helpers.
//
// Single source of truth for "what colour does material X render with":
//   1. a user override from the material library (see stores/materialColors.svelte.ts)
//   2. the curated palette below
//   3. a stable hash colour derived from the id
// This module owns (2) and (3) plus hex parsing; the store layers (1) on top.
// Kept free of runes so it can be unit-tested with `npm run test:unit`.
//
// KEEP IN SYNC: the curated palette must match
// backend scene_builder_service._MATERIAL_COLORS so the CSG viewport,
// ResultsViewport3D and GeometryPlotPanel agree when no override is set.

export type Rgb01 = [number, number, number];

export const MATERIAL_COLORS: Readonly<Record<string, string>> = {
  'UO2': '#E8703A', 'UO2_3.5pct': '#E8703A', 'UO2_4pct': '#D4612E',
  'ThO2': '#C8A882', 'UC': '#B06030',
  'He': '#D0EEF8', 'He4': '#D0EEF8', 'Air': '#E8F4F8',
  'Zr4': '#8BAFC0', 'Zircaloy': '#8BAFC0', 'Zircaloy-4': '#8BAFC0',
  'SS304': '#A0A8B0', 'SS316': '#9098A8', 'Steel': '#909090',
  'Al': '#C8D0D8',
  'H2O': '#4A90D9', 'Water': '#4A90D9', 'D2O': '#3A7AC0',
  'Na': '#F0C040', 'LiFBeF2': '#90D0A0', 'FLiBe': '#90D0A0',
  'B4C': '#303030', 'Hafnium': '#606878', 'Hf': '#606878',
  'AgInCd': '#788090', 'Gd2O3': '#A08060',
  'Graphite': '#505050', 'Be': '#B0C8B0', 'Pb': '#787870',
  'void': '#101020', 'Void': '#101020',
};

/**
 * Canonicalise a user/server colour to `#RRGGBB` (uppercase), or null if it
 * is not a valid hex colour. Accepts `#RGB`, `#RRGGBB`, with or without `#`.
 * Mirrors backend `normalize_hex_color`, except invalid input yields null
 * instead of raising (UI callers just reject the edit).
 */
export function normalizeHex(value: string | null | undefined): string | null {
  if (value == null) return null;
  const m = /^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.exec(value.trim());
  if (!m) return null;
  let d = m[1];
  if (d.length === 3) d = d.split('').map((c) => c + c).join('');
  return '#' + d.toUpperCase();
}

export function hexToRgb01(hex: string): Rgb01 {
  const h = hex.replace('#', '');
  const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

export function rgb01ToHex([r, g, b]: Rgb01): string {
  const to = (v: number) => Math.max(0, Math.min(255, Math.round(v * 255))).toString(16).padStart(2, '0');
  return ('#' + to(r) + to(g) + to(b)).toUpperCase();
}

export function hslToRgb(h: number, s: number, l: number): Rgb01 {
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const hp = h * 6;
  const x = c * (1 - Math.abs((hp % 2) - 1));
  let [r1, g1, b1] = [0, 0, 0];
  if (hp < 1) [r1, g1, b1] = [c, x, 0];
  else if (hp < 2) [r1, g1, b1] = [x, c, 0];
  else if (hp < 3) [r1, g1, b1] = [0, c, x];
  else if (hp < 4) [r1, g1, b1] = [0, x, c];
  else if (hp < 5) [r1, g1, b1] = [x, 0, c];
  else [r1, g1, b1] = [c, 0, x];
  const m = l - c / 2;
  return [r1 + m, g1 + m, b1 + m];
}

/** Stable per-id fallback colour (FNV-1a → hue). Unchanged from the original viewport code. */
export function hashColor(id: string): Rgb01 {
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) { h ^= id.charCodeAt(i); h = Math.imul(h, 16777619); }
  return hslToRgb(((h >>> 0) % 360) / 360, 0.55, 0.55);
}

/** Palette → hash colour; i.e. what a material looks like with NO user override. */
export function defaultMaterialColorRgb01(id: string): Rgb01 {
  const curated = MATERIAL_COLORS[id];
  if (curated) return hexToRgb01(curated);
  const lower = id.toLowerCase();
  for (const [k, v] of Object.entries(MATERIAL_COLORS)) {
    if (k.toLowerCase() === lower) return hexToRgb01(v);
  }
  return hashColor(id);
}

export function defaultMaterialColorHex(id: string): string {
  return rgb01ToHex(defaultMaterialColorRgb01(id));
}

/**
 * Resolve `id` against a {id → hex} override map: exact match first, then
 * case-insensitive (same rule as the palette and the backend scene builder).
 * Returns canonical hex or null when there is no valid override.
 */
export function lookupOverride(overrides: Readonly<Record<string, string>>, id: string): string | null {
  const exact = overrides[id];
  if (exact) return normalizeHex(exact);
  const lower = id.toLowerCase();
  for (const [k, v] of Object.entries(overrides)) {
    if (k.toLowerCase() === lower) return normalizeHex(v);
  }
  return null;
}
