// textHistory.ts — undo/redo stack for ONE geometry project's YAML text.
//
// WHY SNAPSHOTS OF TEXT: a Cascade geometry is a single YAML string
// (`GeometryProject.text`) and every editing surface (Template/Object/
// Parameters panels) writes through `setGeometryText()`. Snapshotting that
// string is therefore complete (nothing can be missed) and trivially correct
// (no inverse operations to keep in sync with the DSL). YAML texts are a few
// KB, so a capped stack is cheap.
//
// WHY THIS FILE HAS NO SVELTE IMPORTS: it must stay unit-testable with plain
// Node (see frontend/tests/textHistory.test.ts) and reusable. The Svelte
// store (projects.svelte.ts) owns reactivity: a class instance stored on a
// `$state` object is NOT proxied, so the store mirrors `canUndo`/`canRedo`
// into plain booleans on the project after every operation.
//
// CALLING CONTRACT
//   record(before, key)  — call BEFORE applying an edit, passing the state
//                          that is about to be replaced.
//   undo(current)/redo(current) — pass the CURRENT state; returns the state
//                          to restore (or null when there is nothing to do).
//                          The caller applies the returned state itself and
//                          must NOT call record() for it.

export interface Snapshot<S> {
  /** Full YAML text at that point in time. */
  text: string;
  /** Opaque caller-defined payload (the store uses it for UI selection). */
  sel: S;
}

export interface TextHistoryOptions {
  /** Max undo steps retained; oldest are dropped first. Default 200. */
  limit?: number;
  /** Edits sharing a coalesce key within this window merge into one step. Default 800 ms. */
  coalesceMs?: number;
  /** Injectable clock for tests. */
  now?: () => number;
}

export class TextHistory<S> {
  private past: Snapshot<S>[] = [];
  private future: Snapshot<S>[] = [];
  private lastKey: string | null = null;
  private lastAt = 0;

  private readonly limit: number;
  private readonly coalesceMs: number;
  private readonly now: () => number;

  constructor(opts: TextHistoryOptions = {}) {
    this.limit = opts.limit ?? 200;
    this.coalesceMs = opts.coalesceMs ?? 800;
    this.now = opts.now ?? (() => Date.now());
  }

  get canUndo(): boolean { return this.past.length > 0; }
  get canRedo(): boolean { return this.future.length > 0; }

  /**
   * Register that an edit is about to replace `before`.
   *
   * `coalesceKey` (optional) identifies a continuous editing gesture, e.g.
   * "field:placement:center_pin:x". A repeat of the same key within
   * `coalesceMs` of the previous one is folded into the existing step: the
   * pre-gesture state is already on the stack, so we only slide the window.
   *
   * Returns true when a new undo step was created.
   */
  record(before: Snapshot<S>, coalesceKey?: string | null): boolean {
    const t = this.now();
    const key = coalesceKey ?? null;

    const coalesce =
      key !== null &&
      key === this.lastKey &&
      this.past.length > 0 &&
      t - this.lastAt <= this.coalesceMs;

    // Any new edit invalidates the redo branch, coalesced or not.
    this.future = [];
    this.lastAt = t;
    this.lastKey = key;

    if (coalesce) return false;

    this.past.push(before);
    if (this.past.length > this.limit) this.past.shift();
    return true;
  }

  /** Step back. Returns the state to restore, or null if nothing to undo. */
  undo(current: Snapshot<S>): Snapshot<S> | null {
    const target = this.takeDifferent(this.past, current.text);
    if (!target) return null;
    this.future.push(current);
    this.breakCoalescing();
    return target;
  }

  /** Step forward. Returns the state to restore, or null if nothing to redo. */
  redo(current: Snapshot<S>): Snapshot<S> | null {
    const target = this.takeDifferent(this.future, current.text);
    if (!target) return null;
    this.past.push(current);
    this.breakCoalescing();
    return target;
  }

  clear(): void {
    this.past = [];
    this.future = [];
    this.breakCoalescing();
  }

  /**
   * Pop from `stack` until an entry whose text differs from `currentText`.
   * Skipping equal entries matters for coalesced gestures that end where
   * they began (type 5, then back to the original value): the stored
   * pre-gesture snapshot equals the current text, and surfacing it would
   * make one Ctrl+Z appear to do nothing.
   */
  private takeDifferent(stack: Snapshot<S>[], currentText: string): Snapshot<S> | null {
    while (stack.length > 0) {
      const candidate = stack.pop()!;
      if (candidate.text !== currentText) return candidate;
    }
    return null;
  }

  /** After undo/redo the next edit must always start a fresh step. */
  private breakCoalescing(): void {
    this.lastKey = null;
    this.lastAt = 0;
  }
}
