// jobs.svelte.ts — job list state: fetching, polling, and the
// selected/selected-for-results job ids. Moved out of the old
// lib/stores/index.svelte.ts as-is — nothing about the logic changed.

import type { JobSummary } from '$lib/types';
import * as api from '$lib/api';

export const jobsState = $state({
  list:                [] as JobSummary[],
  isLoading:           false,
  error:               null as string | null,
  selectedJobId:       null as string | null,
  // Set by JobDetails "View Results →" button, read by ResultsViewer
  selectedResultJobId: null as string | null,
});

let jobsPoller: ReturnType<typeof setInterval> | null = null;

export async function refreshJobs() {
  jobsState.isLoading = true;
  jobsState.error = null;

  try {
    const fresh = await api.jobs.list();

    // Build a map of the incoming data
    const freshMap = new Map(fresh.map(j => [j.id, j]));

    // Update existing entries in-place (preserves object identity for $derived)
    for (const existing of jobsState.list) {
      const updated = freshMap.get(existing.id);
      if (updated) {
        existing.status      = updated.status;
        existing.notes       = updated.notes;
        existing.backend     = updated.backend;
        existing.param_values = updated.param_values;
      }
    }

    // Add any jobs that aren't in the list yet (prepend, newest first)
    const existingIds = new Set(jobsState.list.map(j => j.id));
    const newJobs = fresh.filter(j => !existingIds.has(j.id));
    if (newJobs.length > 0) {
      jobsState.list.unshift(...newJobs);
    }

    // Remove jobs that no longer exist on the backend
    const toRemove = jobsState.list.filter(j => !freshMap.has(j.id));
    for (const gone of toRemove) {
      const idx = jobsState.list.indexOf(gone);
      if (idx !== -1) jobsState.list.splice(idx, 1);
    }

  } catch (e) {
    jobsState.error = e instanceof Error ? e.message : 'Failed to load jobs';
  } finally {
    jobsState.isLoading = false;
  }
}

export function startJobsPolling(interval = 5000) {
  if (jobsPoller) return;

  refreshJobs();

  jobsPoller = setInterval(() => {
    refreshJobs();
  }, interval);
}

export function stopJobsPolling() {
  if (!jobsPoller) return;

  clearInterval(jobsPoller);
  jobsPoller = null;
}
