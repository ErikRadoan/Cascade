// selection.svelte.ts — which geometry object/template is currently
// selected (drives ObjectPanel/TemplatePanel highlighting and what
// ParametersPanel shows). Was `ui.selectedItem` on the old shared store;
// scoped here because only geometry components touch it.
//
// Multi-select: `selectedNames` holds zero or more placement names
// (ctrl/cmd-click in ObjectPanel). `selectedItem` remains the primary
// focus used by ParametersPanel (last clicked).

import type { SelectedItem } from '$lib/types';

export const geometrySelection = $state<{
  selectedItem: SelectedItem | null;
  /** Multi-select set for ObjectPanel boolean composition (placement names). */
  selectedNames: string[];
}>({
  selectedItem: null,
  selectedNames: [],
});
