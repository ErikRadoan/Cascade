// Global application state using Svelte 5 runes.

import type { ActiveTab, SceneResponse, SelectedItem, ValidationError } from '$lib/types';
import * as api from '$lib/api';

// ---------------------------------------------------------------------------
// UI state
// ---------------------------------------------------------------------------

export const ui = $state({
  activeTab:    'geometry' as ActiveTab,
  selectedItem: null as SelectedItem | null,
});

// ---------------------------------------------------------------------------
// Geometry editor state
// ---------------------------------------------------------------------------

export const editor = $state({
  text: `# Cascade geometry definition
# Define templates first, then place them

my_fuel_pin:
  type: FuelPin
  pellet_radius: 0.4096
  pellet_height: 365.76
  pellet_material: UO2
  gap_thickness: 0.0082
  gap_material: He
  clad_thickness: 0.0572
  clad_material: Zr4

my_box:
  type: Box
  x_size: 1.26
  y_size: 1.26
  z_size: 365.76
  material: H2O
  boundary_type: reflective

center_pin:
  type: SinglePlacement
  template: my_fuel_pin
  x: 0.0
  y: 0.0
  z: 0.0

boundary:
  type: SinglePlacement
  template: my_box
  x: 0.0
  y: 0.0
  z: 0.0
`,

  isValid:        true,
  errors:         [] as ValidationError[],
  isValidating:   false,

  scene:          null as SceneResponse | null,
  isLoadingScene: false,
  isSceneStale:   false,
});

// ---------------------------------------------------------------------------
// Centralised text-change handler.
// Any component that edits editor.text (raw YAML editor, ParametersPanel
// form, future template/placement creation dialogs) MUST call this instead
// of mutating editor.text directly and hoping something notices.
// ---------------------------------------------------------------------------

let validateTimer: ReturnType<typeof setTimeout>;

export function setGeometryText(text: string, opts: { immediate?: boolean } = {}) {
  editor.text = text;
  editor.isSceneStale = true;

  clearTimeout(validateTimer);
  const run = async () => {
    editor.isValidating = true;
    try {
      const result = await api.geometry.validate(text);
      editor.isValid = result.valid;
      editor.errors  = result.errors;

      if (result.valid) {
        editor.isLoadingScene = true;
        try {
          editor.scene = await api.geometry.scene(text);
          editor.isSceneStale = false;
        } catch {
          // keep stale flag — viewport shows "Updating…" rather than crash
        } finally {
          editor.isLoadingScene = false;
        }
      }
    } catch {
      // network error — leave previous validation state visible
    } finally {
      editor.isValidating = false;
    }
  };

  if (opts.immediate) {
    run();
  } else {
    validateTimer = setTimeout(run, 400);
  }
}

// ---------------------------------------------------------------------------
// Object visibility — keyed by placement base name (e.g. "core", "boundary").
// Lives here rather than in ObjectPanel/Viewport3D local state so it
// survives tab switches and is readable by both components.
// Absence of a key means visible (default true) — we only store explicit
// hides so newly created placements don't need to be registered anywhere.
// ---------------------------------------------------------------------------

export const visibility = $state<Record<string, boolean>>({});

export function isVisible(name: string): boolean {
  return visibility[name] !== false;
}

export function toggleVisibility(name: string) {
  visibility[name] = !isVisible(name);
}

// ---------------------------------------------------------------------------
// Jobs state
// ---------------------------------------------------------------------------

export const jobsState = $state({
  list:      [] as import('$lib/types').JobSummary[],
  isLoading: false,
  error:     null as string | null,
});