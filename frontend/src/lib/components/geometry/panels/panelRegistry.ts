// panelRegistry.ts — maps a panel id (as stored in the dock layout tree)
// to the component that renders it and the title shown on its tab.
//
// Keep this the single source of truth for "what panels exist" — DockTabGroup
// and the default layout in dockStore both key off these same ids.
//
// Phase D of geometry-restructuring-plan.md: the old FuelPin/Box-only
// ViewportPanel (backed by Viewport3D.svelte + SceneBuilder) is retired.
// 'viewport' now points at CsgViewportPanel, the same component that used
// to only be reachable via the 'csgViewport' tab. 'csgViewport' stays
// registered, aliased to the same component, purely so a dock layout
// persisted before this change (see dockStore.svelte.ts's
// serializeDockLayout/loadDockLayout) doesn't reference a dangling id.
// dockStore's defaultLayout() itself only emits 'viewport' going forward.

import type { Component } from 'svelte';
import ObjectPanel from './ObjectPanel.svelte';
import TemplatePanel from './TemplatePanel.svelte';
import ParametersPanel from './ParametersPanel.svelte';
import CsgViewportPanel from "$lib/components/geometry/panels/CsgViewportPanel.svelte";

export interface PanelDef {
  title: string;
  component: Component;
}

export const PANEL_REGISTRY: Record<string, PanelDef> = {
  objects: { title: 'Objects', component: ObjectPanel },
  templates: { title: 'Templates', component: TemplatePanel },
  viewport: { title: 'Viewport', component: CsgViewportPanel },
  // Backward-compat alias only — see module docstring. Do not add new
  // references to this id; use 'viewport'.
  csgViewport: { title: 'Viewport', component: CsgViewportPanel },
  parameters: { title: 'Parameters', component: ParametersPanel },
};
