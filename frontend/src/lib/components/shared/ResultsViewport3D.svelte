<script lang="ts">
  // ResultsViewport3D — renders a job's geometry with a scalar value
  // overlaid per cell (fission, flux, temperature, ...).
  //
  // Deliberately a separate component from Viewport3D (the geometry
  // editor's viewport), not a variant of it. Viewport3D's job is "show the
  // geometry the user is authoring" and should never grow result-specific
  // concerns. This component's job is "show a scalar field on top of a
  // completed job's geometry" and is meant to be reused across every
  // result panel (fission distribution, flux map, temperature map, ...) —
  // each panel just computes its own `cellColors` map and passes it in.
  // Nothing in here knows what the values mean.
  //
  // Visibility toggling is LOCAL to each instance (own $state, not the
  // editor's shared `visibility` store from $lib/stores) — a results page
  // can have several of these mounted at once (fission/flux/heating), and
  // they shouldn't drive each other, let alone the geometry editor.
  //
  // Coordinate convention: same as Viewport3D — backend speaks OpenMC
  // coordinates (Z-up), Three.js is Y-up. Converted once, here.
  //   OpenMC (x, y, z)  ->  Three.js (x, z, y)
  //
  // rel_err flagging: per results-dashboard-spec.md §3, rel_err must never
  // be hidden and every overlay mode needs the same visual convention for
  // "this value's uncertainty is above threshold". A hatch texture doesn't
  // read well on a lit 3D cylinder at orbit distance, so the 3D encoding is
  // a warning-colored dashed outline + a further opacity cut on top of the
  // normal overlay opacity — same semantic as the hatch/opacity treatment
  // panels use in 2D, just the 3D-appropriate version of it. Callers pass
  // cell_name -> boolean same as cellColors; nothing in here decides the
  // 10% threshold, that's shared/ColorMap.ts's isFlagged().

  import { onMount, onDestroy } from 'svelte';
  import * as THREE from 'three';
  import type { SceneResponse } from '$lib/types';

  let {
    scene: sceneData,
    cellColors = null,
    cellFlags = null,
    isStale = false,
  }: {
    scene: SceneResponse | null;
    // Maps a layer/box's `cell_name` (CylinderLayer.cell_name /
    // WireframeBox.cell_name — see schemas.py) to a CSS color string.
    // A cell with no entry falls back to its normal material color, so a
    // partial map (e.g. only fissile cells) still renders sensibly.
    cellColors?: Record<string, string> | null;
    // Maps cell_name -> "this cell's rel_err is above the shared warning
    // threshold". Optional — panels that don't carry uncertainty (or Panel
    // A/D, which don't use this component) simply omit it.
    cellFlags?: Record<string, boolean> | null;
    isStale?: boolean;
  } = $props();

  // Colored cells stay somewhat translucent rather than forcing full
  // opacity — being able to see partway through outer pins to the ones
  // behind is what makes a "distribution" actually read as one in 3D,
  // rather than just repainting the front layer solid.
  const OVERLAY_LAYER_OPACITY = 0.82;
  const OVERLAY_BOX_OPACITY   = 0.35;

  // When cellColors is set at all, the view has committed to "showing an
  // overlay" — a cell with no entry in that map (no tally data for it)
  // should recede into a neutral gray rather than show its normal
  // material color. Left as material colors, gap/clad/water would
  // visually compete with the heat map instead of reading as "no data
  // here" — same base color used regardless of what the underlying
  // material actually is, so it doesn't get mistaken for a real value.
  const NO_DATA_COLOR   = '#454b57';
  const NO_DATA_OPACITY = 0.28;

  // rel_err flag treatment — same warning red as .error-badge/.flag-swatch
  // elsewhere in the app, so "flagged" means one thing across the UI.
  const FLAG_COLOR              = '#ef4444';
  const FLAGGED_OPACITY_FACTOR  = 0.55; // multiplies whatever opacity was already chosen

  let canvasEl: HTMLCanvasElement;
  let containerEl: HTMLDivElement;

  let renderer: THREE.WebGLRenderer;
  let scene: THREE.Scene;
  let camera: THREE.PerspectiveCamera;
  let animationId: number;

  // Simple orbit-style camera control state (no external dependency)
  let isDragging = $state(false);
  let lastX = 0;
  let lastY = 0;
  let cameraDistance = $state(8);
  let cameraTheta = $state(Math.PI / 4);   // horizontal angle
  let cameraPhi   = $state(Math.PI / 3);   // vertical angle
  let cameraTarget = new THREE.Vector3(0, 0, 0);

  // ── Per-instance object visibility (own state, own menu) ────────────────

  let visibleObjects = $state<Record<string, boolean>>({});
  let menuOpen = $state(false);

  // Groups lattice instances under one toggle, same convention Viewport3D
  // uses ("pin_0", "pin_1", ... -> "pin").
  function baseName(name: string): string {
    return name.replace(/_\d+$/, '');
  }

  function objectNames(data: SceneResponse | null): string[] {
    if (!data) return [];
    const seen = new Set<string>();
    for (const comp of data.components) seen.add(baseName(comp.name));
    return [...seen].sort();
  }

  const objectList = $derived(objectNames(sceneData));

  function isObjectVisible(name: string): boolean {
    return visibleObjects[baseName(name)] !== false;
  }

  function toggleObject(name: string) {
    const key = baseName(name);
    visibleObjects = { ...visibleObjects, [key]: !isObjectVisible(key) };
  }

  // New scenes may introduce object names this instance hasn't seen yet
  // (e.g. scene arrives after an initial null render) — default any new
  // name to visible without clobbering toggles the user already made.
  $effect(() => {
    for (const name of objectList) {
      if (!(name in visibleObjects)) {
        visibleObjects[name] = true;
      }
    }
  });

  // OpenMC (x, y, z) -> Three.js (x, z, y)
  function toThree(x: number, y: number, z: number): THREE.Vector3 {
    return new THREE.Vector3(x, z, y);
  }

  function updateCameraPosition() {
    const x = cameraTarget.x + cameraDistance * Math.sin(cameraPhi) * Math.cos(cameraTheta);
    const y = cameraTarget.y + cameraDistance * Math.cos(cameraPhi);
    const z = cameraTarget.z + cameraDistance * Math.sin(cameraPhi) * Math.sin(cameraTheta);
    camera.position.set(x, y, z);
    camera.lookAt(cameraTarget);
  }

  function onPointerDown(e: PointerEvent) {
    isDragging = true;
    lastX = e.clientX;
    lastY = e.clientY;
    canvasEl.setPointerCapture(e.pointerId);
  }

  function onPointerMove(e: PointerEvent) {
    if (!isDragging) return;
    const dx = e.clientX - lastX;
    const dy = e.clientY - lastY;
    lastX = e.clientX;
    lastY = e.clientY;

    cameraTheta -= dx * 0.005;
    cameraPhi    = Math.max(0.1, Math.min(Math.PI - 0.1, cameraPhi - dy * 0.005));
    updateCameraPosition();
  }

  function onPointerUp(e: PointerEvent) {
    isDragging = false;
    canvasEl.releasePointerCapture(e.pointerId);
  }

  function onWheel(e: WheelEvent) {
    e.preventDefault();
    cameraDistance = Math.max(0.5, Math.min(200, cameraDistance * (1 + e.deltaY * 0.001)));
    updateCameraPosition();
  }

  function resetCamera() {
    cameraTheta = Math.PI / 4;
    cameraPhi   = Math.PI / 3;
    if (sceneData) {
      const b = sceneData.bounds;
      const span = Math.max(b.x_max - b.x_min, b.y_max - b.y_min, b.z_max - b.z_min, 1);
      cameraDistance = span * 1.8;
      cameraTarget = toThree(
        (b.x_min + b.x_max) / 2,
        (b.y_min + b.y_max) / 2,
        (b.z_min + b.z_max) / 2,
      );
    } else {
      cameraDistance = 8;
      cameraTarget = new THREE.Vector3(0, 0, 0);
    }
    updateCameraPosition();
  }

  // ---------------------------------------------------------------------
  // Scene building — rebuilds all meshes from sceneData/cellColors/
  // visibility whenever any of them change
  // ---------------------------------------------------------------------

  let sceneGroup: THREE.Group;
  let hasFramedOnce = false;

  function rebuildScene() {
    console.group("=== ResultsViewport3D rebuildScene ===");

    console.log("sceneData:", sceneData);
    console.log("cellColors:", cellColors);
    console.log("cellFlags:", cellFlags);

    if (cellColors) {
      console.log("cellColors keys:", Object.keys(cellColors));
    }
    // Clear previous geometry
    while (sceneGroup.children.length > 0) {
      const child = sceneGroup.children[0];
      sceneGroup.remove(child);
      if (child instanceof THREE.Mesh || child instanceof THREE.LineSegments) {
        child.geometry.dispose();
        if (Array.isArray(child.material)) {
          child.material.forEach(m => m.dispose());
        } else {
          child.material.dispose();
        }
      }
    }

    if (!sceneData) return;

    for (const comp of sceneData.components) {
      if (!isObjectVisible(comp.name)) continue;

      if (comp.type === 'FuelPin') {
        for (const layer of comp.layers) {
          buildCylinderLayer(comp.position, layer);
        }
      } else if (comp.type === 'Box' && comp.box) {
        buildWireframeBox(comp.position, comp.box);
      }
    }

    // Auto-frame the camera only the first time geometry appears, same
    // rationale as Viewport3D: re-framing on every update would fight the
    // user's orbit/zoom.
    if (!hasFramedOnce) {
      resetCamera();
      hasFramedOnce = true;
    }
    console.groupEnd();
  }

  function buildCylinderLayer(
    position: [number, number, number],
    layer: SceneResponse['components'][number]['layers'][number],
  ) {
    const geo = new THREE.CylinderGeometry(
      layer.r_outer, layer.r_outer, layer.height, 48, 1, false,
    );
    const overrideColor = cellColors?.[layer.cell_name];

    console.log("Cylinder layer");
    console.log({
        cell_name: layer.cell_name,
        overrideColor,
        hasEntry: cellColors ? layer.cell_name in cellColors : false,
        baseColor: layer.color,
        finalColor: overrideColor ?? (cellColors ? NO_DATA_COLOR : layer.color),
    });

    const overlayActive = cellColors != null;
    const flagged = cellFlags?.[layer.cell_name] === true;
    const baseOpacity = overrideColor
      ? OVERLAY_LAYER_OPACITY
      : (overlayActive ? NO_DATA_OPACITY : layer.opacity);
    const mat = new THREE.MeshStandardMaterial({
      color: overrideColor ?? (overlayActive ? NO_DATA_COLOR : layer.color),
      transparent: true,
      opacity: flagged ? baseOpacity * FLAGGED_OPACITY_FACTOR : baseOpacity,
      side: THREE.DoubleSide,
      roughness: 0.6,
      metalness: 0.1,
    });
    const mesh = new THREE.Mesh(geo, mat);

    // Position: OpenMC z_base is bottom of cylinder; Three.js cylinder
    // origin is its vertical centre, so offset by height/2.
    const center = toThree(position[0], position[1], layer.z_base + layer.height / 2);
    mesh.position.copy(center);

    sceneGroup.add(mesh);

    if (flagged) {
      const edges = new THREE.EdgesGeometry(geo);
      const outline = new THREE.LineSegments(
        edges,
        new THREE.LineDashedMaterial({ color: FLAG_COLOR, dashSize: 0.06, gapSize: 0.04 }),
      );
      outline.computeLineDistances();
      outline.position.copy(center);
      sceneGroup.add(outline);
    }
  }

  function buildWireframeBox(
    position: [number, number, number],
    box: SceneResponse['components'][number]['box'],
  ) {
    if (!box) return;

    const geo = new THREE.BoxGeometry(box.x_size, box.z_size, box.y_size);
    const edges = new THREE.EdgesGeometry(geo);
    const flagged = cellFlags?.[box.cell_name] === true;
    const dashed = box.boundary_type !== 'reflective' || flagged;

    const lineMat = dashed
      ? new THREE.LineDashedMaterial({ color: flagged ? FLAG_COLOR : box.color, dashSize: 0.1, gapSize: 0.06 })
      : new THREE.LineBasicMaterial({ color: box.color });

    const line = new THREE.LineSegments(edges, lineMat);
    if (dashed) line.computeLineDistances();

    const center = toThree(position[0], position[1], box.z_base + box.z_size / 2);
    line.position.copy(center);
    sceneGroup.add(line);

    const overrideColor = cellColors?.[box.cell_name];
    const overlayActive = cellColors != null;
    if (box.fill_opacity > 0 || overrideColor || overlayActive) {
      const baseOpacity = overrideColor
        ? OVERLAY_BOX_OPACITY
        : (overlayActive ? NO_DATA_OPACITY * 0.6 : box.fill_opacity * 0.5);
      const fillMat = new THREE.MeshStandardMaterial({
        color: overrideColor ?? (overlayActive ? NO_DATA_COLOR : box.fill_color),
        transparent: true,
        opacity: flagged ? baseOpacity * FLAGGED_OPACITY_FACTOR : baseOpacity,
        side: THREE.BackSide,
      });
      const fillMesh = new THREE.Mesh(geo, fillMat);
      fillMesh.position.copy(center);
      sceneGroup.add(fillMesh);
    }

    geo.dispose();
  }

  // Re-run whenever sceneData, the color overlay, or visibility changes.
  // Reading `visibleObjects` here registers it as a dependency.
  $effect(() => {
    sceneData;
    cellColors;
    cellFlags;
    visibleObjects;
    if (sceneGroup) rebuildScene();
  });

  // ---------------------------------------------------------------------
  // Setup / teardown
  // ---------------------------------------------------------------------

  function resize() {
    if (!containerEl || !renderer || !camera) return;
    const w = containerEl.clientWidth;
    const h = containerEl.clientHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }

  onMount(() => {
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0f172a);

    sceneGroup = new THREE.Group();
    scene.add(sceneGroup);

    const grid = new THREE.GridHelper(20, 20, 0x334155, 0x1e293b);
    scene.add(grid);

    const axes = new THREE.AxesHelper(2);
    scene.add(axes);

    camera = new THREE.PerspectiveCamera(50, 1, 0.01, 2000);
    updateCameraPosition();

    renderer = new THREE.WebGLRenderer({ canvas: canvasEl, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

    const ambient = new THREE.AmbientLight(0xffffff, 0.6);
    scene.add(ambient);
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
    dirLight.position.set(5, 10, 7);
    scene.add(dirLight);
    const dirLight2 = new THREE.DirectionalLight(0xffffff, 0.3);
    dirLight2.position.set(-5, -5, -5);
    scene.add(dirLight2);

    resize();
    rebuildScene();

    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(containerEl);

    function animate() {
      animationId = requestAnimationFrame(animate);
      renderer.render(scene, camera);
    }
    animate();

    return () => {
      resizeObserver.disconnect();
    };
  });

  onDestroy(() => {
    if (animationId) cancelAnimationFrame(animationId);
    renderer?.dispose();
  });
</script>

<div class="viewport-container" bind:this={containerEl}>
  <canvas
    bind:this={canvasEl}
    onpointerdown={onPointerDown}
    onpointermove={onPointerMove}
    onpointerup={onPointerUp}
    onwheel={onWheel}
  ></canvas>

  <div class="viewport-overlay">
    <button class="viewport-btn" onclick={resetCamera} title="Reset camera">
      <svg viewBox="0 0 16 16" fill="currentColor">
        <path d="M8 3a5 5 0 104.546 2.914.5.5 0 00-.908.418A4 4 0 118 4v1.5a.5.5 0 00.854.354l2-2a.5.5 0 000-.708l-2-2A.5.5 0 008 1.5V3z"/>
      </svg>
      Reset view
    </button>

    {#if objectList.length > 0}
      <div class="objects-wrap">
        <button
          class="viewport-btn"
          onclick={() => (menuOpen = !menuOpen)}
          title="Show/hide objects"
        >
          <svg viewBox="0 0 16 16" fill="currentColor">
            <path d="M2 4h12v1.5H2V4zm0 3.25h12v1.5H2v-1.5zM2 10.5h12V12H2v-1.5z"/>
          </svg>
          Objects
        </button>

        {#if menuOpen}
          <div class="objects-menu">
            {#each objectList as name (name)}
              <label class="objects-row">
                <input
                  type="checkbox"
                  checked={isObjectVisible(name)}
                  onchange={() => toggleObject(name)}
                />
                {name}
              </label>
            {/each}
          </div>
        {/if}
      </div>
    {/if}
  </div>

  {#if isStale}
    <div class="stale-badge">Updating…</div>
  {/if}

  {#if sceneData?.error}
    <div class="error-badge">{sceneData.error}</div>
  {/if}
</div>

<style>
  .viewport-container {
    position: relative;
    width: 100%;
    height: 100%;
  }

  canvas {
    display: block;
    width: 100%;
    height: 100%;
    cursor: grab;
    touch-action: none;
  }

  canvas:active {
    cursor: grabbing;
  }

  .viewport-overlay {
    position: absolute;
    bottom: 12px;
    left: 12px;
    display: flex;
    gap: 6px;
  }

  .objects-wrap {
    position: relative;
  }

  .viewport-btn {
    display: flex;
    align-items: center;
    gap: 5px;
    background: var(--color-bg-panel);
    border: 1px solid var(--color-border);
    color: var(--color-subtext);
    font-size: 11px;
    padding: 5px 9px;
    border-radius: 6px;
    cursor: pointer;
  }

  .viewport-btn svg {
    width: 13px;
    height: 13px;
  }

  .viewport-btn:hover {
    color: var(--color-text);
    border-color: var(--color-accent);
  }

  .objects-menu {
    position: absolute;
    bottom: calc(100% + 6px);
    left: 0;
    min-width: 140px;
    max-height: 220px;
    overflow-y: auto;
    background: var(--color-bg-panel);
    border: 1px solid var(--color-border);
    border-radius: 6px;
    padding: 6px;
    display: flex;
    flex-direction: column;
    gap: 3px;
    z-index: 5;
  }

  .objects-row {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    color: var(--color-subtext);
    padding: 3px 4px;
    border-radius: 4px;
    cursor: pointer;
    white-space: nowrap;
  }

  .objects-row:hover {
    color: var(--color-text);
    background: rgba(255, 255, 255, 0.04);
  }

  .objects-row input {
    cursor: pointer;
  }

  .stale-badge,
  .error-badge {
    position: absolute;
    top: 12px;
    right: 12px;
    font-size: 11px;
    padding: 5px 10px;
    border-radius: 6px;
  }

  .stale-badge {
    background: rgba(6, 182, 212, 0.15);
    color: var(--color-accent-hi);
    border: 1px solid var(--color-accent);
  }

  .error-badge {
    background: rgba(239, 68, 68, 0.15);
    color: #f87171;
    border: 1px solid #ef4444;
    max-width: 320px;
  }
</style>