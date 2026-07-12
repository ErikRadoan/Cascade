// panelRegistry.ts — maps a panel id (as stored in the dock layout tree)
// to the component that renders it and the title shown on its tab.
//
// Keep this the single source of truth for "what panels exist" — DockTabGroup
// and the default layout in dockStore both key off these same ids.

import type { Component } from 'svelte';
import ObjectPanel from './ObjectPanel.svelte';
import TemplatePanel from './TemplatePanel.svelte';
import ParametersPanel from './ParametersPanel.svelte';
import ViewportPanel from './ViewportPanel.svelte';

export interface PanelDef {
  title: string;
  component: Component;
}

export const PANEL_REGISTRY: Record<string, PanelDef> = {
  objects: { title: 'Objects', component: ObjectPanel },
  templates: { title: 'Templates', component: TemplatePanel },
  viewport: { title: 'Viewport', component: ViewportPanel },
  parameters: { title: 'Parameters', component: ParametersPanel },
};
