Title: Undo/redo via per-project YAML text snapshots
Date: 2026-10-19
Status: Accepted

Context
-------
A geometry is a single YAML string (`GeometryProject.text`) and every editing
surface (Template, Object and Parameters panels) writes it through
`setGeometryText()`. Undo/redo must cover all of them, survive future panels,
and not require per-operation inverse logic that has to track the DSL.

Decision
--------
Each project owns a `TextHistory` (stores/textHistory.ts): a capped stack of
`{ text, selection }` snapshots.

- `setGeometryText()` records the state it is about to replace. A write whose
  text equals the current text is ignored, which keeps the internal "reload the
  scene" calls out of the history without those callers knowing about it.
- Bursts of edits to one thing pass a `coalesceKey` (ParametersPanel uses
  `field:<kind>:<name>:<key>`) and merge into one step inside an 800 ms
  sliding window. Discrete operations (create, delete, rename) pass none.
- Undo/redo replay through `applyText()` (no recording) with a 120 ms debounce so
  a held Ctrl+Z collapses into one backend validate + scene build.
- The UI selection is stored in each snapshot and restored on replay, dropping
  names that do not exist in the restored document.
- `applyText()` stamps each validate/scene request with a per-project sequence
  number and discards responses that are no longer the latest.
- `saveProject()` persists a captured copy of the text and re-schedules itself
  if the text changed while the request was in flight.

Consequences
------------
- Any new editing surface gets undo/redo for free as long as it writes via
  `setGeometryText()`. Anything that mutates `project.text` directly bypasses it.
- History is in-memory and per tab: it is lost on reload and discarded when the
  tab is closed. Renaming a tab (`renameProject`) is not undoable (it is not YAML).
- Memory is bounded (200 steps x a few KB of YAML). If geometries grow to
  MB-sized texts, switch snapshots to diffs behind the same `TextHistory` API.
- Keyboard shortcuts stand down while a text field has focus (native input undo
  wins) and while a modal is open.

Notes
-----
Tests: `npm run test:unit` (tests/textHistory.test.ts).
