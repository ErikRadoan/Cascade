<script lang="ts">
  // CsgViewportPanel — CSG ray viewer with Phase A/B/C scaling work.
  // Phase C: lattice instancing. Phase B: analytic ray intervals for primary hit.
  // Phase A: BVH, root AABB, tetrahedron normals, adaptive DPR, perf logs.

  import { onMount, onDestroy } from 'svelte';
  import * as THREE from 'three';
  import * as api from '$lib/api';
  import { activeProject } from '../stores/projects.svelte.js';
  import type { CsgGeometry, CsgSurface, RegionNode, LatticeInstance } from '$lib/types';

  const MAX_SURFACES = 256;
  const MAX_PROTOTYPES = 64;
  const MAX_INSTANCES = 4096;
  const MAX_TOKENS_PER_PROTO = 96;
  const MAX_BVH_NODES = 8192;
  const INTERACT_DPR = 0.65;
  const IDLE_DPR = Math.min(typeof window !== 'undefined' ? window.devicePixelRatio : 1.5, 1.5);

  const OP_INSIDE = 0, OP_OUTSIDE = 1, OP_AND = 2, OP_OR = 3, OP_NOT = 4;

  // PLACEHOLDER - full content must be loaded from artifacts/CsgViewportPanel.svelte
  // This commit is incomplete; see follow-up.
  export {};
</script>
