<script lang="ts">
  import { ui } from '$lib/stores/index.svelte';
  import Sidebar from '$lib/components/shared/Sidebar.svelte';
  import MenuBar from '$lib/components/shared/MenuBar.svelte';

  let { children } = $props();
</script>

<div class="app-shell">
  <MenuBar />
  <div class="app-body">
    <Sidebar />
    <main class="app-main">
      {@render children()}
    </main>
  </div>
</div>

<style>
  :global(*) {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }

  :global(:root) {
    /* Color tokens */
    --color-bg-deep:    #0F172A;
    --color-bg-panel:   #1E293B;
    --color-bg-raised:  #334155;
    --color-accent:     #06B6D4;
    --color-accent-hi:  #22D3EE;
    --color-text:       #F8FAFC;
    --color-subtext:    #CBD5E1;
    --color-border:     rgba(51, 65, 85, 0.6);

    /* Typography */
    --font-mono:  'JetBrains Mono', 'Fira Code', monospace;
    --font-sans:  'Inter', system-ui, sans-serif;

    /* Sizing */
    --sidebar-w:    48px;
    --menubar-h:    28px;
    --panel-left-w: 260px;
    --panel-right-w: 260px;

    font-family: var(--font-sans);
    background: var(--color-bg-deep);
    color: var(--color-text);
  }

  :global(body) {
    height: 100dvh;
    overflow: hidden;
  }

  .app-shell {
    display: flex;
    flex-direction: column;
    height: 100dvh;
    overflow: hidden;
  }

  .app-body {
    display: flex;
    flex: 1;
    overflow: hidden;
  }

  .app-main {
    flex: 1;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }
</style>