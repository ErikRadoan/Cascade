<script lang="ts">
  import { onMount } from 'svelte';
  import JobPanel from './JobPanel.svelte';
  import JobDetails from './JobDetails.svelte';
  import JobSubmitModal from './JobSubmitModal.svelte';
  import ProfileManagerModal from './ProfileManagerModal.svelte';
  import * as api from '$lib/api';
  import type { BackendProfile } from '$lib/types';

  // ---------------------------------------------------------------------------
  // Profile state — lives here so both modals share the same list
  // ---------------------------------------------------------------------------

  let profiles:       BackendProfile[] = $state([]);
  let profilesLoaded: boolean          = $state(false);

  async function loadProfiles() {
    try {
      profiles       = await (api as any).profiles.list();
      profilesLoaded = true;
    } catch (e) {
      console.error('Failed to load backend profiles:', e);
      profilesLoaded = true;
    }
  }

  function handleProfilesChange(updated: BackendProfile[]) {
    profiles = updated;
  }

  // ---------------------------------------------------------------------------
  // Modal visibility
  // ---------------------------------------------------------------------------

  let showSubmitModal  = $state(false);
  let showProfileModal = $state(false);

  function openSubmit()  { showSubmitModal  = true; showProfileModal = false; }
  function openProfiles() { showProfileModal = true; showSubmitModal  = false; }
  function closeSubmit()  { showSubmitModal  = false; }
  function closeProfiles() { showProfileModal = false; }

  // Called from JobSubmitModal's "Manage backends →" link
  function openProfilesFromSubmit() {
    showSubmitModal  = false;
    showProfileModal = true;
  }

  // ---------------------------------------------------------------------------
  // Lifecycle
  // ---------------------------------------------------------------------------

  onMount(() => {
    loadProfiles();
  });
</script>

<div class="job-scheduler">
  <aside class="left-panel">
    <JobPanel
      onNewJob={openSubmit}
      onManageBackends={openProfiles}
    />
  </aside>

  <section class="details-area">
    <JobDetails />
  </section>
</div>

<!-- Modals are rendered outside the grid so they can cover the full viewport -->
{#if showSubmitModal}
  <JobSubmitModal
    {profiles}
    onClose={closeSubmit}
    onManageBackends={openProfilesFromSubmit}
  />
{/if}

{#if showProfileModal}
  <ProfileManagerModal
    {profiles}
    onClose={closeProfiles}
    onProfilesChange={handleProfilesChange}
  />
{/if}

<style>
  .job-scheduler {
    display: grid;
    grid-template-columns: 300px 1fr;
    height: 100%;
    overflow: hidden;
  }

  .left-panel {
    border-right: 1px solid var(--color-border);
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  .details-area {
    overflow: hidden;
    background: var(--color-bg-deep);
  }
</style>