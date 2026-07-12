// visibility.svelte.ts — per-project placement visibility toggles (the
// eye icon in ObjectPanel). Keyed by `${projectId}:${placementName}` so
// visibility state doesn't leak between different geometry projects.
// Moved out of the old lib/stores/index.svelte.ts as-is; only the import
// of `projects` is new (was a sibling in the same file before).

import { projects } from './projects.svelte';

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
