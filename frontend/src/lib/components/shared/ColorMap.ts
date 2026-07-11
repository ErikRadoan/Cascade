// Shared results-dashboard conventions: value -> color, log/linear scaling,
// rel_err flagging. One module, imported by every panel (Scalars/Mesh/
// Spectra/3D heatmap) so a "0.2, log, blue-red" legend means the same thing
// everywhere — see results-dashboard-spec.md §3.
//
// No chart/color library dependency — same house style as ResultsViewport3D
// (hand-rolled orbit controls, no three-orbit-controls dep).

export type ScaleType = 'log' | 'linear';

export interface ColorScale {
  type: ScaleType;
  min: number; // domain min (raw value, not log-transformed)
  max: number; // domain max
}

export const REL_ERR_WARN_THRESHOLD = 0.10;

// 5-stop diverging-ish sequential ramp: deep blue -> cyan -> green -> amber
// -> red. Kept as the one ramp every panel imports, so nothing invents a
// second convention. Matches the app's cyan accent at the low end rather
// than a generic blue, so it doesn't clash with --color-accent elsewhere
// in the UI.
const RAMP_STOPS: [number, [number, number, number]][] = [
  [0.0, [30, 41, 59]],    // slate-800 (near --color-bg base, "cold")
  [0.25, [6, 182, 212]],  // cyan (--color-accent)
  [0.5, [34, 197, 94]],   // green
  [0.75, [234, 179, 8]],  // amber
  [1.0, [239, 68, 68]],   // red (matches .error-badge red elsewhere)
];

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function rgbToHex(r: number, g: number, b: number): string {
  const h = (n: number) => Math.round(Math.max(0, Math.min(255, n))).toString(16).padStart(2, '0');
  return `#${h(r)}${h(g)}${h(b)}`;
}

/** t in [0,1] -> hex color string, walking the shared ramp. */
export function rampColor(t: number): string {
  const c = Math.max(0, Math.min(1, t));
  for (let i = 0; i < RAMP_STOPS.length - 1; i++) {
    const [t0, c0] = RAMP_STOPS[i];
    const [t1, c1] = RAMP_STOPS[i + 1];
    if (c >= t0 && c <= t1) {
      const local = t1 === t0 ? 0 : (c - t0) / (t1 - t0);
      return rgbToHex(
        lerp(c0[0], c1[0], local),
        lerp(c0[1], c1[1], local),
        lerp(c0[2], c1[2], local),
      );
    }
  }
  return rgbToHex(...RAMP_STOPS[RAMP_STOPS.length - 1][1]);
}

/** Build a scale from a set of values, ignoring non-finite entries.
 *  Log scale silently drops values <= 0 from the domain (can't log them);
 *  callers should still render those cells, just via valueToT's floor. */
export function buildScale(values: number[], type: ScaleType = 'log'): ColorScale {
  const finite = values.filter((v) => Number.isFinite(v));
  const domain = type === 'log' ? finite.filter((v) => v > 0) : finite;
  if (domain.length === 0) return { type, min: 0, max: 1 };
  return { type, min: Math.min(...domain), max: Math.max(...domain) };
}

/** Map a raw value onto [0,1] given a scale. Values <= 0 on a log scale
 *  clamp to 0 (bottom of the ramp) rather than being excluded from render —
 *  exclusion is the caller's job (e.g. NO_DATA_COLOR), not this function's. */
export function valueToT(value: number, scale: ColorScale): number {
  if (!Number.isFinite(value)) return 0;
  if (scale.max === scale.min) return 0.5;
  if (scale.type === 'log') {
    if (value <= 0) return 0;
    const logMin = Math.log10(Math.max(scale.min, 1e-300));
    const logMax = Math.log10(Math.max(scale.max, 1e-300));
    if (logMax === logMin) return 0.5;
    return (Math.log10(value) - logMin) / (logMax - logMin);
  }
  return (value - scale.min) / (scale.max - scale.min);
}

export function valueToColor(value: number, scale: ColorScale): string {
  return rampColor(valueToT(value, scale));
}

/** Shared rel_err flag — same threshold and semantics everywhere a scalar
 *  with uncertainty is rendered (cell heatmap, mesh voxel, table row, bar). */
export function isFlagged(relErr: number | null | undefined, threshold = REL_ERR_WARN_THRESHOLD): boolean {
  return relErr != null && Number.isFinite(relErr) && relErr > threshold;
}

/** Convenience: derive rel_err from mean/std_dev when a source (e.g. mesh
 *  voxels) gives raw std_dev instead of a precomputed rel_err field. */
export function relErrFrom(mean: number, stdDev: number): number | null {
  if (!Number.isFinite(mean) || mean === 0 || !Number.isFinite(stdDev)) return null;
  return Math.abs(stdDev / mean);
}