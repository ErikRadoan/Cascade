// dockStore.svelte.ts — owns the docking layout tree (splits + tab groups)
// for GeometryEditor's panels, plus every mutation that can happen to it:
// reordering tabs, moving a tab into another group, splitting a pane to
// dock a tab beside it, and resizing splits.
//
// The tree shape (DockNode, see dockTypes.ts) is plain JSON on purpose.
// dock.layout IS the save format — serializeDockLayout()/loadDockLayout()
// exist now so the shape is locked in, but nothing calls them yet. When
// persistence lands, wire a debounced $effect (in GeometryEditor, or
// wherever the project/job context lives) that watches `dock.layout` and
// calls serializeDockLayout(), and call loadDockLayout() on mount if a
// saved layout exists. Nothing else in this file needs to change for that.

import type { DockNode, DockLeaf, DockSplit, DockDragPayload, DropZone } from './dockTypes.ts';

const DOCK_LAYOUT_VERSION = 1;

let idCounter = 0;
function nextId(prefix: string): string {
  idCounter += 1;
  return `${prefix}-${idCounter}`;
}

function leaf(...tabs: string[]): DockLeaf {
  return { type: 'leaf', id: nextId('leaf'), tabs, activeTab: tabs[0] };
}

function split(direction: DockSplit['direction'], children: DockNode[], sizes?: number[]): DockSplit {
  return {
    type: 'split',
    id: nextId('split'),
    direction,
    children,
    sizes: sizes ?? children.map(() => 1 / children.length),
  };
}

// Mirrors the original fixed 3-column layout (Objects+Templates stacked on
// the left, Viewport center, Parameters right) but every pane is now a
// real dock leaf, so it can be dragged, tabbed, split, and resized.
//
// Phase D of geometry-restructuring-plan.md: the center pane used to tab
// together two ids ('viewport', 'csgViewport') that rendered two DIFFERENT
// components (the old FuelPin/Box-only Viewport3D, and the general CSG
// raymarcher) side by side as redundant tabs. Now that 'viewport' itself
// points at the CSG viewer (see panelRegistry.ts), there's only one tab
// here — 'csgViewport' remains a registered alias for old persisted
// layouts, it's just never emitted by a fresh default layout anymore.
function defaultLayout(): DockNode {
  return split(
    'row',
    [
      split('column', [leaf('objects'), leaf('templates')], [0.5, 0.5]),
      leaf('viewport'),
      leaf('parameters'),
    ],
    [0.22, 0.56, 0.22],
  );
}

export const dock = $state<{ layout: DockNode }>({ layout: defaultLayout() });

// Shared drag state. We track this ourselves (rather than relying on
// dataTransfer.getData during dragover) because dataTransfer's payload is
// only reliably readable on the 'drop' event in most browsers — we need
// to know what's being dragged *while* hovering, to compute snap zones.
export const dockDrag = $state<{ active: DockDragPayload | null }>({ active: null });

// ---------------------------------------------------------------------
// Tree traversal helpers
// ---------------------------------------------------------------------

interface NodeInfo {
  node: DockNode;
  parent: DockSplit | null;
  index: number;
}

function findNodeInfo(node: DockNode, id: string, parent: DockSplit | null = null, index = -1): NodeInfo | null {
  if (node.id === id) return { node, parent, index };
  if (node.type === 'split') {
    for (let i = 0; i < node.children.length; i++) {
      const found = findNodeInfo(node.children[i], id, node, i);
      if (found) return found;
    }
  }
  return null;
}

function normalizeSizes(node: DockSplit) {
  const total = node.sizes.reduce((a, b) => a + b, 0);
  node.sizes = total > 0 ? node.sizes.map((s) => s / total) : node.children.map(() => 1 / node.children.length);
}

// Collapses a split down to its own child when only one pane is left in
// it (e.g. after the sibling was dragged away), so the tree never carries
// pointless single-child wrapper splits around.
function collapseSplitIfNeeded(node: DockSplit) {
  if (node.children.length !== 1) {
    normalizeSizes(node);
    return;
  }
  const only = node.children[0];
  const info = findNodeInfo(dock.layout, node.id);
  if (!info) return;
  if (!info.parent) {
    dock.layout = only;
    return;
  }
  info.parent.children[info.index] = only;
  normalizeSizes(info.parent);
}

function removeTabFromLeaf(leafId: string, panelId: string): boolean {
  const info = findNodeInfo(dock.layout, leafId);
  if (!info || info.node.type !== 'leaf') return false;
  const targetLeaf = info.node;
  const idx = targetLeaf.tabs.indexOf(panelId);
  if (idx === -1) return false;

  targetLeaf.tabs.splice(idx, 1);
  if (targetLeaf.activeTab === panelId) {
    targetLeaf.activeTab = targetLeaf.tabs[Math.max(0, idx - 1)] ?? targetLeaf.tabs[0];
  }
  if (targetLeaf.tabs.length === 0) {
    removeEmptyLeaf(leafId);
  }
  return true;
}

function removeEmptyLeaf(leafId: string) {
  const info = findNodeInfo(dock.layout, leafId);
  // Never remove the last pane standing — an empty root leaf is left in
  // place rather than vanishing the whole layout.
  if (!info || !info.parent) return;
  const { parent, index } = info;
  parent.children.splice(index, 1);
  parent.sizes.splice(index, 1);
  collapseSplitIfNeeded(parent);
}

function reorderWithinLeaf(leafId: string, panelId: string, insertIndex: number) {
  const info = findNodeInfo(dock.layout, leafId);
  if (!info || info.node.type !== 'leaf') return;
  const targetLeaf = info.node;
  const from = targetLeaf.tabs.indexOf(panelId);
  if (from === -1) return;

  let to = insertIndex;
  if (from < to) to -= 1; // removing "from" first shifts later indices left
  to = Math.max(0, Math.min(to, targetLeaf.tabs.length - 1));
  if (to === from) return;

  targetLeaf.tabs.splice(from, 1);
  targetLeaf.tabs.splice(to, 0, panelId);
}

// ---------------------------------------------------------------------
// Public mutations
// ---------------------------------------------------------------------

export function setActiveTab(leafId: string, panelId: string) {
  const info = findNodeInfo(dock.layout, leafId);
  if (info && info.node.type === 'leaf') info.node.activeTab = panelId;
}

/** Drop onto a tab strip: reorder in place, or move from another group as a plain tab. */
export function moveTabToGroup(payload: DockDragPayload, targetLeafId: string, insertIndex: number) {
  const { panelId, sourceLeafId } = payload;

  if (sourceLeafId === targetLeafId) {
    reorderWithinLeaf(targetLeafId, panelId, insertIndex);
    return;
  }

  if (!removeTabFromLeaf(sourceLeafId, panelId)) return;

  const targetInfo = findNodeInfo(dock.layout, targetLeafId);
  if (!targetInfo || targetInfo.node.type !== 'leaf') return;
  const targetLeaf = targetInfo.node;

  const idx = Math.min(insertIndex, targetLeaf.tabs.length);
  targetLeaf.tabs.splice(idx, 0, panelId);
  targetLeaf.activeTab = panelId;
}

/** Drop onto a pane's edge zone: split that pane and dock the tab beside it. */
export function splitWithTab(payload: DockDragPayload, targetLeafId: string, zone: Exclude<DropZone, 'center'>) {
  const { panelId, sourceLeafId } = payload;

  // Dragging a group's only tab onto its own edge would just re-split
  // an already-empty space around it — a no-op, so skip the work.
  if (sourceLeafId === targetLeafId) {
    const info = findNodeInfo(dock.layout, targetLeafId);
    if (info && info.node.type === 'leaf' && info.node.tabs.length === 1) return;
  }

  if (!removeTabFromLeaf(sourceLeafId, panelId)) return;

  const targetInfo = findNodeInfo(dock.layout, targetLeafId);
  if (!targetInfo || targetInfo.node.type !== 'leaf') return;
  const { parent, index } = targetInfo;
  const targetLeaf = targetInfo.node;

  const newLeaf = leaf(panelId);
  const direction: DockSplit['direction'] = zone === 'left' || zone === 'right' ? 'row' : 'column';
  const newGoesFirst = zone === 'left' || zone === 'top';

  if (parent && parent.direction === direction) {
    // Already inside a split running the right way — just add a sibling
    // pane next to the target and give it half of the target's space.
    const half = parent.sizes[index] / 2;
    parent.sizes[index] = half;
    const insertAt = newGoesFirst ? index : index + 1;
    parent.children.splice(insertAt, 0, newLeaf);
    parent.sizes.splice(insertAt, 0, half);
    return;
  }

  // Otherwise wrap the target leaf in a brand-new split.
  const wrapper = split(direction, newGoesFirst ? [newLeaf, targetLeaf] : [targetLeaf, newLeaf], [0.5, 0.5]);
  if (!parent) {
    dock.layout = wrapper;
  } else {
    parent.children[index] = wrapper;
  }
}

/** Drag a splitter between children[index - 1] and children[index]. */
export function resizeSplit(node: DockSplit, index: number, deltaFraction: number) {
  const MIN = 0.08;
  let a = node.sizes[index - 1] + deltaFraction;
  let b = node.sizes[index] - deltaFraction;
  if (a < MIN) {
    b -= MIN - a;
    a = MIN;
  }
  if (b < MIN) {
    a -= MIN - b;
    b = MIN;
  }
  if (a < MIN || b < MIN) return; // pane too small to satisfy both minimums
  node.sizes[index - 1] = a;
  node.sizes[index] = b;
}

// ---------------------------------------------------------------------
// Save/load prep — locked-in shape, no wiring yet (see file header).
// ---------------------------------------------------------------------

export function serializeDockLayout(): string {
  return JSON.stringify({ version: DOCK_LAYOUT_VERSION, layout: dock.layout });
}

export function loadDockLayout(json: string): boolean {
  try {
    const parsed = JSON.parse(json);
    if (!parsed || parsed.version !== DOCK_LAYOUT_VERSION || !parsed.layout) return false;
    dock.layout = parsed.layout;
    return true;
  } catch {
    return false;
  }
}

export function resetDockLayout(): void {
  dock.layout = defaultLayout();
}
