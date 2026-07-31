// csg.svelte.ts — Phase D of geometry-restructuring-plan.md.
//
// Both ObjectPanel (the object list) and CsgViewportPanel (the 3D view)
// need the same GET /geometry/csg data for the active project's CURRENT
// text. Before Phase D, each fetched it independently — two debounced
// requests firing on every keystroke instead of one. This store is the
// single source of truth both consumers read from; `requestCsgRefresh()`
// de-dupes and debounces so the second caller in the same render pass is
// a no-op, not a second network request.
//
// This is scoped to geometry TEXT, not to a specific GeometryProject
// object identity — callers pass `activeProject().text` in directly, the
// same pattern CsgViewportPanel already used pre-Phase-D. Switching tabs
// to a different open project will naturally trigger a refetch the next
// time a consumer's $effect re-reads a different `text` value.

import * as api from '$lib/api';
import type { CsgGeometry } from '$lib/types';

export const csgState = $state<{
  data: CsgGeometry | null;
  loading: boolean;
  error: string | null;
}>({
  data: null,
  loading: false,
  error: null,
});

let debounceHandle: ReturnType<typeof setTimeout> | undefined;
let lastRequestedText: string | null = null;

async function fetchNow(text: string) {
  csgState.loading = true;
  csgState.error = null;
  try {
    csgState.data = await api.geometry.csg(text);
  } catch (e: unknown) {
    csgState.error = e instanceof Error ? e.message : String(e);
    csgState.data = null;
  } finally {
    csgState.loading = false;
  }
}

/**
 * Request a refresh for `text`. Call this from a `$effect` that reads
 * `activeProject().text` as a dependency — e.g.:
 *
 *   $effect(() => { requestCsgRefresh(activeProject().text); });
 *
 * De-dupes against the most recently REQUESTED text (not the most
 * recently fetched one) so that if ObjectPanel and CsgViewportPanel both
 * react to the same text change in the same tick, only the first call
 * schedules a fetch — the second sees `lastRequestedText` already
 * matches and does nothing.
 */
export function requestCsgRefresh(text: string) {
  if (text === lastRequestedText) return;
  lastRequestedText = text;
  clearTimeout(debounceHandle);
  debounceHandle = setTimeout(() => fetchNow(text), 400);
}
