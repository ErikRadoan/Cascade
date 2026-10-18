<script lang="ts">
  // LoadingScreen — branded loading UI, in two flavours:
  //
  //   variant="fullscreen"  app startup splash (routes/+layout.svelte). Covers the window.
  //   variant="overlay"     covers its nearest `position: relative` ancestor, e.g. the
  //                         geometry editor while a geometry loads / is switched to.
  //
  // `delay` (ms): the screen only appears if loading is STILL going after this long.
  // Most loads finish in well under 200 ms, and a spinner that flashes for a few
  // frames feels worse than no spinner. Mount/unmount it freely; the delay is handled
  // here. The fullscreen splash uses delay=0 (it is the whole UI at that point).
  //
  // `progress`: 0..1 for a determinate bar, or null for an indeterminate sweep.
  // `actionLabel`/`onaction`: optional escape-hatch button (e.g. "continue offline").

  import { untrack } from 'svelte';
  import { fade } from 'svelte/transition';
  import CascadeLoader from './CascadeLoader.svelte';

  let {
    variant = 'overlay',
    message = 'Loading…',
    detail = '',
    progress = null,
    delay = 0,
    actionLabel = '',
    onaction,
  }: {
    variant?: 'fullscreen' | 'overlay';
    message?: string;
    detail?: string;
    progress?: number | null;
    delay?: number;
    actionLabel?: string;
    onaction?: () => void;
  } = $props();

  // `delay` is read once at mount on purpose (a prop that changes mid-load shouldn't
  // restart the timer), hence untrack. `visible` flips true after it elapses.
  const initialDelay = untrack(() => delay);
  let visible = $state(initialDelay <= 0);
  $effect(() => {
    if (visible) return;
    const t = setTimeout(() => (visible = true), initialDelay);
    return () => clearTimeout(t);
  });

  const full = $derived(variant === 'fullscreen');
</script>

<!-- `|global`: play the fade when the PARENT removes this component, not just when an
     inner block toggles. Without it Svelte 5 skips the fade and the screen pops out. -->
{#if visible}
  <div
    class="loading"
    class:fullscreen={full}
    class:overlay={!full}
    role="status"
    aria-live="polite"
    aria-busy="true"
    in:fade|global={{ duration: full ? 0 : 180 }}
    out:fade|global={{ duration: 320 }}
  >
    <div class="content">
      <CascadeLoader size={full ? 132 : 76} label="Cascade is loading" />

      {#if full}
        <h1 class="brand">Cascade</h1>
      {/if}

      <p class="message">{message}</p>
      <p class="detail" class:empty={!detail}>{detail || '\u00A0'}</p>

      <div class="bar" class:determinate={progress !== null}>
        <span style={progress !== null ? `width: ${Math.round(Math.min(1, Math.max(0, progress)) * 100)}%` : ''}></span>
      </div>

      {#if actionLabel && onaction}
        <button class="action" type="button" onclick={onaction}>{actionLabel}</button>
      {/if}
    </div>
  </div>
{/if}

<style>
  .loading {
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: var(--font-sans);
    color: var(--color-text);
    user-select: none;
  }

  .fullscreen {
    position: fixed;
    inset: 0;
    z-index: 1000;
    /* A faint warm glow behind the logo, like the lattice is lit from within. */
    background:
      radial-gradient(circle at 50% 42%, rgba(239, 107, 47, 0.10), transparent 52%),
      var(--color-bg-deep);
  }

  .overlay {
    position: absolute;
    inset: 0;
    z-index: 50;
    background: rgba(15, 23, 42, 0.78);
    backdrop-filter: blur(3px);
    -webkit-backdrop-filter: blur(3px);
  }

  .content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    padding: 24px;
    text-align: center;
    max-width: min(420px, 90%);
  }

  .brand {
    margin-top: 14px;
    font-size: 28px;
    font-weight: 600;
    letter-spacing: 0.32em;
    text-indent: 0.32em;           /* re-centre: letter-spacing adds trailing space */
    text-transform: uppercase;
    color: var(--color-text);
  }

  .message {
    margin-top: 6px;
    font-size: 14px;
    color: var(--color-subtext);
  }
  .overlay .message { margin-top: 2px; font-size: 13px; }

  .detail {
    font-family: var(--font-mono);
    font-size: 11.5px;
    color: rgba(203, 213, 225, 0.6);
    min-height: 1.4em;             /* reserve the line so the layout never jumps */
  }

  /* --- progress bar ------------------------------------------------------ */
  .bar {
    position: relative;
    width: 180px;
    height: 3px;
    margin-top: 6px;
    border-radius: 3px;
    background: rgba(51, 65, 85, 0.7);
    overflow: hidden;
  }
  .bar span {
    position: absolute;
    top: 0; bottom: 0; left: 0;
    width: 38%;
    border-radius: 3px;
    background: linear-gradient(90deg, #B22A35, #F29B16, #F8C51D);
    animation: sweep 1.5s cubic-bezier(0.4, 0, 0.2, 1) infinite;
  }
  .bar.determinate span {
    animation: none;
    transition: width 0.25s ease;
  }
  @keyframes sweep {
    0%   { transform: translateX(-100%); }
    100% { transform: translateX(263%); }
  }

  .action {
    margin-top: 14px;
    padding: 7px 16px;
    font: inherit;
    font-size: 12.5px;
    color: var(--color-subtext);
    background: transparent;
    border: 1px solid var(--color-border);
    border-radius: 6px;
    cursor: pointer;
    transition: color 0.15s, border-color 0.15s, background 0.15s;
  }
  .action:hover {
    color: var(--color-text);
    border-color: var(--color-accent);
    background: rgba(6, 182, 212, 0.08);
  }
  .action:focus-visible {
    outline: 2px solid var(--color-accent-hi);
    outline-offset: 2px;
  }

  @media (prefers-reduced-motion: reduce) {
    .bar span { animation: none; width: 100%; opacity: 0.6; }
  }
</style>
