// dockPanelContext.ts — the interface PanelHeader uses to act as the drag
// handle / drop target for a dock group, and — when more than one panel is
// tabbed together — to render a tiny inline tab switcher instead of a
// separate strip. Set by DockTabGroup, read by PanelHeader via Svelte
// context, so panel components (ObjectPanel, TemplatePanel, ...) don't
// need to know anything about docking; they just render <PanelHeader>
// exactly like before.

export const DOCK_PANEL_CONTEXT_KEY = 'dock-panel-context';

export interface DockPanelContext {
  /** All panel ids tabbed together in this group, in display order. */
  readonly tabs: string[];
  /** The panel id currently showing. */
  readonly activeTab: string;
  titleFor(panelId: string): string;
  setActiveTab(panelId: string): void;
  onDragStart(panelId: string, e: DragEvent): void;
  onDragEnd(): void;
  /** Drop a dragged tab on this header to tabify it into this group. */
  onDropAsTab(e: DragEvent): void;
}
