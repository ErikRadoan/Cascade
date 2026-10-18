<script lang="ts">
  // GeometryEditor — top-level layout for the geometry tab.
  // Tab bar on top, then a JetBrains-style dock layout below it: panes can
  // be dragged by their tab into other panes (as a tab) or onto a pane's
  // edge (to split it), and resized via the splitters between them.
  // See dockStore.svelte.ts for the layout tree and its mutations.
  //
  // Everything below the tab bar operates on the ACTIVE project.

  import { activeProject, setGeometryText, undo, redo } from './stores/projects.svelte';
  import GeometryTabBar from './GeometryTabBar.svelte';
  import DockLayout from './dock/DockLayout.svelte';
  import { dock } from './dock/dockStore.svelte.js';
  import LoadingScreen from '../shared/LoadingScreen.svelte';
  import { busy } from '$lib/stores/busy.svelte';

  // NOTE: restoreProjects() is no longer called here. Startup is owned by the splash
  // screen (stores/appStatus.svelte.ts), and this component isn't even mounted until
  // it has finished.

  // Loading overlay. Shown while:
  //  - a geometry is being fetched before its tab exists (withBusy), or
  //  - the active geometry has no scene yet and one is being built — i.e. on first open
  //    or when switching to a tab that hasn't loaded (restored tabs load lazily).
  // Deliberately NOT shown for edits to an already-loaded geometry (it has a scene, so
  // `!scene` is false): covering the editor on every keystroke-triggered rebuild
  // would be unusable.
  const proj = $derived(activeProject());
  const loadingMessage = $derived(
    busy.count > 0 ? busy.label
    : proj.isValidating ? 'Validating geometry…'
    : 'Building scene…'
  );
  const showLoading = $derived(
    busy.count > 0 || (!proj.scene && (proj.isLoadingScene || proj.isValidating))
  );

  // Re-run scene load whenever the active tab changes AND that project
  // has never had its scene loaded yet (switching back to an
  // already-loaded tab shouldn't refetch).
  $effect(() => {
    const p = activeProject();
    if (!p.scene && !p.isLoadingScene && !p.isValidating) {
      setGeometryText(p.text, { immediate: true });
    }
  });

  // ---- Undo / redo shortcuts -------------------------------------------------
  // Ctrl/Cmd+Z = undo, Ctrl/Cmd+Shift+Z = redo, Ctrl+Y = redo (Windows/Linux).
  // This component is only mounted while the geometry tab is showing, so the
  // listener is naturally scoped to it.
  //
  // We deliberately stand down when the user is typing in a text field: the
  // browser's native per-input undo is what they expect there, and hijacking
  // it would undo the whole geometry instead of the character they just typed.
  // Checkboxes/ranges/buttons have no native text undo, so the shortcut applies.
  const NON_TEXT_INPUT_TYPES = new Set([
    'checkbox', 'radio', 'button', 'submit', 'reset', 'range', 'color', 'file', 'image',
  ]);

  function isTextEntryTarget(target: EventTarget | null): boolean {
    if (!(target instanceof HTMLElement)) return false;
    if (target.isContentEditable) return true;
    if (target instanceof HTMLInputElement) return !NON_TEXT_INPUT_TYPES.has(target.type);
    return target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement;
  }

  function onHistoryKeydown(e: KeyboardEvent) {
    if (e.defaultPrevented || e.altKey) return;
    if (!(e.ctrlKey || e.metaKey)) return;
    if (isTextEntryTarget(e.target)) return;
    // Don't edit the geometry behind an open modal (Material Library, job dialogs).
    if (document.querySelector('.modal-backdrop, [aria-modal="true"]')) return;

    const key = e.key.toLowerCase();
    if (key === 'z') {
      e.preventDefault();
      if (e.shiftKey) redo(); else undo();
    } else if (key === 'y' && e.ctrlKey && !e.shiftKey) {
      e.preventDefault();
      redo();
    }
  }
</script>

<svelte:window onkeydown={onHistoryKeydown} />

<div class="geometry-tab">
  <GeometryTabBar />

  <div class="geometry-editor">
    <DockLayout node={dock.layout} />
    {#if showLoading}
      <!-- delay: loads under ~150 ms never show it, so quick switches don't flash. -->
      <LoadingScreen variant="overlay" delay={150} message={loadingMessage} detail={busy.count > 0 ? '' : proj.name} />
    {/if}
  </div>
</div>

<style>
  .geometry-tab {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }

  .geometry-editor {
    position: relative;   /* anchors the loading overlay */
    display: flex;
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }
</style>