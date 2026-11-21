// Pure client-side app (WebGL viewport, localStorage-style state): no SSR.
// Required for adapter-static's SPA fallback — see vite.config.ts.
export const ssr = false;
export const prerender = false;
