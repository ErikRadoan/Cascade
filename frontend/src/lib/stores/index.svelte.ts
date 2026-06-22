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
// Geometry projects — like open files in an IDE.
// Each project is independently editable; exactly one is "active" at a
// time (the open tab). Job submission always targets the active project.
// ---------------------------------------------------------------------------

const DEFAULT_TEXT = `# Cascade geometry definition
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
`;

export interface GeometryProject {
  // `id` is the backend geometry_id once saved. Until the first successful
  // save, it's a client-generated temporary id (prefixed "tmp-") so the
  // tab still has a stable React-key-like identity to render against.
  id: string;
  name: string;
  text: string;

  isValid:      boolean;
  errors:       ValidationError[];
  isValidating: boolean;

  scene:          SceneResponse | null;
  isLoadingScene: boolean;
  isSceneStale:   boolean;

  isSaved: boolean;   // has this project ever been persisted (has a real backend id)
  isDirty: boolean;   // unsaved changes since the last successful save/update
  isSaving: boolean;
}

function makeProject(opts: { id?: string; name: string; text?: string; isSaved?: boolean }): GeometryProject {
  return {
    id:             opts.id ?? `tmp-${crypto.randomUUID()}`,
    name:           opts.name,
    text:           opts.text ?? DEFAULT_TEXT,
    isValid:        true,
    errors:         [],
    isValidating:   false,
    scene:          null,
    isLoadingScene: false,
    isSceneStale:   false,
    isSaved:        opts.isSaved ?? false,
    isDirty:        false,
    isSaving:       false,
  };
}

export const projects = $state({
  list:     [makeProject({ name: 'geometry 1' })] as GeometryProject[],
  activeId: '' as string,
});
// Initialise activeId after list exists so we can reference list[0].id
projects.activeId = projects.list[0].id;

/** The currently active project — components read/write through this. */
export function activeProject(): GeometryProject {
  const found = projects.list.find(p => p.id === projects.activeId);
  // Fall back to first project if activeId somehow points nowhere
  // (e.g. the active tab was just closed) — should be rare given
  // closeProject() reassigns activeId itself, but stay defensive.
  return found ?? projects.list[0];
}

// ---------------------------------------------------------------------------
// Validation / scene refresh — operates on a specific project, not a
// global singleton. Debounce timer is per-project so editing two tabs
// in quick succession doesn't cancel each other's pending validation.
// ---------------------------------------------------------------------------

const validateTimers = new Map<string, ReturnType<typeof setTimeout>>();
const saveTimers = new Map<string, ReturnType<typeof setTimeout>>();

export function setGeometryText(text: string, opts: { immediate?: boolean } = {}) {
  const project = activeProject();
  project.text = text;
  project.isSceneStale = true;
  project.isDirty = true;

  const existingTimer = validateTimers.get(project.id);
  if (existingTimer) clearTimeout(existingTimer);

  const run = async () => {
    project.isValidating = true;
    try {
      const result = await api.geometry.validate(text);
      project.isValid = result.valid;
      project.errors  = result.errors;

      if (result.valid) {
        project.isLoadingScene = true;
        try {
          project.scene = await api.geometry.scene(text);
          project.isSceneStale = false;
        } catch {
          // keep stale flag — viewport shows "Updating…" rather than crash
        } finally {
          project.isLoadingScene = false;
        }
      }
    } catch {
      // network error — leave previous validation state visible
    } finally {
      project.isValidating = false;
    }
  };

  if (opts.immediate) {
    run();
  } else {
    validateTimers.set(project.id, setTimeout(run, 400));
  }

  scheduleAutosave(project);
}

// ---------------------------------------------------------------------------
// Autosave — debounced separately from validation (longer delay, since
// hitting the backend on every keystroke for persistence is wasteful
// even though validation already debounces at 400ms).
// ---------------------------------------------------------------------------

function scheduleAutosave(project: GeometryProject) {
  const existing = saveTimers.get(project.id);
  if (existing) clearTimeout(existing);

  saveTimers.set(project.id, setTimeout(() => saveProject(project.id), 1200));
}

export async function saveProject(projectId: string): Promise<void> {
  const project = projects.list.find(p => p.id === projectId);
  if (!project || project.isSaving) return;

  project.isSaving = true;
  try {
    if (project.isSaved) {
      await api.geometry.update(project.id, project.text, project.name);
    } else {
      const result = await api.geometry.save(project.text, project.name);
      // Adopt the real backend id — the tab's identity now matches the
      // persisted record. Existing references to the old tmp- id (e.g.
      // ui.selectedItem doesn't hold project ids, so nothing else needs
      // updating) are unaffected.
      project.id = result.id;
      project.isSaved = true;
    }
    project.isDirty = false;
  } catch {
    // Leave isDirty true — next edit or manual save retries.
    // Silent failure here is deliberate: autosave shouldn't interrupt
    // the user with a popup on every transient network hiccup.
  } finally {
    project.isSaving = false;
  }
}

// ---------------------------------------------------------------------------
// Tab management
// ---------------------------------------------------------------------------

function nextDefaultName(): string {
  const existing = new Set(projects.list.map(p => p.name));
  let i = 1;
  while (existing.has(`geometry ${i}`)) i++;
  return `geometry ${i}`;
}

export function newProject(): GeometryProject {
  const project = makeProject({ name: nextDefaultName() });
  projects.list.push(project);
  projects.activeId = project.id;
  setGeometryText(project.text, { immediate: true });
  return project;
}

export async function openExistingProject(geometryId: string): Promise<void> {
  // If already open, just switch to it instead of opening a duplicate tab.
  const existing = projects.list.find(p => p.id === geometryId);
  if (existing) {
    projects.activeId = existing.id;
    return;
  }

  const detail = await api.geometry.get(geometryId);
  const project = makeProject({
    id:      detail.id,
    name:    detail.name,
    text:    detail.yaml_text,
    isSaved: true,
  });
  projects.list.push(project);
  projects.activeId = project.id;
  setGeometryText(project.text, { immediate: true });
}

export function switchProject(projectId: string) {
  if (projects.list.some(p => p.id === projectId)) {
    projects.activeId = projectId;
  }
}

export function renameProject(projectId: string, name: string) {
  const project = projects.list.find(p => p.id === projectId);
  if (!project || !name.trim()) return;
  project.name = name.trim();
  project.isDirty = true;
  scheduleAutosave(project);
}

/**
 * Close a tab. This does NOT delete the backend record (same as closing
 * a file in an IDE doesn't delete it from disk) — it only removes it from
 * the open-tabs list. Use deleteProjectPermanently() to actually delete.
 */
export function closeProject(projectId: string) {
  const idx = projects.list.findIndex(p => p.id === projectId);
  if (idx === -1) return;

  // Always keep at least one tab open
  if (projects.list.length === 1) return;

  const wasActive = projects.activeId === projectId;
  projects.list.splice(idx, 1);

  if (wasActive) {
    const fallback = projects.list[Math.max(0, idx - 1)];
    projects.activeId = fallback.id;
  }

  validateTimers.delete(projectId);
  saveTimers.delete(projectId);
}

export async function deleteProjectPermanently(projectId: string): Promise<void> {
  const project = projects.list.find(p => p.id === projectId);
  if (project?.isSaved) {
    try {
      await api.geometry.delete(project.id);
    } catch {
      // backend delete failed — still close the tab locally so the UI
      // doesn't get stuck; the record may need manual cleanup
    }
  }
  closeProject(projectId);
}

// ---------------------------------------------------------------------------
// Backward-compat shim: components written against the old singleton
// `editor` store can be migrated incrementally. New code should prefer
// `activeProject()` directly since it's reactive per-call, whereas this
// getter object's properties don't auto-update across re-renders the
// same way plain $derived would inside a component.
// Prefer importing activeProject() in new components.
// ---------------------------------------------------------------------------

export function editorSnapshot() {
  return activeProject();
}

// ---------------------------------------------------------------------------
// Object visibility — now keyed by `${projectId}:${placementName}` so
// visibility state doesn't leak between different geometry projects.
// ---------------------------------------------------------------------------

export const visibility = $state<Record<string, boolean>>({});

function visKey(name: string): string {
  return `${projects.activeId}:${name}`;
}

export function isVisible(name: string): boolean {
  return visibility[visKey(name)] !== false;
}

export function toggleVisibility(name: string) {
  const key = visKey(name);
  visibility[key] = !(visibility[key] !== false);
}

// ---------------------------------------------------------------------------
// Jobs state
// ---------------------------------------------------------------------------

export const jobsState = $state({
  list:      [] as import('$lib/types').JobSummary[],
  isLoading: false,
  error:     null as string | null,
});