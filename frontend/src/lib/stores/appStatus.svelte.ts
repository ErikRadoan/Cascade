// appStatus.svelte.ts — app startup state machine behind the full-screen splash.
//
//   connecting  ->  restoring  ->  ready
//
// connecting: poll the backend's /health until it answers (backoff, never gives up
//             on its own — the backend is often still booting when the dev server
//             is already up). After SKIP_AFTER_MS an escape hatch appears so a
//             genuinely-offline session isn't trapped on the splash.
// restoring:  load the saved geometries as tabs (restoreProjects) and wait for the
//             ACTIVE one's scene, so the first thing the user sees is a finished
//             viewport instead of an empty one that pops in.
// ready:      splash fades out and the real UI mounts (see routes/+layout.svelte).
//
// The UI is intentionally NOT mounted until `ready`. GeometryEditor used to call
// restoreProjects() itself while its $effect simultaneously validated the blank
// default tab; gating the whole shell removes that race.

import * as api from '$lib/api';
import { restoreProjects } from '$lib/components/geometry/stores/projects.svelte';

export type AppPhase = 'connecting' | 'restoring' | 'ready';

export const app = $state({
  phase: 'connecting' as AppPhase,
  message: 'Connecting to backend…',
  detail: '',
  progress: null as number | null,   // 0..1 when known, null = indeterminate
  attempts: 0,
  canSkip: false,
});

const SKIP_AFTER_MS = 8000;   // show "continue without backend" after this long
const MIN_SPLASH_MS = 600;    // never flash the splash for a few ms on a fast load
const sleep = (ms: number) => new Promise<void>(r => setTimeout(r, ms));

let started = false;
let skipped = false;

/** Escape hatch from the splash: continue with the default blank tab, backend or not. */
export function skipWaiting() { skipped = true; }

export async function bootstrap(): Promise<void> {
  if (started) return;        // layout can re-mount (HMR); only ever bootstrap once
  started = true;
  const t0 = performance.now();

  try {
    // ---- 1. wait for the backend -------------------------------------------
    while (!skipped) {
      if (await api.system.health()) break;
      app.attempts++;
      if (performance.now() - t0 > SKIP_AFTER_MS) {
        app.canSkip = true;
        app.message = 'Still waiting for the backend…';
        app.detail = 'Is it running? Start it with: uv run uvicorn cascade.main:app';
      }
      await sleep(Math.min(300 * 1.6 ** app.attempts, 2000));
    }

    // ---- 2. restore saved geometries ---------------------------------------
    if (!skipped) {
      app.phase = 'restoring';
      app.message = 'Loading your geometries…';
      app.detail = '';
      app.canSkip = false;
      await restoreProjects({
        onProgress: (done, total) => {
          app.progress = total > 0 ? done / total : null;
          app.detail = total > 0 ? `${done} of ${total} loaded` : '';
          if (done >= total) { app.message = 'Building scene…'; app.progress = null; app.detail = ''; }
        },
      });
    }
  } finally {
    // Whatever happened, never leave the user stuck on the splash.
    const elapsed = performance.now() - t0;
    if (elapsed < MIN_SPLASH_MS) await sleep(MIN_SPLASH_MS - elapsed);
    app.phase = 'ready';
  }
}
