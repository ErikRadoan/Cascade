// Unit tests for the undo/redo stack. Plain node:test, no framework needed:
//   npm run test:unit
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { TextHistory } from '../src/lib/components/geometry/stores/textHistory.ts';

// Tiny harness mimicking how the store drives the stack.
function makeEditor(opts: ConstructorParameters<typeof TextHistory>[0] = {}) {
  let clock = 0;
  const h = new TextHistory<string>({ ...opts, now: () => clock });
  let text = 'v0';
  return {
    h,
    get text() { return text; },
    advance(ms: number) { clock += ms; },
    edit(next: string, key?: string) {
      if (next === text) return;                       // store ignores no-op writes
      h.record({ text, sel: `sel@${text}` }, key);
      text = next;
    },
    undo() { const r = h.undo({ text, sel: `sel@${text}` }); if (r) text = r.text; return r; },
    redo() { const r = h.redo({ text, sel: `sel@${text}` }); if (r) text = r.text; return r; },
  };
}

test('undo and redo walk back and forth', () => {
  const e = makeEditor();
  e.edit('v1'); e.edit('v2'); e.edit('v3');
  e.undo(); assert.equal(e.text, 'v2');
  e.undo(); assert.equal(e.text, 'v1');
  e.redo(); assert.equal(e.text, 'v2');
  e.redo(); assert.equal(e.text, 'v3');
  assert.equal(e.redo(), null);
});

test('nothing to undo on a fresh history', () => {
  const e = makeEditor();
  assert.equal(e.h.canUndo, false);
  assert.equal(e.undo(), null);
  assert.equal(e.text, 'v0');
});

test('a new edit clears the redo branch', () => {
  const e = makeEditor();
  e.edit('v1'); e.edit('v2');
  e.undo();
  assert.equal(e.h.canRedo, true);
  e.edit('v1b');
  assert.equal(e.h.canRedo, false);
  assert.equal(e.redo(), null);
});

test('snapshot payload (selection) round-trips', () => {
  const e = makeEditor();
  e.edit('v1');
  const back = e.undo();
  assert.equal(back?.sel, 'sel@v0');          // selection from BEFORE the edit
  const fwd = e.redo();
  assert.equal(fwd?.sel, 'sel@v1');           // selection from AFTER the edit
});

test('same key within the window coalesces into one step', () => {
  const e = makeEditor({ coalesceMs: 800 });
  e.edit('a1', 'f'); e.advance(300);
  e.edit('a2', 'f'); e.advance(300);
  e.edit('a3', 'f');
  e.undo();
  assert.equal(e.text, 'v0');                 // whole gesture undone in one step
  assert.equal(e.h.canUndo, false);
});

test('window slides: a long continuous gesture stays one step', () => {
  const e = makeEditor({ coalesceMs: 800 });
  for (let i = 1; i <= 10; i++) { e.edit(`a${i}`, 'f'); e.advance(700); }
  e.undo();
  assert.equal(e.text, 'v0');
});

test('same key after the window expires starts a new step', () => {
  const e = makeEditor({ coalesceMs: 800 });
  e.edit('a1', 'f'); e.advance(2000);
  e.edit('a2', 'f');
  e.undo(); assert.equal(e.text, 'a1');
  e.undo(); assert.equal(e.text, 'v0');
});

test('different keys never coalesce', () => {
  const e = makeEditor();
  e.edit('a1', 'x'); e.edit('a2', 'y');
  e.undo(); assert.equal(e.text, 'a1');
});

test('edits without a key never coalesce', () => {
  const e = makeEditor();
  e.edit('a1'); e.edit('a2'); e.edit('a3');
  e.undo(); e.undo(); e.undo();
  assert.equal(e.text, 'v0');
});

test('undo breaks coalescing: next edit is a fresh step even with the same key', () => {
  const e = makeEditor();
  e.edit('a1', 'f');
  e.undo();
  e.edit('a2', 'f');
  assert.equal(e.h.canUndo, true);
  e.undo(); assert.equal(e.text, 'v0');
});

test('gesture that returns to its start does not leave a dead undo step', () => {
  const e = makeEditor();
  e.edit('a1', 'f'); e.advance(100);
  e.edit('v0', 'f');                          // back to original, coalesced
  e.edit('b1');                               // unrelated real edit
  e.undo(); assert.equal(e.text, 'v0');
  // The coalesced entry equals current text -> skipped, nothing left.
  assert.equal(e.undo(), null);
});

test('limit drops the oldest steps', () => {
  const e = makeEditor({ limit: 3 });
  for (let i = 1; i <= 6; i++) e.edit(`v${i}`);
  e.undo(); e.undo(); e.undo();
  assert.equal(e.text, 'v3');
  assert.equal(e.undo(), null);
});

test('clear empties both stacks', () => {
  const e = makeEditor();
  e.edit('v1'); e.edit('v2'); e.undo();
  e.h.clear();
  assert.equal(e.h.canUndo, false);
  assert.equal(e.h.canRedo, false);
});
