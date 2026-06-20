<script lang="ts">
  import { ui } from '$lib/stores/index.svelte';
  import type { ActiveTab } from '$lib/types';

  const tabs: { id: ActiveTab; label: string; icon: string }[] = [
    { id: 'geometry',      label: 'Geometry Editor',  icon: 'cube'      },
    { id: 'jobs',          label: 'Job Scheduler',    icon: 'cpu'       },
    { id: 'results',       label: 'Results Viewer',   icon: 'chart-bar' },
  ];

  function setTab(id: ActiveTab) {
    ui.activeTab = id;
  }
</script>

<nav class="sidebar" aria-label="Main navigation">
  {#each tabs as tab}
    <button
      class="tab-btn"
      class:active={ui.activeTab === tab.id}
      onclick={() => setTab(tab.id)}
      title={tab.label}
      aria-label={tab.label}
      aria-current={ui.activeTab === tab.id ? 'page' : undefined}
    >
      <!-- Inline SVG icons — no icon library dependency -->
      {#if tab.icon === 'cube'}
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round"
            d="M21 7.5l-9-5.25L3 7.5m18 0l-9 5.25m9-5.25v9l-9 5.25M3 7.5l9 5.25M3 7.5v9l9 5.25m0-9v9"/>
        </svg>
      {:else if tab.icon === 'cpu'}
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round"
            d="M8.25 3v1.5M4.5 8.25H3m18 0h-1.5M4.5 12H3m18 0h-1.5m-15 3.75H3m18 0h-1.5M8.25 19.5V21M12 3v1.5m0 15V21m3.75-18v1.5m0 15V21m-9-1.5h10.5a2.25 2.25 0 002.25-2.25V6.75a2.25 2.25 0 00-2.25-2.25H6.75A2.25 2.25 0 004.5 6.75v10.5a2.25 2.25 0 002.25 2.25zm.75-12h9v9h-9v-9z"/>
        </svg>
      {:else if tab.icon === 'chart-bar'}
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round"
            d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z"/>
        </svg>
      {/if}
    </button>
  {/each}
</nav>

<style>
  .sidebar {
    width: var(--sidebar-w);
    background: var(--color-bg-panel);
    border-right: 1px solid var(--color-border);
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 8px 0;
    gap: 4px;
    flex-shrink: 0;
  }

  .tab-btn {
    width: 36px;
    height: 36px;
    border-radius: 8px;
    border: none;
    background: transparent;
    color: var(--color-subtext);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: color 0.15s, background 0.15s;
  }

  .tab-btn svg {
    width: 20px;
    height: 20px;
  }

  .tab-btn:hover {
    color: var(--color-text);
    background: var(--color-bg-raised);
  }

  .tab-btn.active {
    color: var(--color-accent);
    background: rgba(6, 182, 212, 0.12);
  }
</style>