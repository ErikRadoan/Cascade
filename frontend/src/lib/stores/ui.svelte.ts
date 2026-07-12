// ui.svelte.ts — app-wide UI state that genuinely spans domains (which
// top-level tab — geometry / orchestration / results — is showing).
//
// Anything that only matters to one domain does NOT belong here anymore:
//   - which geometry object/template is selected -> geometry/stores/selection.svelte.ts
//   - which job's results page is open           -> results/stores/resultsUi.svelte.ts
//   - the job list / polling                      -> orchestration/stores/jobs.svelte.ts
// This file should stay small. If you're about to add a field here, ask
// first whether it's actually only used by one domain's components.

import type { ActiveTab } from '$lib/types';

export const ui = $state({
  activeTab: 'geometry' as ActiveTab,
});
