// selection.svelte.ts — which geometry object/template is currently
// selected (drives ObjectPanel/TemplatePanel highlighting and what
// ParametersPanel shows). Was `ui.selectedItem` on the old shared store;
// scoped here because only geometry components touch it.

import type { SelectedItem } from '$lib/types';

export const geometrySelection = $state<{ selectedItem: SelectedItem | null }>({
  selectedItem: null,
});
