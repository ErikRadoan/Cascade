// busy.svelte.ts — "something is loading" flag for work that has no natural
// per-object loading state (e.g. fetching a saved geometry before its tab exists).
//
// Kept dependency-free ON PURPOSE: geometry/stores/projects.svelte.ts imports
// this, and appStatus.svelte.ts imports projects.svelte.ts. Putting this in
// appStatus would create an import cycle.
//
// Usage:  const detail = await withBusy('Opening geometry…', () => api.geometry.get(id));
// UI:     GeometryEditor.svelte shows a LoadingScreen overlay while busy.count > 0.

export const busy = $state({ count: 0, label: '' });

export async function withBusy<T>(label: string, fn: () => Promise<T>): Promise<T> {
  busy.count++;
  busy.label = label;
  try {
    return await fn();
  } finally {
    busy.count = Math.max(0, busy.count - 1);
    if (busy.count === 0) busy.label = '';
  }
}
