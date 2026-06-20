<script lang="ts">
  // Viewport3D — renders the SceneResponse from the backend using Three.js.
  //
  // Coordinate convention: the backend speaks OpenMC coordinates (Z-up).
  // Three.js is Y-up by convention. We convert once, here, at the boundary —
  // nowhere else in the frontend should need to think about this.
  //   OpenMC (x, y, z)  ->  Three.js (x, z, y)

  import { onMount, onDestroy } from 'svelte';
  import * as THREE from 'three';
  import type { SceneResponse } from '$lib/types';
  import { isVisible, visibility } from '$lib/stores/index.svelte';

  let { scene: sceneData, isStale = false }: { scene: SceneResponse | null; isStale?: boolean } = $props();

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
  // Scene building — rebuilds all meshes from sceneData whenever it changes
  // ---------------------------------------------------------------------

  let sceneGroup: THREE.Group;
  let hasFramedOnce = false;

  function rebuildScene() {
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
      // Visibility is tracked per base placement name (lattice index
      // suffix stripped) so toggling one entry in the Objects panel
      // hides every instance from that lattice, not just one pin.
      const baseName = comp.name.replace(/_\d+$/, '');
      if (!isVisible(baseName)) continue;

      if (comp.type === 'FuelPin') {
        for (const layer of comp.layers) {
          buildCylinderLayer(comp.position, layer);
        }
      } else if (comp.type === 'Box' && comp.box) {
        buildWireframeBox(comp.position, comp.box);
      }
    }

    // Auto-frame the camera only the first time geometry appears.
    // Re-framing on every edit was overwriting the user's orbit/zoom —
    // it looked like the viewport couldn't be moved at all.
    if (!hasFramedOnce) {
      resetCamera();
      hasFramedOnce = true;
    }
  }

  function buildCylinderLayer(
    position: [number, number, number],
    layer: SceneResponse['components'][number]['layers'][number],
  ) {
    // Hollow layers (r_inner > 0) are rendered as a thin tube approximation
    // using a ring geometry extruded — simplest correct approach is two
    // concentric cylinders with the inner one subtracted visually via
    // back-face culling isn't trivial in Three.js without CSG, so for v1
    // we render solid cylinders from the OUTSIDE in, with the smallest
    // (innermost) layer drawn last so it visually sits "inside" — this
    // is a reasonable approximation since layers are concentric and the
    // pellet is opaque while gap/clad are translucent.
    const geo = new THREE.CylinderGeometry(
      layer.r_outer, layer.r_outer, layer.height, 48, 1, false,
    );
    const mat = new THREE.MeshStandardMaterial({
      color: layer.color,
      transparent: layer.opacity < 1.0,
      opacity: layer.opacity,
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
  }

  function buildWireframeBox(
    position: [number, number, number],
    box: SceneResponse['components'][number]['box'],
  ) {
    if (!box) return;

    const geo = new THREE.BoxGeometry(box.x_size, box.z_size, box.y_size);
    const edges = new THREE.EdgesGeometry(geo);
    const dashed = box.boundary_type !== 'reflective';

    const lineMat = dashed
      ? new THREE.LineDashedMaterial({ color: box.color, dashSize: 0.1, gapSize: 0.06 })
      : new THREE.LineBasicMaterial({ color: box.color });

    const line = new THREE.LineSegments(edges, lineMat);
    if (dashed) line.computeLineDistances();

    const center = toThree(position[0], position[1], box.z_base + box.z_size / 2);
    line.position.copy(center);
    sceneGroup.add(line);

    // Translucent fill volume so the coolant/moderator region reads visually
    if (box.fill_opacity > 0) {
      const fillMat = new THREE.MeshStandardMaterial({
        color: box.fill_color,
        transparent: true,
        opacity: box.fill_opacity * 0.5,
        side: THREE.BackSide,
      });
      const fillMesh = new THREE.Mesh(geo, fillMat);
      fillMesh.position.copy(center);
      sceneGroup.add(fillMesh);
    }

    geo.dispose();
  }

  // Re-run whenever sceneData OR visibility toggles change.
  // Reading `visibility` here registers it as a dependency of this effect.
  $effect(() => {
    sceneData;
    visibility;
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

    // Grid helper on the XY (OpenMC) ground plane — Three.js XZ plane
    const grid = new THREE.GridHelper(20, 20, 0x334155, 0x1e293b);
    scene.add(grid);

    // Axis helper for orientation
    const axes = new THREE.AxesHelper(2);
    scene.add(axes);

    camera = new THREE.PerspectiveCamera(50, 1, 0.01, 2000);
    updateCameraPosition();

    renderer = new THREE.WebGLRenderer({ canvas: canvasEl, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

    // Lighting
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

  <!-- Overlay controls -->
  <div class="viewport-overlay">
    <button class="viewport-btn" onclick={resetCamera} title="Reset camera">
      <svg viewBox="0 0 16 16" fill="currentColor">
        <path d="M8 3a5 5 0 104.546 2.914.5.5 0 00-.908.418A4 4 0 118 4v1.5a.5.5 0 00.854.354l2-2a.5.5 0 000-.708l-2-2A.5.5 0 008 1.5V3z"/>
      </svg>
      Reset view
    </button>
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