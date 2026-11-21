import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},

			// STATIC SPA build (`npm run build` -> frontend/build/). Used by the standalone
			// app: the backend serves these files itself. `fallback` makes every unknown
			// path serve index.html (client-side routing). `vite dev` is unaffected.
			// Requires src/routes/+layout.ts to set ssr = false.
			adapter: adapter({ fallback: 'index.html' })
		})
	]
});
