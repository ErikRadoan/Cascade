// dockTypes.ts — shared types for the JetBrains-style docking layout used
// by GeometryEditor.
//
// The tree is deliberately plain JSON: no class instances, no functions,
// nothing that can't survive JSON.stringify/parse. That's on purpose —
// this shape is what gets persisted once save/load lands, so the store
// never has to translate between a "live" representation and a "saved"
// one. See dockStore.svelte.ts's serializeDockLayout/loadDockLayout.

export type PanelId = string;

export interface DockLeaf {
  type: 'leaf';
  id: string;
  /** Panel ids docked as tabs in this group, in display order. */
  tabs: PanelId[];
  activeTab: PanelId;
}

export interface DockSplit {
  type: 'split';
  id: string;
  direction: 'row' | 'column';
  children: DockNode[];
  /** Fractional sizes, same length/order as children, sums to ~1. */
  sizes: number[];
}

export type DockNode = DockLeaf | DockSplit;

/** Where a dragged tab is being dropped relative to a target pane. */
export type DropZone = 'center' | 'top' | 'bottom' | 'left' | 'right';

export interface DockDragPayload {
  panelId: PanelId;
  sourceLeafId: string;
}
